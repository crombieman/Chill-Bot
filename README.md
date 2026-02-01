# Chill Bot

A Discord music bot that plays audio from YouTube and other sources.

## Setup

1. Install [Python 3.10+](https://www.python.org/downloads/)
2. Install [FFmpeg](https://ffmpeg.org/download.html) and add it to your PATH
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Create a `.env` file and add your bot token to `.env`:
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
| `!play <url or search>` | Play a URL or search YouTube. Queues if something is already playing. |
| `!pause` | Pause the current track. |
| `!resume` | Resume playback. |
| `!skip` | Skip to the next song in the queue. |
| `!stop` | Stop playback, clear the queue, and disconnect. |
| `!queue` | Show the current song queue. |
| `!nowplaying` | Show info about the currently playing track. |
| `!remove <#>` | Remove a song from the queue by position. |

## Supported Sources

Anything supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), including YouTube, SoundCloud, Bandcamp, and more.
