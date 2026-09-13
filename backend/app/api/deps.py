"""Зависимости API: сессия БД и HTTP Basic.

Пользователь один, поэтому multi-user и OAuth не нужны — но и голым наружу
сервис торчать не должен.
"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal

_basic = HTTPBasic(auto_error=False)


def get_db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def require_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)] = None,
) -> str:
    if not settings.AUTH_ENABLED:
        return "anonymous"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется авторизация",
            headers={"WWW-Authenticate": "Basic"},
        )

    # compare_digest — чтобы время ответа не подсказывало правильный префикс.
    # Байты, а не str: на не-ASCII строках compare_digest кидает TypeError,
    # и вместо честного 401 клиент получил бы 500.
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.ADMIN_USERNAME.encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.ADMIN_PASSWORD.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные учётные данные",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[str, Depends(require_auth)]

__all__ = ["get_db", "require_auth", "DbSession", "CurrentUser"]
