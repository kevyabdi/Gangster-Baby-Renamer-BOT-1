@Client.on_callback_query(filters.regex("upload"))
async def doc(bot, update):
    type_ = update.data.split("_")[1]

    # Hubi in message-ka uu leeyahay text cusub
    new_name = update.message.text
    if not new_name or ":-" not in new_name:
        await update.message.answer("❌ Fadlan isticmaal format sax ah: `SomeText:-FileName`")
        return

    new_filename = new_name.split(":-")[1].strip()
    file_path = f"downloads/{new_filename}"

    # Hubi in downloads folder jiro
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    # File-ka laga reply gareeyay
    file = update.message.reply_to_message
    if not file:
        await update.message.answer("❌ Fadlan reply garee file-ka aad rabto inaad upload sameyso.")
        return

    ms = await update.message.edit("⚠️__**Please wait...**__\n__Downloading file to server...__")
    c_time = time.time()

    # Download media
    try:
        path = await bot.download_media(
            message=file,
            progress=progress_for_pyrogram,
            progress_args=("\n⚠️__**Please wait...**__\n\n😈 **Hack in progress...**", ms, c_time)
        )
    except Exception as e:
        await ms.edit(f"❌ Error downloading file: {e}")
        return

    # Rename downloaded file
    splitpath = path.split("/downloads/")
    dow_file_name = splitpath[1]
    old_file_name = f"downloads/{dow_file_name}"
    os.rename(old_file_name, file_path)

    # Extract metadata
    duration = 0
    try:
        metadata = extractMetadata(createParser(file_path))
        if metadata.has("duration"):
            duration = metadata.get("duration").seconds
    except:
        pass

    # Caption & Thumbnail
    media = getattr(file, file.media.value)
    c_caption = await db.get_caption(update.message.chat.id)
    c_thumb = await db.get_thumbnail(update.message.chat.id)
    ph_path = None

    if c_caption:
        try:
            caption = c_caption.format(
                filename=new_filename,
                filesize=humanize.naturalsize(media.file_size),
                duration=convert(duration)
            )
        except Exception as e:
            await ms.edit(f"❌ Caption Error: ({e})")
            return
    else:
        caption = f"**{new_filename}**"

    if media.thumbs or c_thumb:
        try:
            if c_thumb:
                ph_path = await bot.download_media(c_thumb)
            else:
                ph_path = await bot.download_media(media.thumbs[0].file_id)

            img = Image.open(ph_path).convert("RGB")
            img.thumbnail((320, 320))
            img.save(ph_path, "JPEG")
        except:
            ph_path = None

    # Upload file
    await ms.edit("⚠️__**Please wait...**__\n__Processing file upload....__")
    c_time = time.time()

    try:
        if type_ == "document":
            await bot.send_document(
                update.message.chat.id,
                document=file_path,
                thumb=ph_path,
                caption=caption,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", ms, c_time)
            )
        elif type_ == "video":
            await bot.send_video(
                update.message.chat.id,
                video=file_path,
                caption=caption,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", ms, c_time)
            )
        elif type_ == "audio":
            await bot.send_audio(
                update.message.chat.id,
                audio=file_path,
                caption=caption,
                thumb=ph_path,
                duration=duration,
                progress=progress_for_pyrogram,
                progress_args=("⚠️__**Please wait...**__\n__Processing file upload....__", ms, c_time)
            )
        else:
            await ms.edit("❌ Unknown type specified!")
            return
    except Exception as e:
        await ms.edit(f"❌ Upload Error: {e}")
        return
    finally:
        # Cleanup
        if os.path.exists(file_path):
            os.remove(file_path)
        if ph_path and os.path.exists(ph_path):
            os.remove(ph_path)
        await ms.delete()
