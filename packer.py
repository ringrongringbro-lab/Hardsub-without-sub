import asyncio
import os
import re
import time

from pyrogram import Client
import pysubs2

import hvid

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
SECRET = os.environ["HVID_SECRET"].encode()
CHAT_ID = int(os.environ["CHAT_ID"])
VIDEO_MSG = int(os.environ["VIDEO_MSG"])
SUB_MSG = int(os.environ.get("SUB_MSG") or 0)
NEW_NAME = (os.environ.get("NEW_NAME") or "").strip()

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".hvid"}


def make_progress(status, label):
    state = {"t": 0.0}

    async def cb(cur, total):
        now = time.time()
        if now - state["t"] < 15:
            return
        state["t"] = now
        try:
            await status.edit_text(f"{label} {cur * 100 // max(total, 1)}%")
        except Exception:
            pass

    return cb


def clean_name(name, fallback):
    base = os.path.basename(name or fallback or "video")
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base).strip()
    stem, ext = os.path.splitext(base)
    if ext.lower() in VIDEO_EXT:
        base = stem
    return (base or "video")[:150] + ".hvid"


def to_ass(path):
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    if "[Script Info]" in text and "[Events]" in text:
        return text  # already ASS, keep it exactly as it is
    subs = pysubs2.SSAFile.from_string(text)
    return subs.to_string("ass")


async def main():
    app = Client("packer", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
    async with app:
        status = await app.send_message(CHAT_ID, "⏳ Process shuru ho gaya...")
        try:
            if not SECRET:
                raise RuntimeError("GitHub secrets me HVID_SECRET set nahi hai")
            vmsg = await app.get_messages(CHAT_ID, VIDEO_MSG)
            media = vmsg.video or vmsg.document
            if media is None:
                raise RuntimeError("Us message me video nahi mili")
            orig_name = media.file_name or "video.mp4"

            vpath = await app.download_media(
                vmsg, file_name="work/video_in", progress=make_progress(status, "📥 Download")
            )
            print("video downloaded", os.path.getsize(vpath), "bytes")

            ass_text = None
            if SUB_MSG:
                smsg = await app.get_messages(CHAT_ID, SUB_MSG)
                spath = await app.download_media(smsg, file_name="work/sub_in")
                ass_text = to_ass(spath)
                print("subtitle ready")

            await status.edit_text("🔐 Packing...")
            final_name = clean_name(NEW_NAME, orig_name)
            out_path = os.path.join("work", "out.hvid")
            hvid.pack(vpath, ass_text, out_path, SECRET)
            print("packed", os.path.getsize(out_path), "bytes")

            await app.send_document(
                CHAT_ID,
                out_path,
                file_name=final_name,
                caption="✅ Process Completed!\n" + final_name,
                progress=make_progress(status, "📤 Upload"),
            )
            await status.delete()
        except Exception as e:
            try:
                await status.edit_text("❌ Error: " + type(e).__name__ + ": " + str(e)[:200])
            except Exception:
                pass
            raise


if __name__ == "__main__":
    os.makedirs("work", exist_ok=True)
    asyncio.run(main())
