"""Структурированные логи. Секреты не попадают в вывод никогда."""
from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any

import structlog

from app.core.config import SECRET_ENV_MARKERS, settings

_REDACTED = "***REDACTED***"

# Значения секретов из окружения — вырезаются даже если попали в текст исключения.
_SECRET_VALUES: set[str] = set()

# Ключи, которые вырезаем по имени.
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|session|api[_-]?hash|"
    r"authorization|bearer|dsn|database_url|redis_url)",
    re.IGNORECASE,
)

# Телефон, Bearer-токены и telethon-подобные строки в свободном тексте.
_INLINE_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(gsk_|sk-|ghp_|github_pat_)[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)(postgres(?:ql)?|redis|rediss)://[^\s\"']+"),
]


def _collect_secret_values() -> None:
    for key, value in os.environ.items():
        if not value or len(value) < 8:
            continue
        if any(marker in key.upper() for marker in SECRET_ENV_MARKERS):
            _SECRET_VALUES.add(value)


def _scrub_text(text: str) -> str:
    for secret in _SECRET_VALUES:
        if secret and secret in text:
            text = text.replace(secret, _REDACTED)
    for pattern in _INLINE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = _REDACTED
            else:
                out[k] = _scrub(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        return type(value)(_scrub(v, depth + 1) for v in value)
    return value


def redact_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog-процессор: чистит и ключи, и значения, и вложенные структуры."""
    return _scrub(event_dict)  # type: ignore[return-value]


def configure_logging(service: str) -> None:
    _collect_secret_values()

    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    for noisy in ("httpx", "httpcore", "telethon", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.LOG_JSON
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service, env=settings.APP_ENV)


def get_logger(name: str) -> Any:
    """Именованные логгеры: collector, telegram, github, groq, openai, scheduler,
    decisions, errors."""
    return structlog.get_logger(name)


__all__ = ["configure_logging", "get_logger", "redact_processor"]
