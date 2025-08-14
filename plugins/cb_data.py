from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import asyncio
import math
import os
import time

API_ID = 123456
API_HASH = "your_api_hash"
BOT_TOKEN = "your_bot_token"

app = Client("fast_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


# Progress bar helper
async def progress_bar(current, total, message, start_time, prefix):
    now = time.time()
    diff = now - start_time
    if diff == 0:
        diff = 1

    percentage = current * 100 / total
    speed = current / diff
    eta = (total - current) / speed

    bar_length = 10
    filled_length = int(bar_length * percentage / 100)
    bar = "◾️" * filled_length + "◽️" * (bar_length - filled_length)

    text = (
        f"{prefix}\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"{current / 1024 / 1024:.2f}MB of {total / 1024 / 1024:.2f}MB\n"
        f"Speed: {speed / 1024 / 1024:.2f}MB/s\n"
        f"ETA: {int(eta)}s"
    )
    try:
        await message.edit_text(text)
    except:
        pass


# Fast download with progress
async def fast_download(client, message, file_id, file_name):
    start_time = time.time()
    status = await message.reply("⚠️Please wait...\nStarting download...")

    path = await client.download_media(
        message=file_id,
        file_name=file_name,
        block=False,
        progress=progress_bar,
        progress_args=(status, start_time, "⬇️ Downloading...")
    )
    return path, status


@app.on_message(filters.document | filters.video)
async def ask_file_type(client, message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_id = message
    file_size = message.document.file_size if message.document else message.video.file_size

    await message.reply_text(
        f"Select the output file type\n"
        f"• File Name :- {file_name}\n"
        f"• Size :- {round(file_size / 1024 / 1024, 2)} MB",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 DOCUMENT", callback_data=f"doc|{file_name}")],
            [InlineKeyboardButton("🎬 VIDEO", callback_data=f"vid|{file_name}")]
        ])
    )

    # Save message object for callback
    message._bot_file_msg = file_id


@app.on_callback_query()
async def handle_buttons(client, callback_query):
    try:
        data = callback_query.data.split("|")
        file_type, file_name = data[0], data[1]

        # Get original file message object
        file_msg = callback_query.message.reply_to_message

        # Download
        path, status_msg = await fast_download(client, file_msg, file_msg, file_name)

        # Upload
        start_time = time.time()
        if file_type == "doc":
            await client.send_document(
                chat_id=callback_query.message.chat.id,
                document=path,
                progress=progress_bar,
                progress_args=(status_msg, start_time, "⬆️ Uploading...")
            )
        else:
            await client.send_video(
                chat_id=callback_query.message.chat.id,
                video=path,
                progress=progress_bar,
                progress_args=(status_msg, start_time, "⬆️ Uploading...")
            )

        await status_msg.delete()

        if os.path.exists(path):
            os.remove(path)

    except Exception as e:
        await callback_query.message.reply(f"❌ Error: {e}")


app.run()
