from helper.utils import progress_for_pyrogram
from pyrogram import Client, filters
from pyrogram.types import ForceReply
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
import os
import time
from PIL import Image

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# --------- Helper Functions ---------
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
        photos = await client.get_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            th_msg = await client.download_media(photos[0].file_id, file_name=path)
            im = Image.open(th_msg).convert("RGB")
            im.thumbnail((320, 320))
            im.save(path, "JPEG", quality=80, optimize=True)
            return path
    except Exception:
        pass
    return None

# --------- START RENAME handler ---------
@Client.on_callback_query(filters.regex("^rename$"))
async def start_rename(client, query):
    await query.message.delete()
    await client.send_message(
        query.from_user.id,
        "✏️ **Send me the new file name (with extension)**\nExample: `MyVideo.mp4`",
        reply_markup=ForceReply(selective=True)
    )

# --------- Receive New Name & Upload ---------
@Client.on_message(filters.reply & filters.private)
async def handle_new_name(client, message):
    if not message.reply_to_message:
        return

    src = message.reply_to_message.reply_to_message  # original media message
    if not src or not src.media:
        return await message.reply("❌ No source file found.")

    media = getattr(src, src.media.value)
    new_name = _safe_name(message.text or media.file_name or "file.bin")

    status = await message.reply("⚠️ **Please wait...**\nDownloading & Uploading file...")
    c_time = time.time()
    dl_path = os.path.join(TMP_DIR, new_name)

    await client.download_media(src, file_name=dl_path)

    ph_path = await _prepare_thumb(client, message.chat.id)
    width = height = duration = None
    if src.video or src.audio:
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    try:
        if src.document:
            await client.send_document(
                message.chat.id, document=dl_path, caption=new_name, thumb=ph_path,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time),
                reply_to_message_id=src.id
            )
        elif src.video:
            await client.send_video(
                message.chat.id, video=dl_path, caption=new_name, thumb=ph_path,
                width=width, height=height, duration=duration, supports_streaming=True,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time),
                reply_to_message_id=src.id
            )
        elif src.audio:
            await client.send_audio(
                message.chat.id, audio=dl_path, caption=new_name, thumb=ph_path, duration=duration,
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
