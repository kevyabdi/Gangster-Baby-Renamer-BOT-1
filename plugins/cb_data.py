import math
import time

# Progress function for Pyrogram
async def progress_for_pyrogram(current, total, message, start_time, process_name):
    now = time.time()
    diff = now - start_time

    if diff == 0:
        diff = 0.0001  # avoid division by zero

    # Percentage
    percentage = current * 100 / total

    # Progress bar (10 blocks)
    filled_length = int(percentage // 10)
    bar = "◾" * filled_length + "◽" * (10 - filled_length)

    # Speed MB/s
    speed = current / diff
    speed_mb = speed / 1024 / 1024

    # ETA calculation
    eta = (total - current) / speed
    eta_str = time.strftime("%Mm %Ss", time.gmtime(eta))

    # Elapsed time
    elapsed_str = time.strftime("%Mm %Ss", time.gmtime(diff))

    # Size in MB
    current_mb = current / 1024 / 1024
    total_mb = total / 1024 / 1024

    # Format text
    text = (
        f"⚠️ Please wait...\n\n"
        f"[{bar}] \n"
        f"{percentage:.1f}%\n"
        f"{current_mb:.2f}MB of {total_mb:.2f}MB\n"
        f"Speed: {speed_mb:.2f}MB/s\n"
        f"ETA: {eta_str}\n"
        f"Elapsed: {elapsed_str}"
    )

    try:
        await message.edit_text(text)
    except:
        pass
