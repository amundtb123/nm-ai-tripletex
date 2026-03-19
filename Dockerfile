# AI Accounting Agent — Cloud Run friendly image
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Cloud Run sets PORT; default 8080 for local `docker run`
CMD sh -c 'exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}'
