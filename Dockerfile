FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt
COPY app.py .
ENV PORT=8080
CMD ["sh","-c","gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app:app"]
