"""Почему Telegram-канал отдаёт нули — одна фраза на человеческом языке.

В таблице «Коллекторы» виден только итог прогона, и «0 получено, 0 новых» при
статусе ok означает сразу четыре разные вещи: канал на паузе после FloodWait,
канал не резолвится, курсор ещё ползёт по старой истории, либо всё прочитано и
новых постов действительно нет. Первые две — поломка, третья — ожидание,
четвёртая — норма, а выглядят они одинаково.

Отдельный модуль, а не функция внутри роутера: роутер тянет за собой
подключение к базе, и проверить формулировки без поднятой БД было бы нельзя.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Ниже этого порога отставание считается нормальным: канал мог просто молчать
# выходные. Две недели тишины при живом канале — уже не молчание, а догон.
STALE_AFTER = timedelta(days=2)


def diagnose_source(
    *,
    now: datetime,
    is_active: bool,
    paused_until: datetime | None,
    last_error: str | None,
    last_run_at: datetime | None,
    items_collected: int,
    last_published_at: datetime | None,
    history_limit: int,
) -> str:
    """Одна фраза о состоянии канала. Порядок проверок — от поломки к норме."""
    if paused_until is not None and paused_until > now:
        return f"на паузе после FloodWait до {paused_until.isoformat()}"
    if not is_active:
        return "источник выключен"
    if last_error:
        return f"последняя ошибка: {last_error[:200]}"
    if last_run_at is None:
        return "ещё ни разу не читался"
    if items_collected == 0:
        return "прогоны идут, но не сохранено ни одного сообщения"
    if last_published_at is not None and (now - last_published_at) > STALE_AFTER:
        behind = (now - last_published_at).days
        return (
            f"читает историю: последнее собранное от {last_published_at.date().isoformat()}, "
            f"отставание {behind} дн. Курсор идёт вперёд по {history_limit} сообщений за прогон"
        )
    return "актуален, ждёт новых постов"


__all__ = ["diagnose_source", "STALE_AFTER"]
