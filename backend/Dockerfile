# Один образ на api / worker / scheduler / telegram — различаются только командой.
# Это экономит время сборки на Railway: собирается один раз, запускается четырежды.
#
# КОНТЕКСТ СБОРКИ — КОРЕНЬ РЕПОЗИТОРИЯ, не backend/. Railway собирает с Root
# Directory "/" и dockerfilePath из railway.json; docker-compose повторяет это
# через context: . — поэтому все COPY идут с префиксом backend/.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Системных пакетов не нужно: psycopg[binary], asyncpg и uvicorn[standard]
# приезжают готовыми wheel'ами для cp312. apt-get в билдере Railway
# нестабилен (падает на резолве зеркал) — и это единственная причина,
# по которой сборка вообще могла упасть.
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# Непривилегированный пользователь.
RUN useradd --create-home --shell /bin/false radar \
 && chown -R radar:radar /app
USER radar

EXPOSE 8000

# Railway использует healthcheckPath из railway.json; этот — для docker-compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4).status==200 else 1)" || exit 1

# Railway подставляет $PORT. Для worker/scheduler/telegram команда переопределяется.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
