FROM python:3.11-slim

# Install ffmpeg properly (this is where apt-get DOES work — inside Docker build)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 10000

CMD ["gunicorn", "app:app", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "0", \
     "--bind", "0.0.0.0:10000"]
