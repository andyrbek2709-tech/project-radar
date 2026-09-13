# Один образ на api / worker / scheduler / telegram — различаются только командой.
# Это экономит время сборки на Railway: собирается один раз, запускается четырежды.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && apt-get purge -y --auto-remove build-essential

COPY . .

# Непривилегированный пользователь.
RUN useradd --create-home --shell /bin/false radar \
 && chown -R radar:radar /app
USER radar

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:${PORT:-8000}/health || exit 1

# Railway подставляет $PORT. Для worker/scheduler/telegram команда переопределяется.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
