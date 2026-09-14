"""Конфигурация приложения. Все секреты — только из environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ---------------------------------------------------------------- app
    APP_NAME: str = "Project Radar"
    APP_ENV: Literal["local", "staging", "production"] = "local"
    APP_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me-in-env"
    TIMEZONE: str = "Asia/Aqtau"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # ---------------------------------------------------------- infra
    DATABASE_URL: str = "postgresql+asyncpg://radar:radar@localhost:5432/radar"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ------------------------------------------------------- telegram
    TELEGRAM_API_ID: int | None = None
    TELEGRAM_API_HASH: str | None = None
    TELEGRAM_PHONE: str | None = None
    TELEGRAM_SESSION_STRING: str | None = None
    TELEGRAM_SOURCE_IDS: str = ""
    TELEGRAM_ENABLED: bool = True
    TELEGRAM_HISTORY_LIMIT: int = 50
    TELEGRAM_FLOOD_SLEEP_THRESHOLD: int = 60
    TELEGRAM_DELIVER_REPORT: bool = True

    # --------------------------------------------------------- github
    GITHUB_TOKEN: str | None = None
    GITHUB_USERNAME: str | None = None
    GITHUB_SEARCH_ENABLED: bool = True
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_MIN_STARS: int = 40
    GITHUB_MAX_REPO_AGE_DAYS: int = 30
    GITHUB_STALE_PUSH_DAYS: int = 270
    GITHUB_README_MAX_CHARS: int = 12_000

    # ----------------------------------------------------------- groq
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GROQ_ESCALATION_MODEL: str = "openai/gpt-oss-120b"
    GROQ_ENABLED: bool = True
    GROQ_MAX_COMPLETION_TOKENS: int = 1200
    GROQ_TIMEOUT_SECONDS: int = 60
    # Пауза между запросами к Groq. 0 — без искусственной паузы.
    # Дефолт 2.0 держит порядка 30 запросов в минуту: это лимит бесплатного
    # тарифа. Пачка в 120 находок укладывается в ~4 минуты при интервале
    # пайплайна 10, так что запас есть. На платном тарифе можно ставить 0 —
    # но не раньше, чем в логах пропадут groq_rate_limited_waiting.
    GROQ_MIN_INTERVAL_SECONDS: float = 2.0
    # Сколько готовы ждать по retry-after, прежде чем признать попытку неудачной.
    # Больше — воркер стоит, меньше — находка уходит в повтор следующего прогона.
    GROQ_MAX_RATE_LIMIT_WAIT_SECONDS: float = 90.0

    # --------------------------------------------------------- openai
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4.1"
    OPENAI_DEEP_ANALYSIS_ENABLED: bool = False
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"

    # ----------------------------------------------------- embeddings
    EMBEDDING_PROVIDER: Literal["openai", "local", "null"] = "openai"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 1536
    EMBEDDING_BATCH_SIZE: int = 64

    # ---------------------------------------------------------- radar
    RADAR_ENABLED: bool = True
    RADAR_DAILY_TIME: str = "21:00"
    RADAR_TIMEZONE: str = "Asia/Aqtau"
    TELEGRAM_SCAN_INTERVAL_MINUTES: int = 15
    GITHUB_SCAN_INTERVAL_MINUTES: int = 120
    PIPELINE_INTERVAL_MINUTES: int = 10
    MAX_DEEP_ANALYSES_PER_DAY: int = 20
    MIN_RELEVANCE_SCORE: float = 0.65
    MIN_DEEP_ANALYSIS_SCORE: float = 0.70
    CRITICAL_SCORE: float = 0.80
    RECOMMENDED_SCORE: float = 0.60
    REVIEW_LATER_SCORE: float = 0.40
    DEDUP_COSINE_THRESHOLD: float = 0.08
    DUPLICATE_FEATURE_THRESHOLD: float = 0.85
    PIPELINE_BATCH_SIZE: int = 120

    # -------------------------------------------------------- web/api
    NEXT_PUBLIC_API_URL: str = "http://localhost:8000"
    CORS_ORIGINS: str = "http://localhost:3000"
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-me"
    AUTH_ENABLED: bool = True

    # ------------------------------------------------------ derived
    @field_validator("TELEGRAM_API_ID", mode="before")
    @classmethod
    def _api_id_or_none(cls, v: object) -> object:
        """Пустая строка или плейсхолдер (`PASTE_HERE_…`) → None, а не падение.

        Иначе `.env.example` с `TELEGRAM_API_ID=` и Railway с незаполненным
        плейсхолдером роняют весь сервис на импорте настроек — хотя Telegram
        в этот момент может быть и не нужен.
        """
        if isinstance(v, str):
            s = v.strip()
            return int(s) if s.isdigit() else None
        return v

    @field_validator("RADAR_DAILY_TIME")
    @classmethod
    def _check_time(cls, v: str) -> str:
        hh, _, mm = v.partition(":")
        if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError("RADAR_DAILY_TIME must be HH:MM")
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.RADAR_TIMEZONE or self.TIMEZONE)

    @property
    def radar_hour_minute(self) -> tuple[int, int]:
        hh, _, mm = self.RADAR_DAILY_TIME.partition(":")
        return int(hh), int(mm)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def telegram_source_ids(self) -> list[str]:
        return [s.strip() for s in self.TELEGRAM_SOURCE_IDS.split(",") if s.strip()]

    @property
    def async_database_url(self) -> str:
        """asyncpg для FastAPI.

        Railway отдаёт DATABASE_URL как `postgresql://…` — без указания драйвера.
        Если драйвер не подставить, SQLAlchemy возьмёт дефолтный psycopg2,
        которого в requirements нет.
        """
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if "+" not in url.split("://", 1)[0]:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def sync_database_url(self) -> str:
        """psycopg3 для Alembic и Celery-тасок.

        Строится из async-версии, а не из сырого DATABASE_URL: там уже
        нормализована схема и гарантированно проставлен драйвер.
        """
        return self.async_database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Ключи, значения которых никогда не должны попадать в логи.
SECRET_ENV_MARKERS: tuple[str, ...] = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "SESSION", "HASH",
    "DATABASE_URL", "REDIS_URL", "API_ID", "PHONE",
)

__all__ = ["Settings", "settings", "get_settings", "SECRET_ENV_MARKERS"]
