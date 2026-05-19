import os
import uuid
import glob
import ssl
import certifi

# ============================================================
# حل مشكلة SSL على سيرفرات Hugging Face
# ============================================================
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
ssl._create_default_https_context = ssl.create_default_context

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import yt_dlp
import imageio_ffmpeg

app = FastAPI(title="Bedroo Downloader API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "/tmp/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ============================================================
# User-Agent واقعي زي متصفح عادي عشان نتفادى الحجب
# ============================================================
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

def cleanup_file(filepath: str):
    """تمسح الفيديو من السيرفر بعد ما المستخدم يحمله"""
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"[+] Deleted: {filepath}")
    except Exception as e:
        print(f"[-] Cleanup error: {e}")

def cleanup_old_files():
    """تمسح أي ملفات قديمة من تحميلات فاشلة سابقة"""
    try:
        for f in glob.glob(os.path.join(DOWNLOAD_DIR, "*")):
            os.remove(f)
    except:
        pass

def find_downloaded_file(file_id: str) -> str | None:
    """يبحث عن الملف المحمل بأي امتداد"""
    matches = glob.glob(os.path.join(DOWNLOAD_DIR, f"{file_id}.*"))
    # يتجاهل ملفات yt-dlp المؤقتة (.part, .ytdl)
    matches = [f for f in matches if not f.endswith(('.part', '.ytdl'))]
    return matches[0] if matches else None

def make_safe_filename(title: str, ext: str) -> str:
    """ينظف اسم الملف من الرموز غير المسموح بيها"""
    # بيحتفظ بالحروف العربية والإنجليزية والأرقام
    safe = "".join([c for c in title if c.isalnum() or c in " _-()" or ord(c) > 127]).strip()
    safe = safe[:80]  # حد أقصى 80 حرف عشان المتصفح ميتلخبطش
    return f"{safe or 'video'}.{ext}"

@app.get("/")
async def root():
    return {"status": "running", "message": "Bedroo Downloader API v2.0 ✅"}

@app.get("/download")
async def download_media(url: str, background_tasks: BackgroundTasks, quality: str = "best"):

    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="رابط غير صحيح، تأكد أنه يبدأ بـ http")

    file_id = str(uuid.uuid4())
    output_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    # ============================================================
    # الإعدادات الأساسية المحسّنة
    # ============================================================
    ydl_opts = {
        'outtmpl': output_template,
        'ffmpeg_location': ffmpeg_path,
        'noplaylist': True,
        'socket_timeout': 30,

        # User-Agent واقعي
        'http_headers': {
            'User-Agent': BROWSER_UA,
            'Accept-Language': 'en-US,en;q=0.9,ar;q=0.8',
        },

        # Cookies لو موجودة (مهمة لـ Instagram وFacebook الخاص)
        'cookiefile': './cookies.txt' if os.path.exists('./cookies.txt') else None,

        # تجنب الحجب
        'sleep_interval': 1,
        'max_sleep_interval': 3,

        # YouTube: جرب أكتر من client عشان تتجاوز الحجب
        'extractor_args': {
            'youtube': {
                'player_client': ['web', 'android', 'ios'],
                'skip': ['hls', 'dash'],
            }
        },

        # إعادة المحاولة تلقائياً لو في مشكلة شبكة
        'retries': 5,
        'fragment_retries': 5,

        # تجاهل الأخطاء البسيطة واكمل
        'ignoreerrors': False,
        'no_warnings': False,
    }

    # ============================================================
    # تحديد الجودة
    # ============================================================
    if quality == "audio":
        # أفضل صوت متاح وتحويله لـ mp3
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]

    elif quality in ["1080", "720", "480", "360"]:
        # الجودة المطلوبة أو أقل منها لو مش متاحة، مع fallback للـ best
        ydl_opts['format'] = (
            f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/'
            f'bestvideo[height<={quality}]+bestaudio/'
            f'best[height<={quality}]/best'
        )
        ydl_opts['merge_output_format'] = 'mp4'

    else:
        # أعلى جودة مع تفضيل mp4 عشان يشتغل على كل الأجهزة
        ydl_opts['format'] = (
            'bestvideo[ext=mp4]+bestaudio[ext=m4a]/'
            'bestvideo+bestaudio/best'
        )
        ydl_opts['merge_output_format'] = 'mp4'

    # ============================================================
    # التحميل الفعلي
    # ============================================================
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print(f"[*] Starting download: {url} | quality={quality}")
            info = ydl.extract_info(url, download=True)

            # البحث عن الملف المحمل
            actual_filepath = find_downloaded_file(file_id)

            if not actual_filepath:
                raise Exception("اكتمل التحميل لكن الملف لم يُوجد — حاول مرة ثانية")

            # تجهيز اسم الملف للمستخدم
            video_title = info.get('title', 'video') if info else 'video'
            final_ext = actual_filepath.rsplit('.', 1)[-1]
            download_filename = make_safe_filename(video_title, final_ext)

            print(f"[+] Ready to send: {download_filename} ({os.path.getsize(actual_filepath) // 1024} KB)")

        # مسح الملف من السيرفر بعد الإرسال
        background_tasks.add_task(cleanup_file, actual_filepath)

        return FileResponse(
            path=actual_filepath,
            filename=download_filename,
            media_type='application/octet-stream',
            headers={
                "Content-Disposition": f'attachment; filename="{download_filename}"',
                "X-File-Size": str(os.path.getsize(actual_filepath)),
            }
        )

    except yt_dlp.utils.DownloadError as e:
        err = str(e)
        # رسائل خطأ واضحة حسب السبب
        if "Sign in" in err or "login" in err.lower():
            detail = "هذا الفيديو خاص أو يتطلب تسجيل دخول — جرب رابط فيديو عام"
        elif "Private video" in err:
            detail = "الفيديو خاص ولا يمكن تحميله"
        elif "not available" in err.lower():
            detail = "الفيديو غير متاح في منطقتك أو تم حذفه"
        elif "Unsupported URL" in err:
            detail = "هذا الموقع غير مدعوم"
        elif "429" in err or "Too Many Requests" in err:
            detail = "السيرفر محجوب مؤقتاً من YouTube — انتظر دقيقة وحاول مرة ثانية"
        else:
            detail = f"فشل التحميل: {err}"
        raise HTTPException(status_code=400, detail=detail)

    except Exception as e:
        print(f"[-] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"خطأ غير متوقع: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 7860))
    uvicorn.run(app, host="0.0.0.0", port=port)