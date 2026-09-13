"""Alembic environment.

Важно: pgvector.sqlalchemy импортируется здесь и подставляется в
ischema_names — иначе autogenerate не узнаёт тип VECTOR и генерирует
миграции, которые роняют векторные колонки.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Регистрация типа VECTOR для autogenerate.
from pgvector.sqlalchemy import Vector  # noqa: F401
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.models import Base  # импортирует все модели → наполняет metadata

try:
    postgresql.base.ischema_names["vector"] = Vector
    postgresql.base.ischema_names["halfvec"] = Vector
except Exception:  # pragma: no cover
    pass

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:  # noqa: ANN001
    """Не трогаем то, что создаёт само расширение pgvector."""
    if type_ == "table" and name in {"vector", "alembic_version_pgvector"}:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = settings.sync_database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
