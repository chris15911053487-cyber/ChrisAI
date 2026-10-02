# Chris Li · AI Agent 后端（FastAPI + DeepSeek + Skills）
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    SKILLS_BUILTIN_DIR=/skills/builtin \
    COURSES_DIR=/courses

WORKDIR /srv

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd -u 1000 -m app \
 && mkdir -p /data && chown app:app /data

COPY backend/app ./app
COPY skills/builtin /skills/builtin
COPY courses /courses

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
