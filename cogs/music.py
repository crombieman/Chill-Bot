import asyncio
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

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.queues: dict[int, list[dict]] = {}      # guild_id -> list of song dicts
        self.now_playing: dict[int, dict | None] = {} # guild_id -> current song dict

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
        queue = self._get_queue(guild.id)
        if not queue:
            self.now_playing[guild.id] = None
            # Leave voice after the queue is empty
            asyncio.run_coroutine_threadsafe(guild.voice_client.disconnect(), self.bot.loop)
            return

        song = queue.pop(0)
        self.now_playing[guild.id] = song
        source = discord.FFmpegOpusAudio(song["url"], **FFMPEG_OPTIONS)
        guild.voice_client.play(source, after=lambda _: self._play_next(guild))

    @staticmethod
    def _format_duration(seconds: int) -> str:
        if not seconds:
            return "Live / Unknown"
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # ── commands ─────────────────────────────────────────────

    @commands.command()
    async def play(self, ctx: commands.Context, *, query: str):
        """Play a URL or search term. Adds to queue if something is already playing."""
        if not ctx.author.voice:
            return await ctx.send("You need to be in a voice channel.")

        channel = ctx.author.voice.channel

        # Connect if not already in voice
        if ctx.voice_client is None:
            await channel.connect()
        elif ctx.voice_client.channel != channel:
            await ctx.voice_client.move_to(channel)

        async with ctx.typing():
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
                source = discord.FFmpegOpusAudio(song["url"], **FFMPEG_OPTIONS)
                ctx.voice_client.play(source, after=lambda _: self._play_next(ctx.guild))
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

    @commands.command()
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

    @commands.command()
    async def remove(self, ctx: commands.Context, index: int):
        """Remove a song from the queue by its position (1-based)."""
        queue = self._get_queue(ctx.guild.id)
        if 1 <= index <= len(queue):
            removed = queue.pop(index - 1)
            await ctx.send(f"Removed **{removed['title']}** from the queue.")
        else:
            await ctx.send(f"Invalid index. Queue has {len(queue)} song(s).")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
