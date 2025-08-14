from helper.utils import progress_for_pyrogram, convert
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ForceReply
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from helper.database import db
import os
import humanize
from PIL import Image
import time

# Cancel callback
@Client.on_callback_query(filters.regex('cancel'))
async def cancel(bot, update):
    try:
        await update.message.delete()
    except:
        return

# Rename callback
@Client.on_callback_query(filters.regex('rename'))
async def rename(bot, update):
    await update.message.delete()
    await update.message.reply_text(
        "__Please enter NEW file name...__",
        reply_to_message_id=update.message.reply_to_message.id,
        reply_markup=ForceReply(True)
    )

# Upload callback
@Client.on_callback_query(filters.regex("upload"))
async def upload(bot, update):
    type_ = update.data.split("_")[1]

    # Halkan waxaa laga helayaa reply message-ka user-ka
    try:
        replied_msg = await bot.get_messages(update.message.chat.id, update.message.reply_to_message.message_id)
        new_name = replied_msg.text
        new_filename = new_name.split(":-")[1].strip()
    except Exception as e:
        await update.message.edit(f"❌ Error getting new filename: {e}")
        return

    file_path = f"/tmp/{new_filename}"  # Render host ku shaqeeyo /tmp

    # Faylkii la reply-gareeyay
    file_msg = update.message.reply_to_message

    ms = await update.message.edit("⚠️ Please wait...\nDownloading file to server...")
    c_time = time.time()

    # Download media
    try:
        path = await bot.download_media(
            message=file_msg,
            progress=progress_for_pyrogram,
            progress_args=("⚠️ Please wait...\nDownloading...", ms, c_time)
        )
    except Exception as e:
        await ms.edit(f"❌ Download error: {e}")
        return

    # Rename file
    try:
        os.rename(path, file_path)
    except Exception as e:
        await ms.edit(f"❌ File rename error: {e}")
        return

    # Duration metadata (videos/audio)
    duration = 0
    try:
        metadata = extractMetadata(createParser(file_path))
        if metadata.has("duration"):
            duration = metadata.get('duration').seconds
    except:
        pass

    # Caption and thumbnail
    ph_path = None
    c_caption = await db.get_caption(update.message.chat.id)
    c_thumb = await db.get_thumbnail(update.message.chat.id)

    # Caption formatting
    try:
        media = file_msg.document or file_msg.video or file_msg.audio
        if c_caption:
            caption = c_caption.format(
                filename=new_filename,
                filesize=humanize.naturalsize(media.file_size),
                duration=convert(duration)
            )
        else:
            caption = f"**{new_filename}**"
    except Exception as e:
        await ms.edit(f"❌ Caption error: {e}")
        return

    # Thumbnail handling
    try:
        if (getattr(media, 'thumb', None) or c_thumb):
            thumb_source = c_thumb if c_thumb else media.thumb.file_id
            ph_path = await bot.download_media(thumb_source)
            img = Image.open(ph_path).convert("RGB")
            img.thumbnail((320, 320))
            img.save(ph_path, "JPEG")
    except:
        ph_path = None  # Haddii thumbnail fail gareeyo, sii upload la'aan

    await ms.edit("⚠️ Processing file upload...")

    # Upload
    try:
        if type_ == "document":
            await bot.send_document(
                update.message.chat.id,
                document=file_path,
                thumb=ph_path,
                caption=caption,
                progress=progress_for_pyrogram,
                progress_args=("⚠️ Uploading...", ms, time.time())
            )
        elif type_ == "video":
            await bot.send_video(
                update.message.chat.id,
                video=file_path,
                caption=caption,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️ Uploading...", ms, time.time())
            )
        elif type_ == "audio":
            await bot.send_audio(
                update.message.chat.id,
                audio=file_path,
                caption=caption,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️ Uploading...", ms, time.time())
            )
    except Exception as e:
        await ms.edit(f"❌ Upload error: {e}")
        os.remove(file_path)
        if ph_path:
            os.remove(ph_path)
        return

    await ms.delete()
    os.remove(file_path)
    if ph_path:
        os.remove(ph_path)
