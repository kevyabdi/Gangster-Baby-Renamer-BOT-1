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

# Kayd ku meel gaar ah oo lagu xafido xogta file-ka userka
user_files = {}

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

@Client.on_message(filters.private & (filters.video | filters.document | filters.audio))
async def save_file_info(client, message):
    """Marka file la soo diro → keydi xogta ku meel gaarka ah"""
    media_type = "video" if message.video else "document" if message.document else "audio"
    media = getattr(message, media_type)
    user_files[message.from_user.id] = {
        "file_id": media.file_id,
        "file_name": media.file_name,
        "media_type": media_type
    }
    await message.reply_text(
        f"✅ File kaydsan: `{media.file_name}`\nRiix START RENAME si aad u magac beddesho.",
        quote=True
    )

@Client.on_callback_query(filters.regex("^upload_(document|video|audio)$"))
async def do_upload(client: Client, query):
    user_id = query.from_user.id
    if user_id not in user_files:
        return await query.message.edit_text("❌ No source file found. Fadlan soo dir file mar kale.")

    file_info = user_files[user_id]
    kind = file_info["media_type"]
    file_id = file_info["file_id"]
    orig_name = file_info["file_name"]

    # Magaca cusub ka soo qaad caption/text haddii uu jiro, haddii kale isticmaal kii hore
    new_name = orig_name
    if query.message.caption:
        new_name = _safe_name(query.message.caption)

    status = await query.message.edit_text("⚠️__**Please wait...**__\n__Downloading & Uploading file....__")
    c_time = time.time()
    dl_path = os.path.join(TMP_DIR, new_name)

    # Download
    await client.download_media(file_id, file_name=dl_path)

    # Thumbnail + metadata
    ph_path = await _prepare_thumb(client, user_id)
    width = height = duration = None
    if kind in ("video", "audio"):
        w, h, d = await _extract_meta(dl_path)
        width, height, duration = w, h, d

    try:
        if kind == "document":
            await client.send_document(
                query.message.chat.id, document=dl_path, caption=new_name, thumb=ph_path,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time)
            )
        elif kind == "video":
            await client.send_video(
                query.message.chat.id, video=dl_path, caption=new_name, thumb=ph_path,
                width=width, height=height, duration=duration, supports_streaming=True,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time)
            )
        else:
            await client.send_audio(
                query.message.chat.id, audio=dl_path, caption=new_name, thumb=ph_path, duration=duration,
                progress=progress_for_pyrogram, progress_args=("Uploading...", status, c_time)
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
