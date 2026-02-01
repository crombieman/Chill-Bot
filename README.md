# Chill Bot

A Discord music bot that plays audio from YouTube and other sources.

## Setup

1. Install [Python 3.10+](https://www.python.org/downloads/)
2. Install [FFmpeg](https://ffmpeg.org/download.html) and add it to your PATH
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Create a `.env` file, copy the following contents, and add your bot token to `your_token_here`:
   ```
   DISCORD_TOKEN=your_token_here
   ```
5. Run the bot:
   ```
   python bot.py
   ```

## Commands

| Command | Description |
|---------|-------------|
| `!play <url or search>` | Play a URL or search YouTube. Supports playlists. Queues if something is already playing. |
| `!pause` | Pause the current track. |
| `!resume` | Resume playback. |
| `!skip` | Skip to the next song in the queue. |
| `!stop` | Stop playback, clear the queue, and disconnect. |
| `!queue` | Show the current song queue. |
| `!nowplaying` / `!np` | Show info about the currently playing track. |
| `!volume <0-100>` / `!vol` | Set volume (0–100). Shows current volume if no value given. |
| `!shuffle` | Shuffle the queue. |
| `!loop [off\|track\|queue]` | Toggle loop mode. Cycles through off/track/queue if no arg given. |
| `!seek <time>` | Seek to a position (e.g. `1:30` or `90`). |
| `!lyrics [song]` | Fetch lyrics. Uses the current track if no song is given. |
| `!clear` | Clear the queue without stopping the current song. |
| `!remove <#>` | Remove a song from the queue by position. |

## Supported Sources

Anything supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including YouTube, SoundCloud, Bandcamp, Spotify (track links), and more.

## Extra Features

1. Automatically skips Soundcloud Go+ songs

2. No queue size cap