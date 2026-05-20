import os
import uuid
import glob
import ssl
import json
import time
import asyncio
import certifi
from typing import AsyncGenerator

os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
os.environ['YT_DLP_NO_OAUTH2'] = '1'
ssl._create_default_https_context = ssl.create_default_context

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import yt_dlp
import imageio_ffmpeg

app = FastAPI(title="Bedroo Downloader API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# تخزين حالة كل عملية تحميل في الميموري
download_sessions: dict = {}


def cleanup_file(filepath: str):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"[+] Deleted: {filepath}")
    except Exception as e:
        print(f"[-] Cleanup error: {e}")


def find_downloaded_file(file_id: str):
    matches = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
    matches = [f for f in matches if not f.endswith(('.part', '.ytdl'))]
    return matches[0] if matches else None


def make_safe_filename(title: str, ext: str) -> str:
    safe = "".join([c for c in title if c.isascii() and (c.isalnum() or c in " _-()[]")]).strip()
    safe = safe[:60].strip()
    if not safe:
        safe = f"video_{int(time.time())}"
    return f"{safe}.{ext}"


def get_ydl_opts(file_id: str, quality: str, progress_hook) -> dict:
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    opts = {
        'outtmpl': output_template,
        'ffmpeg_location': ffmpeg_path,
        'noplaylist': True,
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': BROWSER_UA,
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        },
        'cookiefile': None,
        'sleep_interval': 1,
        'max_sleep_interval': 3,
        'extractor_args': {
            'youtube': {
                'player_client': ['web'],
                'skip': ['oauth2', 'configs', 'webpage'],
            }
        },
        'retries': 5,
        'fragment_retries': 5,
        'ignoreerrors': False,
        'progress_hooks': [progress_hook],
    }

    if quality == "audio":
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif quality in ["1080", "720", "480", "360"]:
        opts['format'] = (
            f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/'
            f'bestvideo[height<={quality}]+bestaudio/'
            f'best[height<={quality}]/best'
        )
        opts['merge_output_format'] = 'mp4'
    else:
        opts['format'] = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            'bestvideo+bestaudio/best'
        )
        opts['merge_output_format'] = 'mp4'

    return opts


@app.get("/")
async def root():
    return {"status": "running", "message": "Bedroo Downloader API v3.0 ✅"}


@app.get("/progress/{session_id}")
async def progress_stream(session_id: str):
    """SSE endpoint — بيبعت progress updates للـ frontend"""

    async def event_generator() -> AsyncGenerator[str, None]:
        # انتظر لحد ما الـ session يتخلق
        for _ in range(20):
            if session_id in download_sessions:
                break
            await asyncio.sleep(0.3)

        last_sent = ""
        timeout = 300
        elapsed = 0

        while elapsed < timeout:
            session = download_sessions.get(session_id)
            if not session:
                await asyncio.sleep(0.5)
                elapsed += 0.5
                continue

            data = json.dumps(session, ensure_ascii=False)
            if data != last_sent:
                yield f"data: {data}\n\n"
                last_sent = data

            if session.get("status") in ("done", "error"):
                asyncio.create_task(cleanup_session(session_id))
                break

            await asyncio.sleep(0.4)
            elapsed += 0.4

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


async def cleanup_session(session_id: str):
    await asyncio.sleep(30)
    download_sessions.pop(session_id, None)


@app.get("/download")
async def download_media(url: str, background_tasks: BackgroundTasks,
                         quality: str = "best", session_id: str = ""):

    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="رابط غير صحيح")

    file_id = str(uuid.uuid4())
    if not session_id:
        session_id = file_id

    download_sessions[session_id] = {
        "status": "starting",
        "percent": 0,
        "speed": "",
        "eta": "",
        "filename": "",
    }

    def progress_hook(d):
        if d['status'] == 'downloading':
            raw_percent = d.get('_percent_str', '0%').strip()
            try:
                percent = float(raw_percent.replace('%', '').strip())
            except:
                percent = 0

            download_sessions[session_id] = {
                "status": "downloading",
                "percent": round(percent, 1),
                "speed": d.get('_speed_str', '').strip(),
                "eta": d.get('_eta_str', '').strip(),
                "filename": d.get('filename', ''),
            }

        elif d['status'] == 'finished':
            download_sessions[session_id] = {
                "status": "processing",
                "percent": 99,
                "speed": "",
                "eta": "جاري المعالجة...",
                "filename": d.get('filename', ''),
            }

    try:
        ydl_opts = get_ydl_opts(file_id, quality, progress_hook)
        loop = asyncio.get_event_loop()

        def run_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        print(f"[*] Download: {url} | quality={quality} | session={session_id}")
        info = await loop.run_in_executor(None, run_download)

        actual_filepath = find_downloaded_file(file_id)
        if not actual_filepath:
            raise Exception("الملف لم يُوجد بعد التحميل")

        video_title = info.get('title', 'video') if info else 'video'
        final_ext = actual_filepath.rsplit('.', 1)[-1]
        download_filename = make_safe_filename(video_title, final_ext)
        file_size = os.path.getsize(actual_filepath)

        print(f"[+] Ready: {download_filename} ({file_size // 1024} KB)")

        download_sessions[session_id] = {
            "status": "done",
            "percent": 100,
            "speed": "",
            "eta": "",
            "filename": download_filename,
        }

        background_tasks.add_task(cleanup_file, actual_filepath)

        return FileResponse(
            path=actual_filepath,
            filename=download_filename,
            media_type='application/octet-stream',
            headers={
                "Content-Disposition": f'attachment; filename="{download_filename}"',
                "X-File-Size": str(file_size),
            }
        )

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        if "Sign in" in err or "login" in err.lower():
            detail = "هذا الفيديو خاص أو يتطلب تسجيل دخول"
        elif "Private video" in err:
            detail = "الفيديو خاص ولا يمكن تحميله"
        elif "not available" in err.lower():
            detail = "الفيديو غير متاح في منطقتك أو تم حذفه"
        elif "Unsupported URL" in err:
            detail = "هذا الموقع غير مدعوم"
        elif "429" in err or "Too Many Requests" in err:
            detail = "السيرفر محجوب مؤقتاً — انتظر دقيقة وحاول مرة ثانية"
        else:
            detail = f"فشل التحميل: {err}"

        download_sessions[session_id] = {"status": "error", "percent": 0, "speed": "", "eta": "", "filename": ""}
        raise HTTPException(status_code=400, detail=detail)

    except Exception as e:
        print(f"[-] Error: {e}")
        download_sessions[session_id] = {"status": "error", "percent": 0, "speed": "", "eta": "", "filename": ""}
        raise HTTPException(status_code=500, detail=f"خطأ غير متوقع: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)
