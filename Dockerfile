FROM python:3.10-slim

# تثبيت FFmpeg + Node.js (مهم لحل تشفير YouTube) + SSL certificates
RUN apt-get update && apt-get install -y \
    ffmpeg \
    ca-certificates \
    openssl \
    curl \
    nodejs \
    npm \
    && update-ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir certifi

# تحديث yt-dlp لآخر نسخة
RUN yt-dlp -U || true

# حذف oauth2 plugin عشان ميتفعلش تلقائياً
RUN find / -name "*oauth2*" -path "*/yt_dlp/*" -delete 2>/dev/null || true \
    && find / -name "*oauth*" -path "*/yt-dlp*" -delete 2>/dev/null || true

# حذف أي yt-dlp plugins folder فيه oauth
RUN rm -rf /root/.config/yt-dlp/plugins/ 2>/dev/null || true \
    && rm -rf /home/*/.config/yt-dlp/plugins/ 2>/dev/null || true \
    && rm -rf /app/.config/yt-dlp/plugins/ 2>/dev/null || true \
    && find / -name "*.py" -path "*yt*dlp*plugin*oauth*" -delete 2>/dev/null || true \
    && find / -name "ytdlp_plugins" -type d -exec rm -rf {} + 2>/dev/null || true

COPY . .

RUN mkdir -p /tmp/downloads && chmod 777 /tmp/downloads

# متغيرات بيئة لحل مشاكل SSL
ENV PYTHONHTTPSVERIFY=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
