fromfrom helper.utils import progress_for_pyrogram, convert
from pyrogram import Client, filters
from pyrogram.types import (  InlineKeyboardButton, InlineKeyboardMarkup,ForceReply)
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from helper.database import db
import os 
import humanize
from PIL import Image
import time

@Client.on_callback_query(filters.regex('cancel'))
async def cancel(bot,update):
	try:
           await update.message.delete()
	except:
           return

@Client.on_callback_query(filters.regex('rename'))
async def rename(bot,update):
	user_id = update.message.chat.id
	date = update.message.date
	await update.message.delete()
	await update.message.reply_text("__𝙿𝚕𝚎𝚊𝚜𝚎 𝙴𝚗𝚝𝚎𝚛 𝙽𝚎𝚠 𝙵𝚒𝚕𝚎𝙽𝚊𝚖𝚎...__",	
	reply_to_message_id=update.message.reply_to_message.id,  
	reply_markup=ForceReply(True))
	
@Client.on_callback_query(filters.regex("upload"))
async def doc(bot,update):
     type = update.data.split("_")[1]
     new_name = update.message.text
     new_filename = new_name.split(":-")[1]
     file_path = f"downloads/{new_filename}"
     file = update.message.reply_to_message
     ms = await update.message.edit("⚠️__**Please wait...**__\n__Downloading file to my server...__")
     c_time = time.time()
     try:
     	path = await bot.download_media(message = file, progress=progress_for_pyrogram,progress_args=( "\n⚠️__**Please wait...**__\n\n😈 **Hack in progress...**",  ms, c_time   ))
     except Exception as e:
     	await ms.edit(e)
     	return 
     splitpath = path.split("/downloads/")
     dow_file_name = splitpath[1]
     old_file_name =f"downloads/{dow_file_name}"
     os.rename(old_file_name,file_path)
     duration = 0
     try:
        metadata = extractMetadata(createParser(file_path))
        if metadata.has("duration"):
           duration = metadata.get('duration').seconds
     except:
        pass
     user_id = int(update.message.chat.id) 
     ph_path = None 
     media = getattr(file, file.media.value)
     c_caption = await db.get_caption(update.message.chat.id)
     c_thumb = await db.get_thumbnail(update.message.chat.id)
     if c_caption:
         try:
             caption = c_caption.format(filename=new_filename, filesize=humanize.naturalsize(media.file_size), duration=convert(duration))
         except Exception as e:
             await ms.edit(text=f"Your caption Error unexpected keyword ●> ({e})")
             return 
     else:
         caption = f"**{new_filename}**"
     if (media.thumbs or c_thumb):
         try:
             if c_thumb:
                ph_path = await bot.download_media(c_thumb) 
             else:
                ph_path = await bot.download_media(media.thumbs[0].file_id)
             # Fix thumbnail processing
             with Image.open(ph_path) as img:
                 img = img.convert("RGB")
                 img = img.resize((320, 320))
                 img.save(ph_path, "JPEG")
         except Exception as e:
             print(f"Thumbnail error: {e}")
             ph_path = None
     await ms.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
     c_time = time.time() 
     
     # Check if file exists before upload
     if not os.path.exists(file_path):
         await ms.edit(f"❌ File not found: {file_path}")
         return
         
     file_size = os.path.getsize(file_path)
     print(f"Starting upload - Type: {type}, File: {file_path}, Size: {file_size}")
     
     # Check file size limits (Telegram: 2GB for documents, 50MB for videos)
     if type == "document" and file_size > 2 * 1024 * 1024 * 1024:  # 2GB
         await ms.edit("❌ File too large! Maximum size for documents is 2GB.")
         if os.path.exists(file_path):
             os.remove(file_path)
         if ph_path and os.path.exists(ph_path):
             os.remove(ph_path)
         return
     elif type in ["video", "audio"] and file_size > 50 * 1024 * 1024:  # 50MB
         await ms.edit(f"❌ File too large! Maximum size for {type} is 50MB.\n\n💡 Try sending as document instead.")
         if os.path.exists(file_path):
             os.remove(file_path)
         if ph_path and os.path.exists(ph_path):
             os.remove(ph_path)
         return
     
     # Add timeout for upload based on file size
     import asyncio
     timeout_seconds = min(max(file_size // (1024 * 1024) * 30, 300), 3600)  # 30s per MB, min 5min, max 1hour
     
     try:
        upload_task = None
        progress_text = f"⚠️__**Please wait...**__\n__Uploading {humanize.naturalsize(file_size)} file...__"
        
        if type == "document":
           print("Uploading as document...")
           upload_task = bot.send_document(
		    update.message.chat.id,
                    document=file_path,
                    thumb=ph_path, 
                    caption=caption, 
                    progress=progress_for_pyrogram,
                    progress_args=(progress_text, ms, c_time))
        elif type == "video": 
            print("Uploading as video...")
            upload_task = bot.send_video(
		    update.message.chat.id,
		    video=file_path,
		    caption=caption,
		    thumb=ph_path,
		    duration=duration,
		    progress=progress_for_pyrogram,
		    progress_args=(progress_text, ms, c_time))
        elif type == "audio": 
            print("Uploading as audio...")
            upload_task = bot.send_audio(
		    update.message.chat.id,
		    audio=file_path,
		    caption=caption,
		    thumb=ph_path,
		    duration=duration,
		    progress=progress_for_pyrogram,
		    progress_args=(progress_text, ms, c_time))
        
        # Wait for upload with dynamic timeout
        await asyncio.wait_for(upload_task, timeout=timeout_seconds)
        print("Upload completed successfully!")
        
     except asyncio.TimeoutError:
         await ms.edit(f"❌ Upload timeout after {timeout_seconds//60} minutes!\n\n💡 File might be too large or connection is slow.")
         print(f"Upload timed out after {timeout_seconds} seconds")
         if os.path.exists(file_path):
             os.remove(file_path)
         if ph_path and os.path.exists(ph_path):
             os.remove(ph_path)
         return
     except Exception as e: 
         error_msg = f"Upload Error: {str(e)}\n\nFile: {file_path}\nType: {type}\nSize: {humanize.naturalsize(file_size)}"
         await ms.edit(error_msg) 
         print(f"Upload failed: {e}")
         print(f"Error details - File exists: {os.path.exists(file_path)}, Thumb exists: {ph_path and os.path.exists(ph_path) if ph_path else 'No thumb'}")
         if os.path.exists(file_path):
             os.remove(file_path)
         if ph_path and os.path.exists(ph_path):
             os.remove(ph_path)
         return 
     await ms.delete() 
     if os.path.exists(file_path):
        os.remove(file_path) 
     if ph_path and os.path.exists(ph_path):
        os.remove(ph_path)