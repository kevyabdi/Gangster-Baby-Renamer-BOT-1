# plugins/cb_data.py
import os
import math
import time
import asyncio
import aiohttp
from typing import Optional, Tuple

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
)
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

# ===================== CONFIG & SETUP =====================

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# Tweak these for speed vs. memory/CPU:
PARALLEL_WORKERS = 20          # tirada isku mar wax soo dejisa
CHUNK_SIZE = 8 * 1024 * 1024   # 8MB per HTTP range request
PROG_EDIT_INTERVAL = 0.75      # ilbiriqsiyo u dhaxeeya update-yada progress

# pending state: (chat_id -> dict)
pending = {}  # {chat_id: {"src_msg_id": int, "media_type": "document|video|audio"}}

# ===================== HELPERS =====================

def _safe_name(name: str) -> str:
    # Ka saar traversal & whitespace
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    return name or "file"

def humanbytes(size: float) -> str:
    # 1024 base
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}PB"

async def _extract_meta(path: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    width = height = duration = None
    try:
        metadata = extractMetadata(createParser(path))
        if metadata:
            if metadata.has("duration"):
                duration = int(metadata.get("duration").seconds)
            if metadata.has("width"):
                width = int(metadata.get("width"))
            if metadata.has("height"):
                height = int(metadata.get("height"))
    except Exception:
        pass
    return width, height, duration

async def _prepare_thumb(client: Client, user_id: int) -> Optional[str]:
    """Ilaali thumbnail ≤320px, ≤200KB (Telegram limits)"""
    path = os.path.join(TMP_DIR, f"{user_id}_thumb.jpg")
    try:
        photos = await client.get_profile_photos(user_id, limit=1)
        if getattr(photos, "total_count", 0) > 0:
            th = await client.download_media(photos[0].file_id, file_name=path)
            im = Image.open(th).convert("RGB")
            im.thumbnail((320, 320))
            im.save(path, "JPEG", quality=85, optimize=True)
            if os.path.getsize(path) > 200_000:
                im.save(path, "JPEG", quality=70, optimize=True)
            return path
    except Exception:
        pass
    return None

def _progress_bar(pct: float, width: int = 10) -> str:
    filled = int((pct / 100.0) * width)
    return "◾️" * max(0, filled) + "◽️" * max(0, width - filled)

async def _throttled_edit(msg, text, last_edit_time: list):
    now = time.time()
    if now - last_edit_time[0] >= PROG_EDIT_INTERVAL:
        last_edit_time[0] = now
        try:
            await msg.edit_text(text)
        except Exception:
            pass

# ===================== PARALLEL DOWNLOAD (Telegram CDN) =====================

async def _get_tg_file_direct_url(client: Client, src_msg) -> Tuple[str, int, str]:
    """
    Soo saar URL-ka tooska ah ee Telegram Bot API file + total size + basename.
    """
    if src_msg.document:
        file_id = src_msg.document.file_id
        total = src_msg.document.file_size or 0
        basename = src_msg.document.file_name or "file"
    elif src_msg.video:
        file_id = src_msg.video.file_id
        total = src_msg.video.file_size or 0
        basename = src_msg.video.file_name or "video.mp4"
    elif src_msg.audio:
        file_id = src_msg.audio.file_id
        total = src_msg.audio.file_size or 0
        basename = src_msg.audio.file_name or "audio.mp3"
    else:
        raise ValueError("Unsupported media")

    f = await client.get_file(file_id)
    # Bot API file URL (waa mid degdeg ah haddii server-ku xoog leeyahay)
    url = f"https://api.telegram.org/file/bot{client.bot_token}/{f.file_path}"
    return url, int(total), basename

async def parallel_download(
    url: str,
    dest_path: str,
    total_size: int,
    status_msg,
    title: str = "Downloading"
):
    """
    Parallel HTTP Range download with live progress.
    """
    # Diyaarso parts ranges
    ranges = [(i, min(i + CHUNK_SIZE - 1, total_size - 1))
              for i in range(0, total_size, CHUNK_SIZE)]
    part_paths = [f"{dest_path}.part{i}" for i in range(len(ranges))]

    downloaded = 0
    started = time.time()
    last_edit_time = [0.0]
    lock = asyncio.Lock()  # si loo isku dubbarido counter-ka

    async def render_progress():
        pct = (downloaded * 100 / total_size) if total_size else 0.0
        bar = _progress_bar(pct)
        speed = downloaded / max(0.1, (time.time() - started))
        eta = (total_size - downloaded) / speed if speed > 0 else 0
        text = (
            f"⚠️Please wait...\n\n"
            f"{title}\n"
            f"[{bar}]  `{pct:.1f}%`\n"
            f"{humanbytes(downloaded)} of {humanbytes(total_size)}\n"
            f"Speed: {humanbytes(speed)}/s\n"
            f"ETA: {int(eta)}s"
        )
        await _throttled_edit(status_msg, text, last_edit_time)

    sem = asyncio.Semaphore(PARALLEL_WORKERS)

    async def fetch_part(idx: int, start: int, end: int):
        nonlocal downloaded
        headers = {"Range": f"bytes={start}-{end}"}
        async with aiohttp.ClientSession() as session:
            async with sem:
                async with session.get(url, headers=headers) as r:
                    r.raise_for_status()
                    with open(part_paths[idx], "wb") as out:
                        async for chunk in r.content.iter_chunked(512 * 1024):
                            if not chunk:
                                break
                            out.write(chunk)
                            async with lock:
                                downloaded += len(chunk)
                            await render_progress()

    # Bilaab downloads
    await status_msg.edit_text("⚠️Please wait...\n\nStarting fast download...")
    tasks = [fetch_part(i, rng[0], rng[1]) for i, rng in enumerate(ranges)]
    await asyncio.gather(*tasks)

    # Isku dar parts
    with open(dest_path, "wb") as merged:
        for p in part_paths:
            with open(p, "rb") as pf:
                merged.write(pf.read())
            try:
                os.remove(p)
            except Exception:
                pass

    # Final progress 100%
    try:
        await status_msg.edit_text(
            f"✅ Download complete\n{humanbytes(total_size)} saved."
        )
    except Exception:
        pass

# ===================== UI STEPS =====================

@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def on_media(client: Client, message):
    """Marka file la soo diro → soo saar START RENAME button."""
    media = getattr(message, message.media.value)
    fname = getattr(media, "file_name", None) or "file"
    fsize = getattr(media, "file_size", 0)

    # Xusuusnow fariinta asalka ah si aan kadib uga faa'iideysano (reply missing scenarios)
    pending[message.chat.id] = {
        "src_msg_id": message.id,
        "media_type": message.media.value
    }

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("START RENAME", callback_data="start_rename")],
            [InlineKeyboardButton("❌ CANCEL", callback_data="cancel")]
        ]
    )
    await message.reply_text(
        f"**What do you want me to do with this file.?**\n"
        f"**File Name :** `{fname}`\n"
        f"**File Size :** `{humanbytes(fsize)}`",
        reply_markup=kb,
        quote=True
    )

@Client.on_callback_query()
async def on_cb(client: Client, query):
    data = query.data

    if data == "cancel":
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if data == "start_rename":
        # Hel source (reply_to_message ama kaydkeenna)
        src = query.message.reply_to_message
        if not src:
            info = pending.get(query.message.chat.id)
            if info:
                try:
                    src = await client.get_messages(query.message.chat.id, info["src_msg_id"])
                except Exception:
                    src = None

        if not src or not src.media:
            return await query.answer("❌ No source file found.", show_alert=True)

        # Weydii magac cusub
        try:
            await query.message.edit_text(
                "✏️ **Send new file name with extension**\n"
                "_e.g._ `MyVideo.mp4`\n\n"
                "Reply to this message.",
                reply_markup=ForceReply(selective=True)
            )
        except Exception:
            await query.message.reply_text(
                "✏️ **Send new file name with extension**\n"
                "_e.g._ `MyVideo.mp4`\n\n"
                "Reply to this message.",
                reply_markup=ForceReply(selective=True)
            )

        # Sug jawaabta (60s)
        try:
            new_name_msg = await client.listen(query.message.chat.id, filters=filters.text, timeout=60)
        except asyncio.TimeoutError:
            return await query.message.edit_text("⌛ Timed out. Try again.")

        new_name = _safe_name(new_name_msg.text)
        # Haddii uusan lahayn extension, ka qaado kii hore
        ext = ""
        if src.document and src.document.file_name:
            _, ext = os.path.splitext(src.document.file_name)
        elif src.video and src.video.file_name:
            _, ext = os.path.splitext(src.video.file_name)
        elif src.audio and src.audio.file_name:
            _, ext = os.path.splitext(src.audio.file_name)
        if not os.path.splitext(new_name)[1] and ext:
            new_name += ext

        # Bilaab download degdeg ah (parallel)
        status = await new_name_msg.reply_text("⚠️Please wait...\nPreparing fast download...")
        try:
            url, total, _ = await _get_tg_file_direct_url(client, src)
        except Exception as e:
            return await status.edit_text(f"❌ Could not get file URL: `{e}`")

        out_path = os.path.join(TMP_DIR, new_name)
        await parallel_download(url, out_path, total, status, title=f"Downloading `{new_name}`")

        # Thumb & meta
        ph_path = await _prepare_thumb(client, query.message.chat.id)
        width = height = duration = None
        if src.video or src.audio:
            width, height, duration = await _extract_meta(out_path)

        # Upload (with progress)
        up_started = time.time()
        last_edit_time = [0.0]

        async def upload_progress(current: int, total_up: int):
            pct = current * 100 / max(1, total_up)
            bar = _progress_bar(pct)
            speed = current / max(0.1, (time.time() - up_started))
            eta = (total_up - current) / speed if speed > 0 else 0
            text = (
                "⚠️Please wait...\n\n"
                f"Uploading `{new_name}`\n"
                f"[{bar}]  `{pct:.1f}%`\n"
                f"{humanbytes(current)} of {humanbytes(total_up)}\n"
                f"Speed: {humanbytes(speed)}/s\nETA: {int(eta)}s"
            )
            await _throttled_edit(status, text, last_edit_time)

        try:
            if src.document:
                await client.send_document(
                    query.message.chat.id,
                    document=out_path,
                    caption=new_name,
                    thumb=ph_path,
                    progress=upload_progress
                )
            elif src.video:
                await client.send_video(
                    query.message.chat.id,
                    video=out_path,
                    caption=new_name,
                    thumb=ph_path,
                    width=width,
                    height=height,
                    duration=duration,
                    supports_streaming=True,
                    progress=upload_progress
                )
            elif src.audio:
                await client.send_audio(
                    query.message.chat.id,
                    audio=out_path,
                    caption=new_name,
                    thumb=ph_path,
                    duration=duration,
                    progress=upload_progress
                )
            try:
                await status.edit_text("✅ Done!")
                await asyncio.sleep(1.2)
                await status.delete()
            except Exception:
                pass
        except Exception as e:
            try:
                await status.edit_text(f"❌ Upload failed: `{e}`")
            except Exception:
                pass
        finally:
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
                if ph_path and os.path.exists(ph_path):
                    os.remove(ph_path)
            except Exception:
                pass
