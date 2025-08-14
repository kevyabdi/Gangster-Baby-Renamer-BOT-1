from helper.utils import progress_for_pyrogram
from pyrogram import Client, filters
from pyrogram.types import ForceReply
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
import os
import time
import math
import aiohttp
import asyncio
from PIL import Image

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# ======= SAFE NAME =======
def _safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1].strip()
    return name or "file"

# ======= GET VIDEO META =======
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

# ======= PREPARE THUMB =======
async def _prepare_thumb(client: Client, user_id: int):
    path = os.path.join(TMP_DIR, f"{user_id}_thumb.jpg")
    try:
        photos = await client.get_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            th_msg = await client.download_media(photos[0].file_id, file_name=path)
            im = Image.open(th_msg).convert("RGB")
            im.thumbnail((320, 320))
            im.save(path, "JPEG", quality=80, optimize=True)
            return path
    except Exception:
        return None

# ======= PARSE NEW NAME =======
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

# ======= PARALLEL DOWNLOAD =======
async def parallel_download(url, dest, status_msg):
    async with aiohttp.ClientSession() as session:
        async with session.head(url) as resp:
            size = int(resp.headers.get('Content-Length', 0))
        chunk_size = 10 * 1024 * 1024  # 10MB
        chunks = math.ceil(size / chunk_size)

        async def fetch_part(idx):
            start = idx * chunk_size
            end = min(size - 1, start + chunk_size - 1)
            headers = {"Range": f"bytes={start}-{end}"}
            async with session.get(url, headers=headers) as r:
                with open(dest, 'r+b') as f:
                    f.seek(start)
                    while True:
                        block = await r.content.read(1024 * 512)
                        if not block:
                            break
                        f.write(block)

        # Prepare file
        with open(dest, 'wb') as f:
            f.truncate(size)

        tasks = [fetch_part(i) for i in range(chunks)]

        async def progress_monitor():
            while True:
                if os.path.exists(dest):
                    done = os.path.getsize(dest)
                    percent = done * 100 / size
                    bar_len = 10
                    filled = int(bar_len * percent / 100)
                    bar = "◾️" * filled + "◽️" * (bar_len - filled)
                    speed = done / (time.time() - start_time + 0.1)
                    eta = (size - done) / (speed + 0.1)
                    await status_msg.edit_text(
                        f"⚠️Please wait...\n\n[{bar}] \n"
                        f"{percent:.1f}%\n"
                        f"{done/1024/1024:.1f}MB of {size/1024/1024:.1f}MB\n"
                        f"Speed: {speed/1024/1024:.2f}MB/s\nETA: {int(eta)}s"
                    )
                await asyncio.sleep(1)

        start_time = time.time()
        monitor_task = asyncio.create_task(progress_monitor())
        await asyncio.gather(*tasks)
        monitor_task.cancel()

@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_upload(client: Client, query):
    kind = query.data.split("_", 1)[1]
    msg = query.message
    src = msg.reply_to_message
    if not src:
        return await msg.edit_text("❌ No source file found.")

    media = getattr(src, src.media.value) if src.media else None
    if media is None:
        return await msg.edit_text("❌ Unsupported media.")

    new_name = _parse_new_name(msg.text or msg.caption or "") or media.file_name or f"file.{('mp4' if kind=='video' else 'bin')}"
    new_name = _safe_name(new_name)

    status = await msg.edit_text("⚠️Please wait...\nDownloading file....")
    c_time = time.time()
    dl_path = os.path.join(TMP_DIR, new_name)

    # Use parallel download if it's from URL
    if getattr(media, "file_path", None):
        await parallel_download(media.file_path, dl_path, status)
    else:
        await client.download_media(src, file_name=dl_path,
                                    progress=progress_for_pyrogram,
                                    progress_args=("Downloading...", status, c_time))

    ph_path = await _prepare_thumb(client, msg.chat.id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    try:
        if kind == "document":
            await client.send_document(
                msg.chat.id, document=dl_path, caption=new_name, thumb=ph_path,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time),
                reply_to_message_id=src.id
            )
        elif kind == "video":
            await client.send_video(
                msg.chat.id, video=dl_path, caption=new_name, thumb=ph_path,
                width=width, height=height, duration=duration, supports_streaming=True,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time),
                reply_to_message_id=src.id
            )
        else:
            await client.send_audio(
                msg.chat.id, audio=dl_path, caption=new_name, thumb=ph_path, duration=duration,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time),
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
