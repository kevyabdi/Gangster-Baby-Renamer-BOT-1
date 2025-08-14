# plugins# plugins/cb_data.py
import os
import time
import asyncio
import aiohttp
from typing import Optional, Tuple, Dict, Any

from pyrogram import Client, filters
from pyrogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, Message
)
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

# ========= CONFIG =========
TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# Ku hagaaji xawaaraha/culayska host-kaaga
PARALLEL_WORKERS = 20            # inta requests ee isla mar socda
CHUNK_SIZE = 8 * 1024 * 1024     # 8MB per chunk
PROG_EDIT_INTERVAL = 0.75        # ilbiriqsiyada u dhexeeya updates

# Kaydi xaaladda chat kasta
pending: Dict[int, Dict[str, Any]] = {}
# =========================


# ========= HELPERS =========
def _safe_name(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    return name or "file"

def humanbytes(size: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}PB"

def _progress_bar(pct: float, width: int = 10) -> str:
    filled = int((pct / 100.0) * width)
    return "◾️" * max(0, filled) + "◽️" * max(0, width - filled)

async def _throttled_edit(msg: Message, text: str, last_edit_time: list):
    now = time.time()
    if now - last_edit_time[0] >= PROG_EDIT_INTERVAL:
        last_edit_time[0] = now
        try:
            await msg.edit_text(text)
        except Exception:
            pass

async def _extract_meta(path: str):
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
    """Samee thumbnail ≤320px, ≤200KB."""
    path = os.path.join(TMP_DIR, f"{user_id}_thumb.jpg")
    try:
        photos = await client.get_profile_photos(user_id, limit=1)
        if getattr(photos, "total_count", 0) > 0:
            f = await client.download_media(photos[0].file_id, file_name=path)
            im = Image.open(f).convert("RGB")
            im.thumbnail((320, 320))
            im.save(path, "JPEG", quality=85, optimize=True)
            if os.path.getsize(path) > 200_000:
                im.save(path, "JPEG", quality=70, optimize=True)
            return path
    except Exception:
        pass
    return None
# ===========================


# ========= FAST TG DOWNLOAD =========
async def _get_tg_file_direct_url(client: Client, src_msg: Message) -> Tuple[str, int, str]:
    """Soo celi (url, size, original_name) ee Telegram Bot API file CDN."""
    if src_msg.document:
        file_id = src_msg.document.file_id
        total = int(src_msg.document.file_size or 0)
        basename = src_msg.document.file_name or "file"
    elif src_msg.video:
        file_id = src_msg.video.file_id
        total = int(src_msg.video.file_size or 0)
        basename = src_msg.video.file_name or "video.mp4"
    elif src_msg.audio:
        file_id = src_msg.audio.file_id
        total = int(src_msg.audio.file_size or 0)
        basename = src_msg.audio.file_name or "audio.mp3"
    else:
        raise ValueError("Unsupported media type")

    f = await client.get_file(file_id)
    url = f"https://api.telegram.org/file/bot{client.bot_token}/{f.file_path}"
    return url, total, basename

async def parallel_download(url: str, dest_path: str, total_size: int, status_msg: Message, title: str):
    """Parallel HTTP range download + progress."""
    ranges = [(i, min(i + CHUNK_SIZE - 1, total_size - 1))
              for i in range(0, total_size, CHUNK_SIZE)]
    part_paths = [f"{dest_path}.part{i}" for i in range(len(ranges))]

    downloaded = 0
    started = time.time()
    last_edit_time = [0.0]
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(PARALLEL_WORKERS)

    async def render_progress():
        pct = (downloaded * 100 / total_size) if total_size else 0.0
        bar = _progress_bar(pct)
        speed = downloaded / max(0.2, (time.time() - started))
        eta = (total_size - downloaded) / speed if speed > 0 else 0
        text = (
            f"⚠️Please wait...\n\n"
            f"{title}\n"
            f"[{bar}]  `{pct:.1f}%`\n"
            f"{humanbytes(downloaded)} of {humanbytes(total_size)}\n"
            f"Speed: {humanbytes(speed)}/s\nETA: {int(eta)}s"
        )
        await _throttled_edit(status_msg, text, last_edit_time)

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

    await status_msg.edit_text("⚠️Please wait...\n\nStarting fast download...")
    tasks = [fetch_part(i, rng[0], rng[1]) for i, rng in enumerate(ranges)]
    await asyncio.gather(*tasks)

    # Merge parts
    with open(dest_path, "wb") as merged:
        for p in part_paths:
            with open(p, "rb") as pf:
                merged.write(pf.read())
            try:
                os.remove(p)
            except Exception:
                pass

    try:
        await status_msg.edit_text(f"✅ Download complete: {humanbytes(total_size)}")
    except Exception:
        pass
# =====================================


# ========= UI / FLOW =========
@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def on_media(client: Client, message: Message):
    """Tallaabo 1: file yimaado → START RENAME."""
    media = getattr(message, message.media.value)
    fname = getattr(media, "file_name", None) or "file"
    fsize = getattr(media, "file_size", 0)

    pending[message.chat.id] = {
        "src_msg_id": message.id,
        "media_type": message.media.value,
        "state": None,
        "new_name": None
    }

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("START RENAME", callback_data="start_rename")],
            [InlineKeyboardButton("❌ CANCEL", callback_data="cancel")]
        ]
    )
    await message.reply_text(
        f"**What do you want me to do with this file.?**\n"
        f"• **File Name :** `{fname}`\n"
        f"• **File Size :** `{humanbytes(fsize)}`",
        reply_markup=kb,
        quote=True
    )

@Client.on_callback_query()
async def on_cb(client: Client, query):
    data = query.data
    chat_id = query.message.chat.id

    # CANCEL
    if data == "cancel":
        try:
            await query.message.delete()
        except Exception:
            pass
        pending.pop(chat_id, None)
        return

    # START RENAME → dalbo magac cusub
    if data == "start_rename":
        src = query.message.reply_to_message
        if not src:
            info = pending.get(chat_id)
            if info:
                try:
                    src = await client.get_messages(chat_id, info.get("src_msg_id", 0))
                except Exception:
                    src = None
        if not src or not src.media:
            return await query.answer("❌ No source file found.", show_alert=True)

        st = pending.setdefault(chat_id, {})
        st["state"] = "await_name"
        st["prompt_msg_id"] = query.message.id

        try:
            await query.message.edit_text(
                "✏️ **Send new file name with extension**\n"
                "_e.g._ `MyVideo.mp4`\n\nReply to this message.",
                reply_markup=ForceReply(selective=True)
            )
        except Exception:
            await query.message.reply_text(
                "✏️ **Send new file name with extension**\n"
                "_e.g._ `MyVideo.mp4`\n\nReply to this message.",
                reply_markup=ForceReply(selective=True)
            )
        return

    # DOORO NOOCA SOO SAARKA
    if data in ("choose_document", "choose_video", "choose_audio"):
        kind = data.split("_", 1)[1]  # document|video|audio
        st = pending.get(chat_id)
        if not st or st.get("state") != "ready_to_process" or not st.get("new_name"):
            return await query.answer("❌ No pending rename. Start again.", show_alert=True)

        # hel src
        src = None
        if "src_msg_id" in st:
            try:
                src = await client.get_messages(chat_id, st["src_msg_id"])
            except Exception:
                src = None
        if not src or not src.media:
            return await query.answer("❌ Source message not found.", show_alert=True)

        out_name = st["new_name"]
        status = await query.message.edit_text("⚠️Please wait...\nPreparing fast download...")

        try:
            url, total, _ = await _get_tg_file_direct_url(client, src)
        except Exception as e:
            return await status.edit_text(f"❌ Could not get file URL: `{e}`")

        out_path = os.path.join(TMP_DIR, out_name)
        await parallel_download(url, out_path, total, status, title=f"Downloading `{out_name}`")

        # Thumb & meta
        ph_path = await _prepare_thumb(client, chat_id)
        width = height = duration = None
        if src.video or src.audio:
            width, height, duration = await _extract_meta(out_path)

        # Upload progress
        up_started = time.time()
        last_edit_time = [0.0]

        async def upload_progress(current: int, total_up: int):
            pct = current * 100 / max(1, total_up)
            bar = _progress_bar(pct)
            speed = current / max(0.2, (time.time() - up_started))
            eta = (total_up - current) / speed if speed > 0 else 0
            text = (
                "⚠️Please wait...\n\n"
                f"Uploading `{out_name}`\n"
                f"[{bar}]  `{pct:.1f}%`\n"
                f"{humanbytes(current)} of {humanbytes(total_up)}\n"
                f"Speed: {humanbytes(speed)}/s\nETA: {int(eta)}s"
            )
            await _throttled_edit(status, text, last_edit_time)

        try:
            if kind == "document":
                await client.send_document(
                    chat_id,
                    document=out_path,
                    caption=out_name,
                    thumb=ph_path,
                    progress=upload_progress
                )
            elif kind == "video":
                await client.send_video(
                    chat_id,
                    video=out_path,
                    caption=out_name,
                    thumb=ph_path,
                    width=width,
                    height=height,
                    duration=duration,
                    supports_streaming=True,
                    progress=upload_progress
                )
            else:  # audio
                await client.send_audio(
                    chat_id,
                    audio=out_path,
                    caption=out_name,
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

        pending.pop(chat_id, None)
        return


# Qabta jawaabta magaca (reply), adigoon isticmaalin pyromod.listen
@Client.on_message(filters.private & filters.text & filters.reply)
async def on_name_reply(client: Client, message: Message):
    chat_id = message.chat.id
    st = pending.get(chat_id)
    if not st or st.get("state") != "await_name":
        return

    new_name = _safe_name(message.text)

    # haddii extension la iloobay → soo ceshashada kii hore
    ext = ""
    try:
        src = await client.get_messages(chat_id, st.get("src_msg_id", 0))
        if src and src.media:
            if src.document and src.document.file_name:
                _, ext = os.path.splitext(src.document.file_name)
            elif src.video and src.video.file_name:
                _, ext = os.path.splitext(src.video.file_name)
            elif src.audio and src.audio.file_name:
                _, ext = os.path.splitext(src.audio.file_name)
    except Exception:
        pass
    if not os.path.splitext(new_name)[1] and ext:
        new_name += ext

    st["new_name"] = new_name
    st["state"] = "ready_to_process"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📄 DOCUMENTS", callback_data="choose_document")],
            [InlineKeyboardButton("🎬 VIDEO", callback_data="choose_video")],
            [InlineKeyboardButton("🎵 AUDIO", callback_data="choose_audio")],
            [InlineKeyboardButton("❌ CANCEL", callback_data="cancel")]
        ]
    )
    await message.reply_text(
        f"**Select the output file type**\n• **File Name :** `{new_name}`",
        reply_markup=kb,
        quote=True
    )

