import discord
from discord import app_commands
from discord.ui import View
import asyncio
import yt_dlp
import urllib.parse, urllib.request, re
from dotenv import load_dotenv
import os
import random
import sqlite3

# =======================
# Database Setup
# =======================
def init_db():
    conn = sqlite3.connect("musicbot.db")
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS guild_settings 
           (guild_id INTEGER PRIMARY KEY, dj_role INTEGER, 
            volume INTEGER DEFAULT 100, loop_mode TEXT DEFAULT 'none',
            always_on INTEGER DEFAULT 0)"""
    )
    conn.commit()
    conn.close()

# =======================
# Global Variables
# =======================
queues: dict[int, list[str]] = {}
voice_clients: dict[int, discord.VoiceClient] = {}
current_songs: dict[int, str] = {}
filters_active: dict[int, str] = {}
dj_roles: dict[int, int] = {}
bot_instance: discord.Client | None = None

# =======================
# yt‑dlp & FFmpeg
# =======================
ytdl_opts = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
}

ffmpeg_opts_base = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

FILTERS = {
    "bassboost": "bass=g=15,dynaudnorm=f=110",
    "nightcore": "atempo=1.2,asetrate=44100*1.2",
    "vaporwave": "atempo=0.85,asetrate=44100*0.85",
    "8d": "stereotools=phase=45:stereowideness=500",
    "karaoke": "stereotools=mlev=0.03:mlevtype=1:mono=1",
    "clear": "",
}

# =======================
# Helpers
# =======================
def get_volume(gid: int) -> int:
    conn = sqlite3.connect("musicbot.db")
    c = conn.cursor()
    c.execute("SELECT volume FROM guild_settings WHERE guild_id=?", (gid,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 100

def set_volume(gid: int, vol: int) -> None:
    conn = sqlite3.connect("musicbot.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO guild_settings (guild_id, volume) VALUES (?, ?)",
        (gid, vol),
    )
    conn.commit()
    conn.close()

def cleanup_guild(gid: int) -> None:
    voice_clients.pop(gid, None)
    queues.pop(gid, None)
    current_songs.pop(gid, None)
    filters_active.pop(gid, None)

def is_youtube_url(url: str) -> bool:
    return any(x in url.lower() for x in ["youtube.com", "youtu.be", "shorts", "watch?v="])

async def yt_search(query: str) -> str | None:
    try:
        q = urllib.parse.urlencode({"search_query": query})
        html = urllib.request.urlopen(
            f"https://www.youtube.com/results?{q}", timeout=10
        ).read().decode()
        vid = re.search(r"/watch\?v=(.{11})", html)
        return f"https://www.youtube.com/watch?v={vid.group(1)}" if vid else None
    except Exception:
        return None

# =======================
# Buttons & Filters (UI)
# =======================
class ProMusicButtons(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=7200)
        self.interaction = interaction

    def has_dj_permission(self, interaction: discord.Interaction) -> bool:
        guild = interaction.guild
        if not guild:
            return False
        dj_role_id = dj_roles.get(guild.id)
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        return (
            member.guild_permissions.administrator
            or (dj_role_id and any(r.id == dj_role_id for r in member.roles))
        )

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "⏮️ Previous अभी implement नहीं किया गया है.", ephemeral=True
        )

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.primary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.has_dj_permission(interaction):
            return await interaction.response.send_message(
                "❌ DJ/Admin only!", ephemeral=True
            )
        vc = voice_clients.get(interaction.guild.id)  # type: ignore
        if not vc:
            return await interaction.response.send_message(
                "❌ अभी कुछ भी नहीं चल रहा.", ephemeral=True
            )
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Resumed!", ephemeral=True)
        else:
            vc.pause()
            await interaction.response.send_message("⏸️ Paused!", ephemeral=True)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.has_dj_permission(interaction):
            return await interaction.response.send_message(
                "❌ DJ/Admin only!", ephemeral=True
            )
        vc = voice_clients.get(interaction.guild.id)  # type: ignore
        if vc:
            vc.stop()
        await interaction.response.send_message("⏭️ Skipped!", ephemeral=True)
        await play_next_safe(interaction)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.has_dj_permission(interaction):
            return await interaction.response.send_message(
                "❌ DJ/Admin only!", ephemeral=True
            )
        gid = interaction.guild.id  # type: ignore
        vc = voice_clients.get(gid)
        if vc:
            vc.stop()
            await asyncio.sleep(1)
            await vc.disconnect()
        cleanup_guild(gid)
        await interaction.response.send_message("⏹️ Stopped!", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.has_dj_permission(interaction):
            return await interaction.response.send_message(
                "❌ DJ/Admin only!", ephemeral=True
            )
        gid = interaction.guild.id  # type: ignore
        if queues.get(gid):
            random.shuffle(queues[gid])
        await interaction.response.send_message("🔀 Shuffled queue!", ephemeral=True)

class FilterSelect(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=300)
        self.interaction = interaction

    @discord.ui.select(
        placeholder="🎛️ Choose Filter",
        options=[discord.SelectOption(label=k.title(), value=k) for k in FILTERS.keys()],
    )
    async def select_filter(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ):
        gid = interaction.guild.id  # type: ignore
        filters_active[gid] = FILTERS[select.values[0]]
        await interaction.response.send_message(
            f"✅ {select.values[0].title()} applied!", ephemeral=True
        )

# =======================
# Core Play Logic
# =======================
async def play_next_safe(interaction: discord.Interaction):
    try:
        gid = interaction.guild.id  # type: ignore
        conn = sqlite3.connect("musicbot.db")
        c = conn.cursor()
        c.execute("SELECT loop_mode FROM guild_settings WHERE guild_id=?", (gid,))
        result = c.fetchone()
        loop_mode = result[0] if result else "none"
        conn.close()

        if loop_mode == "song" and gid in current_songs:
            await play_command(interaction, current_songs[gid])
        elif queues.get(gid):
            song = queues[gid].pop(0)
            current_songs[gid] = song
            await play_command(interaction, song)
    except Exception as e:
        print("play_next_safe error:", e)

async def play_command(interaction: discord.Interaction, query: str):
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        return

    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message(
            "❌ Voice channel join करो!", ephemeral=True
        )

    gid = interaction.guild.id  # type: ignore
    await interaction.response.defer(thinking=True)

    url = query if is_youtube_url(query) else await yt_search(query)
    if not url:
        return await interaction.followup.send("❌ Song नहीं मिला!")

    try:
        with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        stream_url = info["url"]
        title = info.get("title", "Unknown")[:100]
        duration = info.get("duration", 0)
        thumbnail = info.get("thumbnail", "")

    except Exception as e:
        return await interaction.followup.send(f"❌ yt-dlp error: {str(e)[:80]}")

    # Voice connect
    vc = voice_clients.get(gid)
    if not vc or not vc.is_connected():
        vc = await interaction.user.voice.channel.connect()
        voice_clients[gid] = vc
        set_volume(gid, 100)

    # If already playing, queue it
    if vc.is_playing() or vc.is_paused():
        queues.setdefault(gid, []).append(url)
        return await interaction.followup.send(
            f"📝 Added to queue:\n**{title}**  (`{len(queues[gid])}` in queue)"
        )

    vol = get_volume(gid)
    filter_str = filters_active.get(gid, "")
    if filter_str:
        ffmpeg_opts = {
            "before_options": ffmpeg_opts_base["before_options"],
            "options": f'-vn -af "{filter_str},volume={vol/100}"',
        }
    else:
        ffmpeg_opts = {
            "before_options": ffmpeg_opts_base["before_options"],
            "options": f'-vn -filter:a "volume={vol/100}"',
        }

    try:
        audio_source = discord.FFmpegOpusAudio(
            stream_url, executable="ffmpeg", **ffmpeg_opts
        )
    except Exception as e:
        return await interaction.followup.send(f"❌ FFmpeg error: {str(e)[:80]}")

    # =======================
    # Pro Max Now Playing UI
    # =======================
    color_playing = 0x00ff9f  # neon green / aqua
    duration_str = f"{duration//60:02d}:{duration%60:02d}" if duration else "--:--"

    embed = discord.Embed(
        title="🎧  NOW PLAYING",
        color=color_playing,
    )

    bar = "▱" * 20
    embed.description = f"**{title}**\n`{bar}`  `00:00 / {duration_str}`"

    embed.add_field(name="⏱️ Duration", value=f"`{duration_str}`", inline=True)
    embed.add_field(name="🔊 Volume", value=f"`{vol}%`", inline=True)

    active_filter = filters_active.get(gid, "")
    filter_name = (
        [k for k, v in FILTERS.items() if v == active_filter] or ["clear"]
    )[0].title()
    embed.add_field(name="🎛️ Filter", value=f"`{filter_name}`", inline=True)

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    try:
        avatar_url = interaction.user.display_avatar.url
    except Exception:
        avatar_url = None
    embed.set_footer(
        text=f"Requested by {interaction.user.display_name}",
        icon_url=avatar_url,
    )

    view = ProMusicButtons(interaction)
    msg = await interaction.followup.send(embed=embed, view=view)

    asyncio.create_task(progress_bar(msg, embed, title, duration, vc, duration_str))

    def after_callback(error: Exception | None):
        try:
            if bot_instance and bot_instance.is_ready():
                bot_instance.loop.call_soon_threadsafe(
                    asyncio.create_task, play_next_safe(interaction)
                )
        except Exception as e:
            print("after_callback error:", e)

    current_songs[gid] = url
    vc.play(audio_source, after=after_callback)

async def progress_bar(
    msg: discord.Message,
    embed: discord.Embed,
    title: str,
    duration: int,
    vc: discord.VoiceClient,
    duration_str: str,
):
    elapsed = 0
    bar_length = 20

    while vc.is_connected() and (vc.is_playing() or vc.is_paused()):
        if vc.is_paused():
            await asyncio.sleep(1)
            continue

        await asyncio.sleep(2)
        elapsed += 2

        if duration > 0:
            ratio = min(elapsed / duration, 1.0)
        else:
            ratio = 0.0

        filled = int(ratio * bar_length)
        bar = "▰" * filled + "▱" * (bar_length - filled)
        current_str = f"{elapsed//60:02d}:{elapsed%60:02d}"

        embed.description = (
            f"**{title}**\n"
            f"`{bar}`  `{current_str} / {duration_str}`"
        )

        try:
            await msg.edit(embed=embed)
        except discord.HTTPException:
            break

# =======================
# Bot + Slash Commands
# =======================
class ProMaxBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        init_db()
        await self.tree.sync()

bot = ProMaxBot()
bot_instance = bot

@bot.event
async def on_ready():
    print(f"🚀 PRO MAX MUSIC BOT logged in as {bot.user}")
    print("✅ Slash commands synced & ready!")

# /play
@bot.tree.command(name="play", description="Song play करो (name या YouTube URL)")
@app_commands.describe(query="Song name या YouTube link")
async def slash_play(interaction: discord.Interaction, query: str):
    await play_command(interaction, query)

# /volume
@bot.tree.command(name="volume", description="Volume set या देखो")
@app_commands.describe(level="0–150 (empty = current)")
async def slash_volume(interaction: discord.Interaction, level: int | None = None):
    gid = interaction.guild.id  # type: ignore
    if level is None:
        return await interaction.response.send_message(
            f"🔊 Volume: {get_volume(gid)}%", ephemeral=True
        )
    level = max(0, min(150, level))
    set_volume(gid, level)
    await interaction.response.send_message(
        f"🔊 Volume set: {level}%", ephemeral=True
    )

# /queue
@bot.tree.command(name="queue", description="Current music queue देखो")
async def slash_queue(interaction: discord.Interaction):
    gid = interaction.guild.id  # type: ignore
    q = queues.get(gid, [])
    if not q:
        return await interaction.response.send_message(
            "📝 Queue empty है", ephemeral=True
        )
    embed = discord.Embed(
        title=f"📝 Queue ({len(q)})", color=discord.Color.blurple()
    )
    for i, song in enumerate(q[:10]):
        embed.add_field(name=f"#{i+1}", value=song[:70], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# /clear
@bot.tree.command(name="clear", description="Queue पूरी साफ करो")
async def slash_clear(interaction: discord.Interaction):
    gid = interaction.guild.id  # type: ignore
    queues[gid] = []
    await interaction.response.send_message("🗑️ Queue cleared!", ephemeral=True)

# /filters
@bot.tree.command(name="filters", description="Audio filters menu")
async def slash_filters(interaction: discord.Interaction):
    gid = interaction.guild.id  # type: ignore
    active = filters_active.get(gid, "clear")
    embed = discord.Embed(title="🎛️ Filters", color=discord.Color.purple())
    for name, effect in FILTERS.items():
        status = "✅ Active" if effect == active else "❌"
        embed.add_field(name=name.title(), value=status, inline=True)
    await interaction.response.send_message(
        embed=embed, view=FilterSelect(interaction), ephemeral=True
    )

# /djset
@bot.tree.command(name="djset", description="DJ role set करो (Admin only)")
@app_commands.describe(role="DJ role mention करो")
@app_commands.checks.has_permissions(administrator=True)
async def slash_djset(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Guild context missing.", ephemeral=True
        )
    dj_roles[guild.id] = role.id
    conn = sqlite3.connect("musicbot.db")
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO guild_settings (guild_id, dj_role) VALUES (?, ?)",
        (guild.id, role.id),
    )
    conn.commit()
    conn.close()
    await interaction.response.send_message(
        f"✅ DJ role set: {role.mention}", ephemeral=True
    )

# =======================
# Run
# =======================
if __name__ == "__main__":
    load_dotenv()
    TOKEN = os.getenv("TOKEN") or "YOUR_BOT_TOKEN_HERE"
    bot.run(TOKEN)
