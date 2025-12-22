from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import yt_dlp
import os
import asyncio

API_ID = 26514816
API_HASH = "a853e553875a7903bdc49016085825ca"
BOT_TOKEN = "8583785725:AAER1Uq3RjPJPi1YTObSNaVPcT5JCPnaBV8"

app = Client("downloader", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

user_links = {}
user_formats = {}

MAX_CONCURRENT_DOWNLOADS = 3
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

COOKIES_FILE = "instagram_cookies.txt"

# Fix YouTube signature issues
yt_dlp.utils.std_headers['User-Agent'] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# ------------------------- Progress Hook -------------------------
async def progress_hook(d, message):
    if d['status'] == 'downloading':
        try:
            downloaded = d.get('downloaded_bytes', 0)
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            percent = (downloaded / total * 100) if total else 0

            bar = "█" * int(percent // 10) + "—" * (10 - int(percent // 10))

            txt = (
                f"⏳ **Downloading...**\n"
                f"Progress: `{percent:.1f}%`\n"
                f"[{bar}]"
            )
            await message.edit(txt)
        except:
            pass


# ------------------------- Download Function -------------------------
async def download(url, quality_format, status_msg):
    async with download_semaphore:
        ydl_opts = {
            "format": quality_format,
            "cookies": COOKIES_FILE,
            "outtmpl": "downloads/%(title)s.%(ext)s",
            "allow_unplayable_formats": True,
            "progress_hooks": [lambda d: asyncio.create_task(progress_hook(d, status_msg))]
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            return file_path, info.get("title", "Video")


# ------------------------- Bot Start -------------------------
@app.on_message(filters.command("start"))
async def start(_, msg):
    await msg.reply("👋 **Welcome!**\nSend any **YouTube / Instagram / Facebook** link to download.")


# ------------------------- Link Handler -------------------------
@app.on_message(filters.text)
async def get_link(_, msg):
    url = msg.text.strip()

    if not any(x in url for x in ["youtu", "instagram", "facebook", "fb.watch", "reel"]):
        return await msg.reply("❌ Unsupported URL.\nOnly YouTube, Instagram, Facebook allowed.")

    temp_msg = await msg.reply("🔍 Fetching video info...")

    try:
        with yt_dlp.YoutubeDL({
            "quiet": True,
            "cookies": COOKIES_FILE,
            "ignoreerrors": True,
            "user_agent": "Mozilla/5.0"
        }) as ydl:
            info = ydl.extract_info(url, download=False)

    except Exception as e:
        return await temp_msg.edit(f"❌ Failed.\n**Instagram mostly needs fresh cookies.**\n\n`{e}`")

    user_links[msg.from_user.id] = url
    formats = info.get("formats", [])

    qualities = sorted(list(set([f["height"] for f in formats if f.get("height")])))

    buttons = [[InlineKeyboardButton("🎵 Audio", callback_data="audio")]]
    for q in qualities:
        buttons.append([InlineKeyboardButton(f"{q}p", callback_data=str(q))])

    await temp_msg.edit("📌 **Available Qualities:**", reply_markup=InlineKeyboardMarkup(buttons))


# ------------------------- Callback Handler -------------------------
@app.on_callback_query()
async def callback(client: Client, call: CallbackQuery):
    user_id = call.from_user.id
    url = user_links.get(user_id)

    if not url:
        return await call.answer("❌ URL expired!", show_alert=True)

    quality = call.data

    # Final SAFE format for all platforms
    if quality == "audio":
        fmt = "bestaudio/best"
    else:
        h = int(quality)
        fmt = (
            f"bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height={h}][ext=mp4]/"
            f"bestvideo[height<={h}][ext=mp4]+bestaudio/best"
        )

    status_msg = await call.message.reply("⏳ **Starting download...**")

    try:
        file_path, title = await download(url, fmt, status_msg)

        await call.message.reply_video(
            video=file_path,
            caption=f"✔ **Done!**\n🎬 {title}\n📌 {quality}"
        )

        await status_msg.delete()
        os.remove(file_path)

    except Exception as e:
        await status_msg.edit(f"❌ Error:\n`{e}`")


# ------------------------- Run Bot -------------------------
app.run()
