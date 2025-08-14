from helper.utils import progress_for_pyrogram
from pyrogram import Client, filters
from pyrogram.types import ForceReply
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
import os
import time
from PIL import Image
import aiofiles
import aiohttp
import math

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1].strip()
    return name or "file"


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


async def _prepare_thumb(client: Client, user_id: int):
    path = os.path.join(TMP_DIR, f"{user_id}_thumb.jpg")
    try:
        th_msg = await client.download_media(await client.get_profile_photos(user_id, limit=1).next(), file_name=path)
        im = Image.open(th_msg).convert("RGB")
        im.thumbnail((320, 320))
        im.save(path, "JPEG", quality=80, optimize=True)
        return path
    except Exception:
        return None


def _parse_new_name(from_text: str) -> str | None:
    if not from_text:
        return None
    if "```" in from_text:
        try:
            seg = from_text.split("```", 1)[1]
            return _safe_name(seg.split("```", 1)[0])
        except Exception:
            pass
    key = "File Name"
    if key in from_text:
        after = from_text.split(key, 1)[1]
        for sep in [":", "-", ":-"]:
            if sep in after:
                return _safe_name(after.split(sep, 1)[1].strip())
    return None


async def chunked_download(client, message, file_name, status_msg):
    # Download with progress
    c_time = time.time()
    file_path = os.path.join(TMP_DIR, file_name)

    async def progress(current, total):
        now = time.time()
        diff = now - c_time
        if diff == 0:
            diff = 0.001
        percentage = current * 100 / total
        speed = current / diff
        eta = round((total - current) / speed)
        bar_length = 10
        filled = int(bar_length * current // total)
        bar = "◾️" * filled + "◽️" * (bar_length - filled)
        text = (
            f"⚠️ **Please wait...**\n\n"
            f"[{bar}] \n"
            f"{percentage:.1f}%\n"
            f"{current/1024/1024:.1f}MB of {total/1024/1024:.1f}MB\n"
            f"Speed: {speed/1024/1024:.2f}MB/s\n"
            f"ETA: {eta}s"
        )
        await status_msg.edit_text(text)

    await client.download_media(
        message,
        file_name=file_path,
        progress=progress
    )
    return file_path


@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_upload(client: Client, query):
    kind = query.data.split("_", 1)[1]
    msg = query.message
    src = msg.reply_to_message

    if not src:
        return await msg.edit_text("❌ No source file found. Please reply to a file.")

    media = getattr(src, src.media.value) if src.media else None
    if media is None:
        return await msg.edit_text("❌ Unsupported media.")

    new_name = _parse_new_name(msg.text or msg.caption or "") or media.file_name or f"file.{('mp4' if kind=='video' else 'bin')}"
    new_name = _safe_name(new_name)

    status = await msg.edit_text("⚠️ **Please wait...**\nDownloading file...")
    dl_path = await chunked_download(client, src, new_name, status)

    ph_path = await _prepare_thumb(client, msg.chat.id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    try:
        if kind == "document":
            await client.send_document(
                msg.chat.id, document=dl_path, caption=new_name, thumb=ph_path,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, time.time()),
                reply_to_message_id=src.id
            )
        elif kind == "video":
            await client.send_video(
                msg.chat.id, video=dl_path, caption=new_name, thumb=ph_path,
                width=width, height=height, duration=duration, supports_streaming=True,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, time.time()),
                reply_to_message_id=src.id
            )
        else:
            await client.send_audio(
                msg.chat.id, audio=dl_path, caption=new_name, thumb=ph_path, duration=duration,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, time.time()),
                reply_to_message_id=src.id
            )
    except Exception as e:
        await status.edit(f"❌ Upload failed: `{e}`")
    else:
        await status.delete()
    finally:
        try:
            if os.path.exists(dl_path): os.remove(dl_path)
            if ph_path and os.path.exists(ph_path): os.remove(ph_path)
        except Exception:
            pass
