# -*- coding: utf-8 -*-
import discord
from discord.ext import commands
from discord.ui import View, Button
import asyncio
import yt_dlp
import urllib.parse, urllib.request, re
from dotenv import load_dotenv
import os

# =======================
# Custom Bot Class
# =======================
class MyBot(commands.Bot):
    async def setup_hook(self):
        self.loop.create_task(self.auto_leave_no_command_task())

    async def auto_leave_no_command_task(self):
        await self.wait_until_ready()
        global last_command_time

        while True:
            await asyncio.sleep(5)
            now = asyncio.get_event_loop().time()

            if not self.voice_clients:
                continue

            vc = self.voice_clients[0]

            if not vc.is_connected():
                continue

            if len(vc.channel.members) == 1:
                continue

            if now - last_command_time > AUTO_LEAVE_NO_COMMAND:
                try:
                    if not vc.is_playing() and not vc.is_paused():
                        await vc.disconnect(force=True)
                        guild_id = vc.guild.id
                        voice_clients.pop(guild_id, None)
                        print("Auto Leave: No command for 5 minutes")
                except:
                    pass


def run_bot():
    load_dotenv()
    TOKEN = os.getenv("TOKEN") or "YOUR_BOT_TOKEN_HERE"

    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True

    client = MyBot(command_prefix=".", intents=intents)

    global last_command_time, AUTO_LEAVE_EMPTY_VC, AUTO_LEAVE_NO_COMMAND
    global queues, voice_clients

    AUTO_LEAVE_EMPTY_VC = 60
    AUTO_LEAVE_NO_COMMAND = 300
    last_command_time = 0

    queues = {}
    voice_clients = {}

    async def auto_leave_if_empty(vc):
        await asyncio.sleep(AUTO_LEAVE_EMPTY_VC)
        if vc and vc.is_connected():
            if len(vc.channel.members) == 1:
                try:
                    guild_id = vc.guild.id
                    await vc.disconnect(force=True)
                    voice_clients.pop(guild_id, None)
                    print("Auto Leave: Empty VC")
                except:
                    pass

    @client.event
    async def on_command(ctx):
        global last_command_time
        last_command_time = asyncio.get_event_loop().time()

    # =======================
    # Youtube Search
    # =======================
    YT_BASE = "https://www.youtube.com/"
    YT_RESULTS = YT_BASE + "results?"
    YT_WATCH = YT_BASE + "watch?v="

    ytdl = yt_dlp.YoutubeDL({"format": "bestaudio"})
    ffmpeg_opts = {
        "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
        "options": "-vn"
    }

    def is_youtube_url(url):
        return (
            "youtube.com" in url or
            "youtu.be" in url or
            "shorts" in url
        )

    async def yt_search(text):
        q = urllib.parse.urlencode({"search_query": text})
        try:
            html = urllib.request.urlopen(YT_RESULTS + q).read().decode()
            ids = re.findall(r"/watch\?v=(.{11})", html)
            if not ids:
                return None
            return YT_WATCH + ids[0]
        except:
            return None

    # =======================
    # Play Next
    # =======================
    async def play_next(ctx):
        gid = ctx.guild.id
        if gid not in queues:
            return

        if queues[gid]:
            next_song = queues[gid].pop(0)
            await play(ctx, link=next_song)

    # =======================
    # Music Buttons
    # =======================
    class MusicButtons(View):
        def __init__(self, ctx, msg):
            super().__init__(timeout=None)
            self.ctx = ctx
            self.msg = msg

        @discord.ui.button(label="Skip", style=discord.ButtonStyle.blurple)
        async def skip(self, interaction, button):
            vc = voice_clients.get(self.ctx.guild.id)
            if vc:
                vc.stop()
            await interaction.response.send_message("Skipped.", delete_after=2)
            await asyncio.sleep(0.2)
            await play_next(self.ctx)

        @discord.ui.button(label="Pause", style=discord.ButtonStyle.gray)
        async def pause(self, interaction, button):
            vc = voice_clients.get(self.ctx.guild.id)
            if vc:
                vc.pause()
            await interaction.response.send_message("Paused.", delete_after=2)

        @discord.ui.button(label="Resume", style=discord.ButtonStyle.green)
        async def resume(self, interaction, button):
            vc = voice_clients.get(self.ctx.guild.id)
            if vc:
                vc.resume()
            await interaction.response.send_message("Resumed.", delete_after=2)

        @discord.ui.button(label="Stop", style=discord.ButtonStyle.red)
        async def stop(self, interaction, button):
            vc = voice_clients.get(self.ctx.guild.id)
            if vc:
                vc.stop()
                await vc.disconnect(force=True)
                voice_clients.pop(self.ctx.guild.id, None)

            await interaction.response.send_message("Stopped.", delete_after=2)

    # =======================
    # PLAY COMMAND (UPDATED SYSTEM)
    # =======================
    @client.command()
    async def play(ctx, *, link):
        global last_command_time
        last_command_time = asyncio.get_event_loop().time()

        # Delete user command always
        try:
            await ctx.message.delete()
        except:
            pass

        gid = ctx.guild.id

        # LOADING ANIMATION
        loading = await ctx.send("🔄 **Loading song…**")
        await asyncio.sleep(0.6)
        await loading.edit(content="⏳ **Processing audio…**")

        # Search handling
        if not is_youtube_url(link):
            link = await yt_search(link)

        if not link:
            return await loading.edit(content="❌ No results found.")

        # Extract audio BEFORE join VC
        try:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ytdl.extract_info(link, download=False)
            )
        except Exception as e:
            return await loading.edit(content=f"❌ Error:\n```{e}```")

        title = info.get("title", "Unknown")
        thumb = info.get("thumbnail")
        duration = info.get("duration", 0)
        stream_url = info["url"]

        audio = discord.FFmpegOpusAudio(stream_url, **ffmpeg_opts)

        # Final preparing
        await loading.edit(content=f"🎵 **Almost ready…**")

        # JOIN VC after loading completes
        if gid not in voice_clients or not voice_clients[gid].is_connected():
            try:
                vc = await ctx.author.voice.channel.connect()
            except:
                return await loading.edit(content="❌ Join a voice channel first.")
            voice_clients[gid] = vc
            client.loop.create_task(auto_leave_if_empty(vc))

        vc = voice_clients[gid]

        # Queue handling
        if vc.is_playing() or vc.is_paused():
            queues.setdefault(gid, []).append(link)
            m = await loading.edit(content="📝 Added to queue.")
            await asyncio.sleep(2)
            await loading.delete()
            return

        # Delete loading now
        await loading.delete()

        # NOW PLAYING
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**{title}**\nRequested by: {ctx.author.mention}",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=thumb)

        embed.set_author(
            name="Music Maniac Bot",
            icon_url=client.user.avatar.url if client.user.avatar else None
        )

        msg = await ctx.send(embed=embed)
        await msg.edit(view=MusicButtons(ctx, msg))

        # PROGRESS BAR
        async def updater():
            elapsed = 0
            while vc.is_playing() or vc.is_paused():
                if vc.is_paused():
                    await asyncio.sleep(1)
                    continue

                elapsed += 2
                filled = int((elapsed / duration) * 20) if duration else 0
                bar = "[" + "=" * filled + "-" * (20 - filled) + "]"

                next_name = "None"
                if queues.get(gid):
                    try:
                        inf = await asyncio.get_event_loop().run_in_executor(
                            None, lambda: ytdl.extract_info(queues[gid][0], download=False)
                        )
                        next_name = inf.get("title", "Unknown")
                    except:
                        next_name = "Unknown"

                embed.description = (
                    f"**{title}**\n"
                    f"Elapsed: {elapsed}s / {duration}s\n"
                    f"{bar}\n\n"
                    f"➡️ **Next:** {next_name}"
                )

                try:
                    await msg.edit(embed=embed)
                except:
                    pass

                await asyncio.sleep(2)

        asyncio.create_task(updater())

        # PLAY audio
        def after(e):
            fut = asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop)
            try:
                fut.result()
            except:
                pass

        vc.play(audio, after=after)

    # =======================
    # QUEUE COMMAND
    # =======================
    @client.command()
    async def queue(ctx, *, link):
        try:
            await ctx.message.delete()
        except:
            pass

        gid = ctx.guild.id
        queues.setdefault(gid, []).append(link)

        m = await ctx.send("Added to queue.")
        await asyncio.sleep(2)
        await m.delete()

    @client.command()
    async def clear_queue(ctx):
        try:
            await ctx.message.delete()
        except:
            pass
        gid = ctx.guild.id
        queues[gid] = []

        m = await ctx.send("Queue cleared.")
        await asyncio.sleep(2)
        await m.delete()

    client.run(TOKEN)


run_bot()
