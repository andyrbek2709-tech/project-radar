"""FastAPI-приложение Project Radar."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import routes_findings, routes_ops, routes_projects
from app.core.config import settings
from app.core.db import SessionLocal, dispose_engines
from app.core.logging import configure_logging, get_logger

configure_logging("api")
log = get_logger("api")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info(
        "api_starting",
        env=settings.APP_ENV,
        timezone=settings.RADAR_TIMEZONE,
        embedding_provider=settings.EMBEDDING_PROVIDER,
        # Флаги, а не значения: секреты в логи не попадают.
        groq_configured=bool(settings.GROQ_API_KEY),
        github_configured=bool(settings.GITHUB_TOKEN),
        telegram_configured=bool(settings.TELEGRAM_SESSION_STRING),
        openai_deep_analysis=settings.OPENAI_DEEP_ANALYSIS_ENABLED,
    )
    yield
    await dispose_engines()
    log.info("api_stopped")


app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Персональный технический радар: найти → проверить → сопоставить с "
        "конкретным проектом → оценить реальную пользу → убрать дубли и шум."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_projects.router, prefix="/api")
app.include_router(routes_findings.router, prefix="/api")
app.include_router(routes_ops.router, prefix="/api")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Healthcheck для Railway. БД не трогает — должен отвечать всегда."""
    return {"status": "ok", "service": "api"}


@app.get("/health/deep", tags=["health"])
def health_deep() -> JSONResponse:
    """Проверка БД и наличия расширения pgvector."""
    checks: dict[str, object] = {"database": False, "pgvector": False}
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = True
            row = session.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
            checks["pgvector"] = row is not None
    except Exception as exc:  # noqa: BLE001
        checks["error"] = str(exc)[:200]

    healthy = bool(checks["database"] and checks["pgvector"])
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )


@app.get("/", tags=["health"])
def root() -> dict[str, str]:
    return {
        "name": settings.APP_NAME,
        "docs": "/docs",
        "health": "/health",
    }
