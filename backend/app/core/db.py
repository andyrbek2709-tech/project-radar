"""Движки и сессии SQLAlchemy.

Два движка по назначению:
  * async (asyncpg)  — FastAPI;
  * sync  (psycopg3) — Celery-таски и скрипты.

Celery не дружит с asyncio, поэтому весь pipeline работает на синхронном движке —
это сознательное решение, а не упрощение (см. ARCHITECTURE, «Принятые решения»).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ------------------------------------------------------------------ async
_async_engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    echo=False,
)


@event.listens_for(_async_engine.sync_engine, "connect")
def _register_vector_async(dbapi_connection, _record) -> None:  # noqa: ANN001
    """pgvector под asyncpg требует регистрации типа на каждом соединении."""
    try:
        from pgvector.asyncpg import register_vector

        dbapi_connection.run_async(register_vector)
    except Exception:  # pragma: no cover - расширение может быть ещё не создано
        pass


AsyncSessionLocal = async_sessionmaker(
    _async_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ------------------------------------------------------------------- sync
sync_engine: Engine = create_engine(
    settings.sync_database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=False,
)

SessionLocal = sessionmaker(bind=sync_engine, expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Транзакция для Celery-тасок и скриптов."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def dispose_engines() -> None:
    await _async_engine.dispose()
    sync_engine.dispose()


__all__ = [
    "AsyncSessionLocal",
    "SessionLocal",
    "get_async_session",
    "session_scope",
    "sync_engine",
    "dispose_engines",
]
