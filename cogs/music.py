import asyncio
import random
import re

import aiohttp
import discord
from discord.ext import commands
import yt_dlp

SPOTIFY_REGEX = re.compile(
    r"https?://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
)

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "auto",
}

YTDL_PLAYLIST_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "extract_flat": True,
    "quiet": True,
    "no_warnings": True,
}

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, list[dict]] = {}      # guild_id -> list of song dicts
        self.now_playing: dict[int, dict | None] = {} # guild_id -> current song dict
        self.volumes: dict[int, float] = {}           # guild_id -> volume (0.0–1.0)
        self.loop_mode: dict[int, str] = {}           # guild_id -> "off" | "track" | "queue"

    # ── helpers ──────────────────────────────────────────────

    def _get_queue(self, guild_id: int) -> list[dict]:
        return self.queues.setdefault(guild_id, [])

    async def _resolve_spotify(self, url: str) -> list[str]:
        """Convert Spotify URLs to YouTube search queries via oEmbed."""
        match = SPOTIFY_REGEX.match(url)
        if not match:
            return [url]

        kind = match.group(1)

        if kind == "track":
            oembed_url = f"https://open.spotify.com/oembed?url={url}"
            async with aiohttp.ClientSession() as session:
                async with session.get(oembed_url) as resp:
                    if resp.status != 200:
                        raise commands.CommandError("Could not fetch Spotify track info.")
                    data = await resp.json()
            # oEmbed title is "track name - artist"
            return [f"ytsearch:{data['title']}"]

        # For albums/playlists, we'd need the full Spotify API.
        raise commands.CommandError(
            "Only Spotify track links are supported. "
            "Album and playlist links require Spotify API credentials."
        )

    async def _extract_playlist(self, url: str) -> list[dict]:
        """Extract all entries from a playlist URL. Returns a list of minimal dicts."""
        ytdl = yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTIONS)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=False))

        if "entries" not in data:
            return []

        entries = []
        for entry in data["entries"]:
            if entry is None:
                continue
            entries.append({
                "title": entry.get("title", "Unknown"),
                "url": entry.get("url", entry.get("webpage_url", "")),
                "webpage_url": entry.get("webpage_url", entry.get("url", "")),
                "duration": entry.get("duration", 0),
            })
        return entries

    @staticmethod
    def _is_playlist_url(query: str) -> bool:
        """Check if a query looks like a playlist URL."""
        if "list=" in query:
            return True
        if "soundcloud.com" in query and "/sets/" in query:
            return True
        if "bandcamp.com" in query and "/album/" in query:
            return True
        return False

    async def _extract_info(self, query: str) -> dict:
        """Run yt-dlp extraction in a thread so we don't block the event loop."""
        # Resolve Spotify URLs to a YouTube search query
        if SPOTIFY_REGEX.match(query):
            queries = await self._resolve_spotify(query)
            query = queries[0]

        ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))

        # If a search returned a playlist of results, take the first one
        if "entries" in data:
            data = data["entries"][0]

        return {
            "title": data.get("title", "Unknown"),
            "url": data["url"],                       # direct audio stream URL
            "webpage_url": data.get("webpage_url", query),
            "duration": data.get("duration", 0),
        }

    def _play_next(self, guild: discord.Guild):
        """Callback: when a track ends, play the next one in queue."""
        # Schedule the async version from the callback thread
        asyncio.run_coroutine_threadsafe(self._play_next_async(guild), self.bot.loop)

    async def _play_next_async(self, guild: discord.Guild):
        """Async handler for advancing to the next track."""
        mode = self.loop_mode.get(guild.id, "off")
        current = self.now_playing.get(guild.id)
        queue = self._get_queue(guild.id)

        if mode == "track" and current:
            song = current
        elif mode == "queue" and current:
            queue.append(current)
            song = queue.pop(0)
        elif queue:
            song = queue.pop(0)
        else:
            self.now_playing[guild.id] = None
            if guild.voice_client:
                await guild.voice_client.disconnect()
            return

        # Resolve stream URL for flat-extracted playlist entries
        if not song["url"].startswith("http") or "manifest" not in song["url"] and "googlevideo" not in song["url"]:
            try:
                resolved = await self._extract_info(song.get("webpage_url") or song["url"])
                song.update(resolved)
            except Exception:
                # Skip unresolvable tracks
                await self._play_next_async(guild)
                return

        self.now_playing[guild.id] = song
        source = discord.FFmpegPCMAudio(song["url"], before_options=FFMPEG_BEFORE_OPTS, options=FFMPEG_OPTS)
        source = discord.PCMVolumeTransformer(source, volume=self.volumes.get(guild.id, 0.5))
        guild.voice_client.play(source, after=lambda e: self._play_next(guild) if not e else print(f"Player error: {e}"))

    @staticmethod
    def _format_duration(seconds: int | float) -> str:
        if not seconds:
            return "Live / Unknown"
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # ── commands ─────────────────────────────────────────────

    @commands.command()
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a URL or search term. Supports playlists."""
        if not ctx.author.voice:
            return await ctx.send("You need to be in a voice channel.")

        channel = ctx.author.voice.channel

        # Connect if not already in voice
        if ctx.voice_client is None:
            await channel.connect()
        elif ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)

        async with ctx.typing():
            # Handle playlist URLs
            if self._is_playlist_url(query):
                try:
                    entries = await self._extract_playlist(query)
                except Exception as e:
                    return await ctx.send(f"Failed to extract playlist: {e}")

                if not entries:
                    return await ctx.send("No tracks found in that playlist.")

                queue = self._get_queue(ctx.guild.id)
                queue.extend(entries)
                await ctx.send(f"Added **{len(entries)}** tracks from playlist to the queue.")

                # Start playing if nothing is currently playing
                if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                    song = queue.pop(0)
                    self.now_playing[ctx.guild.id] = song
                    # Resolve the full stream URL for flat-extracted entries
                    try:
                        resolved = await self._extract_info(song["url"])
                        song.update(resolved)
                    except Exception:
                        pass
                    source = discord.FFmpegPCMAudio(song["url"], before_options=FFMPEG_BEFORE_OPTS, options=FFMPEG_OPTS)
                    source = discord.PCMVolumeTransformer(source, volume=self.volumes.get(ctx.guild.id, 0.5))
                    ctx.voice_client.play(source, after=lambda e: self._play_next(ctx.guild) if not e else print(f"Player error: {e}"))
                    await ctx.send(
                        f"Now playing: **{song['title']}** "
                        f"[{self._format_duration(song['duration'])}]"
                    )
                return

            # Single track
            try:
                song = await self._extract_info(query)
            except Exception as e:
                return await ctx.send(f"Failed to extract audio: {e}")

            queue = self._get_queue(ctx.guild.id)

            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                queue.append(song)
                await ctx.send(
                    f"Added to queue (#{len(queue)}): **{song['title']}** "
                    f"[{self._format_duration(song['duration'])}]"
                )
            else:
                self.now_playing[ctx.guild.id] = song
                source = discord.FFmpegPCMAudio(song["url"], before_options=FFMPEG_BEFORE_OPTS, options=FFMPEG_OPTS)
                source = discord.PCMVolumeTransformer(source, volume=self.volumes.get(ctx.guild.id, 0.5))
                ctx.voice_client.play(source, after=lambda e: self._play_next(ctx.guild) if not e else print(f"Player error: {e}"))
                await ctx.send(
                    f"Now playing: **{song['title']}** "
                    f"[{self._format_duration(song['duration'])}]"
                )

    @commands.command()
    async def pause(self, ctx: commands.Context):
        """Pause the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("Paused.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def resume(self, ctx: commands.Context):
        """Resume the current track."""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("Resumed.")
        else:
            await ctx.send("Nothing is paused.")

    @commands.command()
    async def skip(self, ctx: commands.Context):
        """Skip the current track."""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()  # triggers _play_next via the after callback
            await ctx.send("Skipped.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def stop(self, ctx: commands.Context):
        """Stop playback, clear the queue, and leave the voice channel."""
        self._get_queue(ctx.guild.id).clear()
        self.now_playing[ctx.guild.id] = None
        if ctx.voice_client:
            ctx.voice_client.stop()
            await ctx.voice_client.disconnect()
            await ctx.send("Stopped and disconnected.")
        else:
            await ctx.send("Not connected to voice.")

    @commands.command()
    async def queue(self, ctx: commands.Context):
        """Show the current song queue."""
        current = self.now_playing.get(ctx.guild.id)
        queue = self._get_queue(ctx.guild.id)

        if not current and not queue:
            return await ctx.send("The queue is empty.")

        lines = []
        if current:
            lines.append(
                f"**Now playing:** {current['title']} "
                f"[{self._format_duration(current['duration'])}]"
            )

        for i, song in enumerate(queue, start=1):
            lines.append(
                f"`{i}.` {song['title']} [{self._format_duration(song['duration'])}]"
            )

        if not queue and current:
            lines.append("*No more songs in queue.*")

        await ctx.send("\n".join(lines))

    @commands.command(aliases=["np"])
    async def nowplaying(self, ctx: commands.Context):
        """Show the currently playing track."""
        current = self.now_playing.get(ctx.guild.id)
        if current:
            await ctx.send(
                f"Now playing: **{current['title']}** "
                f"[{self._format_duration(current['duration'])}]\n"
                f"{current['webpage_url']}"
            )
        else:
            await ctx.send("Nothing is playing right now.")

    @commands.command(aliases=["vol"])
    async def volume(self, ctx: commands.Context, level: int = None):
        """Set volume (0–100). Shows current volume if no value given."""
        if level is None:
            current = int(self.volumes.get(ctx.guild.id, 0.5) * 100)
            return await ctx.send(f"Volume: **{current}%**")

        if not 0 <= level <= 100:
            return await ctx.send("Volume must be between 0 and 100.")

        self.volumes[ctx.guild.id] = level / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = level / 100
        await ctx.send(f"Volume set to **{level}%**.")

    @commands.command()
    async def shuffle(self, ctx: commands.Context):
        """Shuffle the current queue."""
        queue = self._get_queue(ctx.guild.id)
        if len(queue) < 2:
            return await ctx.send("Not enough songs in the queue to shuffle.")
        random.shuffle(queue)
        await ctx.send(f"Shuffled **{len(queue)}** songs.")

    @commands.command()
    async def loop(self, ctx: commands.Context, mode: str = None):
        """Toggle loop mode: off, track, or queue."""
        current = self.loop_mode.get(ctx.guild.id, "off")

        if mode is None:
            # Cycle: off -> track -> queue -> off
            cycle = {"off": "track", "track": "queue", "queue": "off"}
            mode = cycle[current]

        mode = mode.lower()
        if mode not in ("off", "track", "queue"):
            return await ctx.send("Valid modes: `off`, `track`, `queue`.")

        self.loop_mode[ctx.guild.id] = mode
        labels = {"off": "Looping disabled.", "track": "Looping current track.", "queue": "Looping entire queue."}
        await ctx.send(labels[mode])

    @commands.command()
    async def seek(self, ctx: commands.Context, timestamp: str):
        """Seek to a position in the current track (e.g. 1:30 or 90)."""
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            return await ctx.send("Nothing is playing.")

        current = self.now_playing.get(ctx.guild.id)
        if not current:
            return await ctx.send("Nothing is playing.")

        # Parse timestamp: supports "1:30", "1:30:00", or just seconds "90"
        parts = timestamp.split(":")
        try:
            parts = [int(p) for p in parts]
        except ValueError:
            return await ctx.send("Invalid timestamp. Use format like `1:30` or `90`.")

        if len(parts) == 1:
            seconds = parts[0]
        elif len(parts) == 2:
            seconds = parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            return await ctx.send("Invalid timestamp format.")

        # Restart the stream with an FFmpeg seek offset
        ctx.voice_client.stop()
        seek_opts = f"{FFMPEG_BEFORE_OPTS} -ss {seconds}"
        source = discord.FFmpegPCMAudio(current["url"], before_options=seek_opts, options=FFMPEG_OPTS)
        source = discord.PCMVolumeTransformer(source, volume=self.volumes.get(ctx.guild.id, 0.5))
        ctx.voice_client.play(source, after=lambda e: self._play_next(ctx.guild) if not e else print(f"Player error: {e}"))
        await ctx.send(f"Seeked to **{timestamp}**.")

    @commands.command()
    async def lyrics(self, ctx: commands.Context, *, query: str = None):
        """Fetch lyrics for the current track or a given search term."""
        if query is None:
            current = self.now_playing.get(ctx.guild.id)
            if not current:
                return await ctx.send("Nothing is playing. Provide a song name to search.")
            query = current["title"]

        async with ctx.typing():
            search_url = f"https://api.lyrics.ovh/v1/{query}"
            # Try splitting "Artist - Title" format
            if " - " in query:
                artist, title = query.split(" - ", 1)
                search_url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
            else:
                # Use artist as empty, title as query
                search_url = f"https://api.lyrics.ovh/v1/_/{query}"

            async with aiohttp.ClientSession() as session:
                async with session.get(search_url) as resp:
                    if resp.status != 200:
                        return await ctx.send(f"Could not find lyrics for **{query}**.")
                    data = await resp.json()

            text = data.get("lyrics")
            if not text:
                return await ctx.send(f"No lyrics found for **{query}**.")

            # Discord message limit is 2000 chars
            if len(text) > 1900:
                text = text[:1900] + "\n..."

            await ctx.send(f"**Lyrics for: {query}**\n{text}")

    @commands.command()
    async def clear(self, ctx: commands.Context):
        """Clear the queue without stopping the current song."""
        queue = self._get_queue(ctx.guild.id)
        count = len(queue)
        queue.clear()
        await ctx.send(f"Cleared **{count}** song(s) from the queue.")

    @commands.command()
    async def remove(self, ctx: commands.Context, index: int):
        """Remove a song from the queue by its position (1-based)."""
        queue = self._get_queue(ctx.guild.id)
        if 1 <= index <= len(queue):
            removed = queue.pop(index - 1)
            await ctx.send(f"Removed **{removed['title']}** from the queue.")
        else:
            await ctx.send(f"Invalid index. Queue has {len(queue)} song(s).")


    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Leave when the bot is alone in a voice channel."""
        if member.bot:
            return

        vc = member.guild.voice_client
        if vc is None:
            return

        # Check if the bot is the only one left in the channel
        if len(vc.channel.members) == 1:
            self._get_queue(member.guild.id).clear()
            self.now_playing[member.guild.id] = None
            vc.stop()
            await vc.disconnect()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
