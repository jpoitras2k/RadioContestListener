FROM python:3.12-slim

# Install system audio and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

ENV PORT=4173
ENV HOST=0.0.0.0
ENV AUDIO_SOURCE=stream

EXPOSE 4173

CMD ["python", "main.py"]
