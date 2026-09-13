"""Отдельный долгоживущий процесс сбора из Telegram.

Почему не Celery-таска: Telethon держит долгую MTProto-сессию. Логин на каждый
прогон таски Telegram расценивает как подозрительную активность и раздаёт
FloodWait по нарастающей — семь секунд превращаются в часы. Здесь одно живое
соединение, чтение по таймеру и аккуратная обработка FloodWait.

Запуск: python -m app.collectors.telegram_runner
"""
from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone

from app.core.config import settings
from app.core.db import session_scope
from app.core.logging import configure_logging, get_logger

configure_logging("telegram")
log = get_logger("telegram")

_stop = asyncio.Event()


def _handle_signal(*_: object) -> None:
    log.info("telegram_runner_stopping")
    _stop.set()


async def _tick() -> None:
    from app.collectors.telegram_collector import collect_all

    with session_scope() as session:
        result = await collect_all(session)
    log.info("telegram_tick", **result)


async def main() -> None:
    if not settings.TELEGRAM_ENABLED:
        log.info("telegram_runner_disabled")
        return

    from app.collectors.telegram_collector import TelegramUnavailable

    interval = max(60, settings.TELEGRAM_SCAN_INTERVAL_MINUTES * 60)
    log.info("telegram_runner_started", interval_seconds=interval)

    # Бэкофф на случай недоступности Telegram — растёт, но не бесконечно.
    backoff = interval

    while not _stop.is_set():
        started = datetime.now(timezone.utc)
        try:
            await _tick()
            backoff = interval
        except TelegramUnavailable as exc:
            log.warning("telegram_unavailable", error=str(exc))
            backoff = min(backoff * 2, 3600)
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram_tick_failed", error=str(exc)[:300])
            backoff = min(backoff * 2, 3600)

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        sleep_for = max(30.0, backoff - elapsed)
        try:
            await asyncio.wait_for(_stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            continue

    log.info("telegram_runner_stopped")


def run() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, AttributeError):
            # Windows не поддерживает add_signal_handler для SIGTERM.
            signal.signal(sig, _handle_signal)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()


if __name__ == "__main__":
    run()
