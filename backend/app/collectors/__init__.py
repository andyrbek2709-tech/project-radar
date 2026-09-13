"""Коллекторы источников.

Архитектура расширяемая: чтобы добавить Habr, Reddit, HN или RSS, достаточно
реализовать протокол Source и зарегистрировать kind — pipeline ниже
нормализации трогать не нужно.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """Интерфейс источника. Реализации v1: telegram, github_search, github_watch."""

    kind: str

    def collect(self, session: Any) -> dict[str, Any]:
        """Забрать новое и сложить в raw_items. Возвращает статистику прогона."""
        ...


REGISTRY: dict[str, str] = {
    "telegram": "app.collectors.telegram_collector",
    "github_search": "app.collectors.github_radar",
    "github_watch": "app.collectors.github_radar",
    # Зарезервировано под v2:
    # "rss": "app.collectors.rss",
    # "hackernews": "app.collectors.hackernews",
    # "reddit": "app.collectors.reddit",
    # "habr": "app.collectors.habr",
}

__all__ = ["Source", "REGISTRY"]
