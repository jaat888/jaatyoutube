FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -U -r requirements.txt
COPY app.py .
# blitz.cloud: user 1000, root nahi, saari capabilities band -> HOME writable chahiye
RUN mkdir -p /app/home && chown -R 1000:1000 /app
ENV HOME=/app/home PORT=8080 PYTHONUNBUFFERED=1
USER 1000:1000
EXPOSE 8080
CMD ["sh","-c","gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app:app"]
