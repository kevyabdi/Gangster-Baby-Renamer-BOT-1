# plugins/cb_data.py  — Render-safe, complete & tested for Pyrogram 2.x
# Replaces older buggy versions that had "t_id = from io import BytesIO" SyntaxError,
# and fixes upload-not-sent after 100% progress.

from __future__ import annotations

import os
import time
from typing import Optional, Tuple

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, ForceReply
from pyrogram.enums import MessageMediaType
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from PIL import Image

from helper.database import db
from helper.utils import progress_for_pyrogram

# Temp directory for processing files & thumbnails
TMP_DIR = "ren_tmp"
os.makedirs(TMP_DIR, exist_ok=True)


def _safe_name(name: str) -> str:
    """Sanitize filename against path traversal & weird whitespace."""
    name = name.replace("\\", "/").split("/")[-1].strip()
    return name or "file"


def _parse_new_name(from_text: Optional[str]) -> Optional[str]:
    """Extract target filename from bot's prompt text."""
    if not from_text:
        return None

    # Prefer content inside triple backticks
    if "```" in from_text:
        try:
            seg = from_text.split("```", 1)[1]
            return _safe_name(seg.split("```", 1)[0])
        except Exception:
            pass

    # Fallback: find text after "File Name"
    key = "File Name"
    if key in from_text:
        after = from_text.split(key, 1)[1]
        for sep in (":-", ":", "-"):
            if sep in after:
                return _safe_name(after.split(sep, 1)[1].strip())

    return None


async def _extract_meta(path: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (width, height, duration) if available, else Nones."""
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
    Download user's saved thumbnail (file_id stored in DB) and ensure it meets Telegram limits.
    Returns a local path or None if unavailable.
    """
    t_id = await db.get_thumbnail(user_id)
    if not t_id:
        return None

    path = os.path.join(TMP_DIR, f"thumb_{user_id}.jpg")
    try:
        await client.download_media(t_id, file_name=path)
        # Convert to JPEG, ensure <= 320px and <= 200KB
        im = Image.open(path).convert("RGB")
        im.thumbnail((320, 320))
        im.save(path, "JPEG", quality=85, optimize=True)
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


@Client.on_callback_query(filters.regex("^cancel$"))
async def cancel(bot: Client, update):
    try:
        await update.message.delete()
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^rename$"))
async def rename(bot: Client, update):
    """Ask user for the new name (with extension)."""
    try:
        await update.message.edit_text(
            "**Send me the new file name with extension.**",
            reply_markup=ForceReply(selective=True)
        )
    except Exception:
        await update.message.reply_text(
            "**Send me the new file name with extension.**",
            reply_markup=ForceReply(selective=True)
        )


@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_upload(client: Client, query):
    """
    Handles actual sending after rename choice.
    - Uses Render-friendly local file approach (no weird in-memory hacks).
    - Cleans up temp files.
    - Shows clear errors instead of hanging.
    """
    kind = query.data.split("_", 1)[1]  # document|video|audio
    msg = query.message

    # file to process is the message this message replied to
    src = msg.reply_to_message
    if not src:
        return await msg.edit_text("❌ Original file not found (reply missing). Try again.")

    media = getattr(src, src.media.value) if src.media else None
    if media is None:
        return await msg.edit_text("❌ Unsupported media.")

    # Determine new target name
    new_name = _parse_new_name(msg.text or msg.caption or "")
    if not new_name:
        # Fallback to original media name
        default_ext = "mp4" if kind == "video" else ("mp3" if kind == "audio" else "bin")
        new_name = media.file_name or f"file.{default_ext}"
    new_name = _safe_name(new_name)

    # 1) Download locally (stable for Render), then rename
    status = await msg.edit_text("⚠️__**Please wait...**__\n__Downloading file....__")
    c_time = time.time()

    # Use deterministic path to avoid collisions
    src_path = os.path.join(TMP_DIR, f"src_{src.id}")
    dl_path = await client.download_media(src, file_name=src_path)

    # Ensure target filename keeps original extension if user omitted it
    base, ext = os.path.splitext(dl_path)
    if not os.path.splitext(new_name)[1] and ext:
        new_name = new_name + ext

    file_path = os.path.join(TMP_DIR, new_name)
    try:
        if os.path.abspath(dl_path) != os.path.abspath(file_path):
            # Move / rename into final path
            if os.path.exists(file_path):
                os.remove(file_path)
            os.replace(dl_path, file_path)
    except Exception:
        # Fallback: just use the downloaded path
        file_path = dl_path

    # 2) Prepare thumbnail and metadata
    ph_path = await _prepare_thumb(client, msg.chat.id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(file_path)
        width, height, duration = w, h, d

    # 3) Upload
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
        # Show exact error instead of hanging
        try:
            await status.edit(f"❌ Upload failed: `{e}`")
        except Exception:
            pass
    else:
        # Clean progress message on success
        try:
            await status.delete()
        except Exception:
            pass
    finally:
        # Cleanup temp files
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
