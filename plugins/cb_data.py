from helper.utils import progress_for_pyrogram
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from helper.database import db
from pyrogram.enums import MessageMediaType
import os
import time
from PIL import Image

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

def _safe_name(name: str) -> str:
    # Block path traversal and weird whitespace
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

async def _prepare_thumb(client: Client, user_id: int) -> str | None:
    # Download user's saved thumbnail (if any) and ensure it meets Telegram limits
    t_id = from io import BytesIO
    bio = BytesIO()
    await c.download_media(media, file_name=bio)
    bio.name = new_filename
        # convert to JPEG, <= 320px, <= 200KB
        im = Image.open(path).convert("RGB")
        im.thumbnail((320, 320))
        im.save(path, "JPEG", quality=85, optimize=True)
        if os.path.getsize(path) > 200_000:
            im.save(path, "JPEG", quality=70, optimize=True)
        return path
    except Exception:
        # If anything goes wrong, ignore custom thumb
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return None

def _parse_new_name(from_text: str) -> str | None:
    # Expects something like: **• File Name :-**```example.mp4```
    if not from_text:
        return None
    # find content between triple backticks or after "File Name :-"
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
                cand = after.split(sep, 1)[1].strip()
                return _safe_name(cand)
    return None

@Client.on_callback_query(filters.regex("^cancel$"))
async def cancel(bot, update):
    try:
        await update.message.delete()
    except Exception:
        pass

@Client.on_callback_query(filters.regex("^rename$"))
async def ask_new_name(client, query):
    m = query.message
    try:
        await m.edit_text(
            "**Send me the new file name with extension.**",
            reply_markup=ForceReply(True)
        )
    except Exception:
        await m.reply_text("**Send me the new file name with extension.**", reply_markup=ForceReply(True))

@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_upload(client: Client, query):
    kind = query.data.split("_", 1)[1]  # document|video|audio
    msg = query.message
    # The file to process is the message this message replied to
    src = msg.reply_to_message
    if not src:
        return await msg.edit_text("❌ Original file not found (reply missing). Try again.")
    media = getattr(src, src.media.value) if src.media else None
    if media is None:
        return await msg.edit_text("❌ Unsupported media.")
    # Determine target name from current message text
    new_name = _parse_new_name(msg.text or msg.caption or "")
    if not new_name:
        # Fallback to original media name
        new_name = media.file_name or f"file.{('mp4' if kind=='video' else 'bin')}"
    new_name = _safe_name(new_name)

    # Download
    status = await msg.edit_text("⚠️__**Please wait...**__\n__Downloading file....__")
    c_time = time.time()
    dl_path = await client.download_media(src, file_name=os.path.join(TMP_DIR, "src"))
    base, ext = os.path.splitext(dl_path)
    if not os.path.splitext(new_name)[1] and ext:
        new_name = new_name + ext
    file_path = os.path.join(TMP_DIR, new_name)
    try:
        os.replace(dl_path, file_path)
    except Exception:
        file_path = dl_path  # fallback

    # Prepare thumb & metadata
    ph_path = await _prepare_thumb(client, msg.chat.id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(file_path)
        width, height, duration = w, h, d

    # Upload
    try:
        await status.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
        if kind == "document":
            await client.send_document(
                msg.chat.id,
                document=file_path,
                caption=new_name,
                thumb=ph_path,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", status, c_time),
                reply_to_message_id=src.id
            )
        elif kind == "video":
            await client.send_video(
                msg.chat.id,
                video=file_path,
                caption=new_name,
                thumb=ph_path,
                width=width,
                height=height,
                duration=duration,
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", status, c_time),
                reply_to_message_id=src.id
            )
        else:  # audio
            await client.send_audio(
                msg.chat.id,
                audio=file_path,
                caption=new_name,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", status, c_time),
                reply_to_message_id=src.id
            )
    except Exception as e:
        try:
            await status.edit(f"❌ Upload failed: `{e}`")
        except Exception:
            pass
    else:
        try:
            await status.delete()
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
        try:
            if ph_path and os.path.exists(ph_path):
                os.remove(ph_path)
        except Exception:
            pass
