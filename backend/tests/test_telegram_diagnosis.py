"""«0 получено, 0 новых» обязано различаться по причине. БД не нужна.

История: 16.09.2026 таблица «Коллекторы» показывала telegram со статусом ok и
нулями каждые 15 минут. По этой строке нельзя было отличить работающий сбор,
которому нечего забрать, от канала на паузе после FloodWait и от канала,
который вообще не резолвится. Коммит 9523b45 закрыл только один случай —
когда упали ВСЕ каналы; при частичном сбое и при живом, но отстающем курсоре
строка по-прежнему выглядит как «сегодня ничего нового».
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.telegram_diagnosis import diagnose_source

NOW = datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc)

BASE = {
    "now": NOW,
    "is_active": True,
    "paused_until": None,
    "last_error": None,
    "last_run_at": NOW - timedelta(minutes=15),
    "items_collected": 10,
    "last_published_at": NOW - timedelta(hours=3),
    "history_limit": 50,
}


def test_healthy_channel() -> None:
    assert diagnose_source(**BASE) == "актуален, ждёт новых постов"


def test_paused_after_flood_wait_wins_over_everything() -> None:
    """Пауза важнее прочего: канал физически не читается, остальное вторично."""
    out = diagnose_source(**{**BASE, "paused_until": NOW + timedelta(hours=2)})
    assert "на паузе после FloodWait" in out


def test_expired_pause_is_not_a_pause() -> None:
    out = diagnose_source(**{**BASE, "paused_until": NOW - timedelta(minutes=1)})
    assert out == "актуален, ждёт новых постов"


def test_resolve_error_is_visible() -> None:
    out = diagnose_source(**{**BASE, "last_error": 'resolve failed: Cannot find any entity'})
    assert "resolve failed" in out


def test_never_read() -> None:
    assert diagnose_source(**{**BASE, "last_run_at": None}) == "ещё ни разу не читался"


def test_runs_but_stores_nothing() -> None:
    out = diagnose_source(**{**BASE, "items_collected": 0, "last_published_at": None})
    assert out == "прогоны идут, но не сохранено ни одного сообщения"


def test_catching_up_on_history_is_not_silence() -> None:
    """Главный случай: прогон отдаёт нули, а канал просто ещё не догнал историю.

    Курсор двигается вперёд по history_limit сообщений за прогон, поэтому у
    канала с длинной историей последнее собранное сообщение датировано прошлым
    годом — и это не «нечего забрать», а «до сегодняшних постов ещё далеко».
    """
    out = diagnose_source(**{**BASE, "last_published_at": NOW - timedelta(days=400)})
    assert "читает историю" in out
    assert "отставание 400 дн." in out
    assert "по 50 сообщений за прогон" in out


def test_weekend_silence_is_not_catching_up() -> None:
    """Сутки без постов — обычное молчание канала, а не отставание."""
    out = diagnose_source(**{**BASE, "last_published_at": NOW - timedelta(days=1)})
    assert out == "актуален, ждёт новых постов"
