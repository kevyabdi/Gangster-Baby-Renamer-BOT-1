# plugins/cb_data.py — robust rename flow + faster IO for Render
from __future__ import annotations

import os
import time
from typing import Optional, Dict, Any

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from pyrogram.enums import MessageMediaType
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

from helper.utils import progress_for_pyrogram
from helper.database import db

TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)

# Kayd ku-meel-gaar ah oo lagu hayo xogta file-ka user-ka (Render memory)
user_files: Dict[int, Dict[str, Any]] = {}


def _safe_name(name: str) -> str:
    name = name.replace("\\", "/").split("/")[-1].strip()
    return name or "file"


def _parse_ext_from_media(message) -> str:
    if message.video: return ".mp4"
    if message.audio: return ".mp3"
    if message.document and message.document.file_name:
        _, ext = os.path.splitext(message.document.file_name)
        return ext or ".bin"
    return ".bin"


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
    """
    Doorbid thumbnail-ka kaydsan ee DB; haddii uusan jirin → None.
    (Waxaan uga tagnaa profile-photo grabbing si aanan ugu darin latency.)
    """
    t_id = await db.get_thumbnail(user_id)
    if not t_id:
        return None

    path = os.path.join(TMP_DIR, f"thumb_{user_id}.jpg")
    try:
        await client.download_media(t_id, file_name=path)
        im = Image.open(path).convert("RGB")
        im.thumbnail((320, 320))
        im.save(path, "JPEG", quality=85, optimize=True)
        # Ensure <= 200KB
        if os.path.getsize(path) > 200_000:
            im.save(path, "JPEG", quality=70, optimize=True)
        return path
    except Exception:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return None


# ====== Intake: marka user-ku soo diro file, kaydi xogta ======
@Client.on_message(filters.private & (filters.video | filters.document | filters.audio))
async def cache_incoming_file(client: Client, message):
    media_type = "video" if message.video else ("audio" if message.audio else "document")
    media = getattr(message, media_type)

    user_files[message.from_user.id] = {
        "file_id": media.file_id,
        "file_name": (media.file_name or f"file{_parse_ext_from_media(message)}"),
        "media_type": media_type,
        "src_chat_id": message.chat.id,
        "src_msg_id": message.id,
    }

    # Badhamo yaryar oo caawinaya
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏ START RENAME", callback_data="rename"),
         InlineKeyboardButton("✖ CANCEL", callback_data="cancel")],
        [InlineKeyboardButton("⬆ Upload as Document", callback_data="upload_document")],
        [InlineKeyboardButton("⬆ Upload as Video", callback_data="upload_video")],
        [InlineKeyboardButton("⬆ Upload as Audio", callback_data="upload_audio")]
    ])

    await message.reply_text(
        f"**What do you want me to do with this file.?**\n"
        f"**File Name : -** `{user_files[message.from_user.id]['file_name']}`\n"
        f"**File Size :-** {round((media.file_size or 0)/1024/1024, 2)} MB",
        reply_markup=kb,
        quote=True
    )


# ====== CANCEL ======
@Client.on_callback_query(filters.regex("^cancel$"))
async def cancel_action(client: Client, query):
    try:
        await query.message.delete()
    except Exception:
        pass


# ====== START RENAME: weydii magac cusub (ForceReply) ======
@Client.on_callback_query(filters.regex("^rename$"))
async def start_rename(client: Client, query):
    uid = query.from_user.id
    if uid not in user_files:
        return await query.message.edit_text("❌ No source file found. Please send the file again.")

    try:
        await query.message.delete()
    except Exception:
        pass

    await client.send_message(
        uid,
        "✏️ **Send me the new file name (with extension)**\nExample: `MyVideo.mp4`",
        reply_markup=ForceReply(selective=True)
    )


# ====== Receive new name (ForceReply) & upload ======
@Client.on_message(filters.private & filters.reply)
async def handle_new_name_and_upload(client: Client, message):
    uid = message.from_user.id
    if uid not in user_files:
        return await message.reply_text("❌ Source expired. Please send the file again.", quote=True)

    file_info = user_files[uid]
    kind = file_info["media_type"]
    file_id = file_info["file_id"]
    orig_name = file_info["file_name"]

    # Parse new name, ensure extension
    new_name = _safe_name(message.text or orig_name or "file.bin")
    root, ext = os.path.splitext(new_name)
    if not ext:
        # keep original ext if missing
        _, orig_ext = os.path.splitext(orig_name)
        new_name = root + (orig_ext or _parse_ext_from_media(type("X",(object,),{"video":kind=='video','audio':kind=='audio','document':kind=='document'})()))

    status = await message.reply_text("⚠️__**Please wait...**__\n__Downloading file....__", quote=True)
    c_time = time.time()

    # Fast path: download to deterministic path
    dl_path = os.path.join(TMP_DIR, new_name)
    try:
        # Pyrogram already streams efficiently; avoid heavy progress callbacks to speed up
        await client.download_media(file_id, file_name=dl_path)
    except Exception as e:
        return await status.edit(f"❌ Download failed: `{e}`")

    # Prepare thumb + meta
    ph_path = await _prepare_thumb(client, uid)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    # Upload
    try:
        await status.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
        if kind == "document":
            await client.send_document(
                message.chat.id,
                document=dl_path,
                caption=new_name,
                thumb=ph_path,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
            )
        elif kind == "video":
            await client.send_video(
                message.chat.id,
                video=dl_path,
                caption=new_name,
                thumb=ph_path,
                width=width,
                height=height,
                duration=duration,
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
            )
        else:  # audio
            await client.send_audio(
                message.chat.id,
                audio=dl_path,
                caption=new_name,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
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
            if os.path.exists(dl_path):
                os.remove(dl_path)
        except Exception:
            pass
        try:
            if ph_path and os.path.exists(ph_path):
                os.remove(ph_path)
        except Exception:
            pass


# ====== Direct upload buttons (upload_document/video/audio) without rename ======
@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_direct_upload(client: Client, query):
    uid = query.from_user.id
    if uid not in user_files:
        return await query.message.edit_text("❌ No source file found. Please send the file again.")

    file_info = user_files[uid]
    kind = query.data.split("_", 1)[1]  # user choice overrides cached kind when needed
    file_id = file_info["file_id"]
    new_name = file_info["file_name"]

    status = await query.message.edit_text("⚠️__**Please wait...**__\n__Downloading file....__")
    c_time = time.time()

    dl_path = os.path.join(TMP_DIR, new_name)
    try:
        await client.download_media(file_id, file_name=dl_path)
    except Exception as e:
        return await status.edit(f"❌ Download failed: `{e}`")

    ph_path = await _prepare_thumb(client, uid)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    try:
        await status.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
        if kind == "document":
            await client.send_document(
                query.message.chat.id,
                document=dl_path,
                caption=new_name,
                thumb=ph_path,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
            )
        elif kind == "video":
            await client.send_video(
                query.message.chat.id,
                video=dl_path,
                caption=new_name,
                thumb=ph_path,
                width=width,
                height=height,
                duration=duration,
                supports_streaming=True,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
            )
        else:
            await client.send_audio(
                query.message.chat.id,
                audio=dl_path,
                caption=new_name,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("Uploading...", status, c_time),
                reply_to_message_id=file_info["src_msg_id"]
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
            if os.path.exists(dl_path):
                os.remove(dl_path)
        except Exception:
            pass
        try:
            if ph_path and os.path.exists(ph_path):
                os.remove(ph_path)
        except Exception:
            pass
