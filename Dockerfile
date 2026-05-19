FROM python:3.10-slim

# تثبيت FFmpeg + تحديث SSL certificates عشان نحل مشاكل الاتصال
RUN apt-get update && apt-get install -y \
    ffmpeg \
    ca-certificates \
    openssl \
    curl \
    && update-ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir certifi

# تحديث yt-dlp لآخر نسخة دايماً عشان يتجاوز حمايات YouTube الجديدة
RUN yt-dlp -U || true

COPY . .

RUN mkdir -p /tmp/downloads && chmod 777 /tmp/downloads

# متغيرات بيئة لحل مشاكل SSL
ENV PYTHONHTTPSVERIFY=1
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
