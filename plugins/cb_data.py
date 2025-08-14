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
    t_id = await db.get_thumbnail(user_id)
    if not t_id:
        return None
    path = os.path.join(TMP_DIR, f"thumb_{user_id}.jpg")
    try:
        await client.download_media(t_id, file_name=path)
        # convert to JPEG, <= 320px, <= 200KB
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
                cand = after.split(sep, 1)[1].strip()
                return _safe_name(cand)
    return None


# ✅ Cusbooneysi: Progress bar function cusub
async def custom_progress_bar(current, total, status_message, start_time):
    now = time.time()
    diff = now - start_time
    if diff == 0:
        diff = 0.1
    percentage = current * 100 / total
    speed = current / diff
    eta = (total - current) / speed
    elapsed_time = round(diff)

    # Samee progress bar
    filled_blocks = int(percentage // 10)
    bar = "[" + "◾" * filled_blocks + "◽" * (10 - filled_blocks) + "]"

    # Format size
    def human_readable_size(size):
        if size < 1024:
            return f"{size}B"
        elif size < 1024**2:
            return f"{size/1024:.2f}KB"
        elif size < 1024**3:
            return f"{size/(1024**2):.2f}MB"
        else:
            return f"{size/(1024**3):.2f}GB"

    text = (
        f"⚠️Please wait...\n\n"
        f"{bar} \n"
        f"{percentage:.1f}%\n"
        f"{human_readable_size(current)} of {human_readable_size(total)}\n"
        f"Speed: {human_readable_size(speed)}/s\n"
        f"ETA: {int(eta)}s"
    )

    try:
        await status_message.edit(text)
    except Exception:
        pass


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
    kind = query.data.split("_", 1)[1]
    msg = query.message
    src = msg.reply_to_message
    if not src:
        return await msg.edit_text("❌ Original file not found (reply missing). Try again.")
    media = getattr(src, src.media.value) if src.media else None
    if media is None:
        return await msg.edit_text("❌ Unsupported media.")

    new_name = _parse_new_name(msg.text or msg.caption or "")
    if not new_name:
        new_name = media.file_name or f"file.{('mp4' if kind=='video' else 'bin')}"
    new_name = _safe_name(new_name)

    status = await msg.edit_text("⚠️Please wait...\n\n[◽◽◽◽◽◽◽◽◽◽]\n0.0%")
    c_time = time.time()

    dl_path = await client.download_media(
        src,
        file_name=os.path.join(TMP_DIR, "src"),
        progress=custom_progress_bar,
        progress_args=(status, c_time)
    )

    base, ext = os.path.splitext(dl_path)
    if not os.path.splitext(new_name)[1] and ext:
        new_name = new_name + ext
    file_path = os.path.join(TMP_DIR, new_name)
    try:
        os.replace(dl_path, file_path)
    except Exception:
        file_path = dl_path

    ph_path = await _prepare_thumb(client, msg.chat.id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(file_path)
        width, height, duration = w, h, d

    try:
        await status.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
        if kind == "document":
            await client.send_document(
                msg.chat.id,
                document=file_path,
                caption=new_name,
                thumb=ph_path,
                progress=progress_for_pyrogram,
                progress_args=("⚠️Please wait...\n\nUploading...", status, c_time),
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
                progress_args=("⚠️Please wait...\n\nUploading...", status, c_time),
                reply_to_message_id=src.id
            )
        else:
            await client.send_audio(
                msg.chat.id,
                audio=file_path,
                caption=new_name,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️Please wait...\n\nUploading...", status, c_time),
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
