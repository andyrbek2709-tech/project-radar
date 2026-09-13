"""Telegram-коллектор на Telethon (user session, не Bot API).

Работает отдельным долгоживущим процессом (см. runner.py), а не внутри Celery:
Telethon держит долгую MTProto-сессию, и перелогин на каждый прогон таски —
прямой путь к нарастающему FloodWait по личному аккаунту.

Курсор — min_id в source_cursors. Уже обработанные сообщения не читаются повторно.
Медиа не скачиваются — только метаданные.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.source import CollectorRun, ProcessingStatus, RawItem, Source, SourceKind
from app.services.normalizer import extract_github_repos, extract_urls

log = get_logger("telegram")


class TelegramUnavailable(RuntimeError):
    pass


def _require_telethon():  # noqa: ANN202
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as exc:  # pragma: no cover
        raise TelegramUnavailable(
            "Telethon не установлен. pip install 'telethon>=1.45'"
        ) from exc
    return TelegramClient, StringSession


def build_client():  # noqa: ANN201
    """StringSession из env. Значение сессии не логируется никогда."""
    if not (settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH):
        raise TelegramUnavailable("TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы")
    if not settings.TELEGRAM_SESSION_STRING:
        raise TelegramUnavailable(
            "TELEGRAM_SESSION_STRING не задан. "
            "Сгенерируй локально: python scripts/gen_telegram_session.py"
        )

    TelegramClient, StringSession = _require_telethon()
    client = TelegramClient(
        StringSession(settings.TELEGRAM_SESSION_STRING),
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH,
    )
    client.flood_sleep_threshold = settings.TELEGRAM_FLOOD_SLEEP_THRESHOLD
    return client


def ensure_sources(session: Session) -> list[Source]:
    """Источники из TELEGRAM_SOURCE_IDS заводятся при первом запуске."""
    configured = settings.telegram_source_ids
    for raw in configured:
        existing = session.execute(
            select(Source).where(
                Source.kind == SourceKind.TELEGRAM, Source.external_id == raw
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Source(
                    kind=SourceKind.TELEGRAM,
                    external_id=raw,
                    title=raw,
                    is_active=True,
                    config={"history_limit": settings.TELEGRAM_HISTORY_LIMIT},
                )
            )
    session.flush()

    now = datetime.now(timezone.utc)
    active = session.execute(
        select(Source).where(
            Source.kind == SourceKind.TELEGRAM,
            Source.is_active.is_(True),
        )
    ).scalars().all()

    # Источники на паузе после длинного FloodWait пропускаем до истечения срока.
    return [s for s in active if s.paused_until is None or s.paused_until <= now]


def _parse_entity_id(external_id: str) -> Any:
    """`-1001234567890` → int, `@name` / `name` → строка для resolve."""
    cleaned = external_id.strip()
    if cleaned.lstrip("-").isdigit():
        return int(cleaned)
    return cleaned


def extract_message_payload(message: Any, chat_id: str) -> dict[str, Any]:
    """Всё нужное без скачивания вложений."""
    text = (getattr(message, "message", None) or getattr(message, "text", None) or "").strip()

    urls: list[str] = []
    for entity, value in (message.get_entities_text() if hasattr(message, "get_entities_text") else []):
        entity_name = type(entity).__name__
        if entity_name == "MessageEntityTextUrl" and getattr(entity, "url", None):
            urls.append(entity.url)
        elif entity_name == "MessageEntityUrl" and value:
            urls.append(value)
    for url in extract_urls(text):
        if url not in urls:
            urls.append(url)

    author: str | None = None
    from_id = getattr(message, "from_id", None)
    if from_id is not None:
        author = str(getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None) or from_id)

    reply_to = getattr(message, "reply_to", None)
    media = getattr(message, "media", None)
    fwd = getattr(message, "fwd_from", None)

    return {
        "chat_id": chat_id,
        "message_id": message.id,
        "text": text,
        "date": message.date.isoformat() if getattr(message, "date", None) else None,
        "author": author,
        "reply_to_msg_id": getattr(reply_to, "reply_to_msg_id", None) if reply_to else None,
        "reply_to_top_id": getattr(reply_to, "reply_to_top_id", None) if reply_to else None,
        "is_forward": fwd is not None,
        "forward_from": str(getattr(fwd, "from_name", "") or "") if fwd else None,
        "views": getattr(message, "views", None),
        "urls": urls,
        "github_repos": extract_github_repos(urls),
        # Метаданные вложений без самих файлов.
        "media_type": type(media).__name__ if media is not None else None,
        "has_media": media is not None,
    }


async def collect_source(client: Any, session: Session, source: Source) -> dict[str, int]:
    """Прочитать новое из одного канала. Курсор двигается только вперёд."""
    from telethon.errors import FloodWaitError

    cursor = source.cursor
    if cursor is None:
        from app.models.source import SourceCursor

        cursor = SourceCursor(source_id=source.id)
        session.add(cursor)
        session.flush()

    min_id = int(cursor.last_item_id) if (cursor.last_item_id or "").isdigit() else 0
    limit = int((source.config or {}).get("history_limit", settings.TELEGRAM_HISTORY_LIMIT))

    fetched = stored = 0
    max_seen = min_id

    try:
        entity = await client.get_entity(_parse_entity_id(source.external_id))
    except FloodWaitError as exc:
        _pause_source(source, exc.seconds)
        return {"fetched": 0, "stored": 0, "flood_wait": exc.seconds}
    except Exception as exc:  # noqa: BLE001
        source.last_error = f"resolve failed: {str(exc)[:200]}"
        log.warning("telegram_resolve_failed", source=source.external_id, error=str(exc)[:200])
        return {"fetched": 0, "stored": 0}

    if not source.title or source.title == source.external_id:
        source.title = getattr(entity, "title", None) or getattr(entity, "username", None) or source.external_id

    try:
        # reverse=True — хронологический порядок, min_id — не читаем прочитанное.
        async for message in client.iter_messages(
            entity, min_id=min_id, reverse=True, limit=limit
        ):
            fetched += 1
            max_seen = max(max_seen, message.id)

            payload = extract_message_payload(message, source.external_id)
            if not payload["text"] and not payload["urls"]:
                continue

            external_id = f"tg:{source.external_id}:{message.id}"
            exists = session.execute(
                select(RawItem.id).where(
                    RawItem.source_id == source.id, RawItem.external_id == external_id
                )
            ).first()
            if exists:
                continue

            session.add(
                RawItem(
                    source_id=source.id,
                    external_id=external_id,
                    kind="telegram_message",
                    payload=payload,
                    content_text=payload["text"],
                    urls=payload["urls"],
                    github_urls=[f"https://github.com/{r}" for r in payload["github_repos"]],
                    author=payload["author"],
                    published_at=message.date,
                    processing_status=ProcessingStatus.PENDING,
                )
            )
            stored += 1

    except FloodWaitError as exc:
        _pause_source(source, exc.seconds)
        log.warning("telegram_flood_wait", source=source.external_id, seconds=exc.seconds)
    except Exception as exc:  # noqa: BLE001
        source.last_error = str(exc)[:300]
        log.warning("telegram_collect_failed", source=source.external_id, error=str(exc)[:200])

    if max_seen > min_id:
        cursor.last_item_id = str(max_seen)
    source.last_run_at = datetime.now(timezone.utc)
    session.flush()

    return {"fetched": fetched, "stored": stored}


def _pause_source(source: Source, seconds: int) -> None:
    """Длинный FloodWait — пауза источнику, а не агрессивный ретрай.

    Ретраи после FloodWait превращают 7 секунд в часы. Лучше пропустить цикл.
    """
    if seconds > 300:
        source.paused_until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        source.last_error = f"FloodWait {seconds}s — источник приостановлен"


async def collect_all(session: Session) -> dict[str, Any]:
    """Один проход по всем активным Telegram-источникам."""
    if not settings.TELEGRAM_ENABLED:
        return {"status": "skipped", "reason": "disabled"}

    sources = ensure_sources(session)
    if not sources:
        return {"status": "ok", "fetched": 0, "stored": 0, "sources": 0}

    run = CollectorRun(collector="telegram", status="running")
    session.add(run)
    session.flush()

    total_fetched = total_stored = 0
    client = build_client()

    async with client:
        for source in sources:
            result = await collect_source(client, session, source)
            total_fetched += result.get("fetched", 0)
            total_stored += result.get("stored", 0)
            # Между каналами — небольшая пауза, чтобы не выглядеть скриптом.
            await asyncio.sleep(1.0)

    run.items_fetched = total_fetched
    run.items_new = total_stored
    run.status = "ok"
    run.finished_at = datetime.now(timezone.utc)

    log.info(
        "telegram_collect_done",
        sources=len(sources),
        fetched=total_fetched,
        stored=total_stored,
    )
    return {
        "status": "ok",
        "sources": len(sources),
        "fetched": total_fetched,
        "stored": total_stored,
    }


async def send_to_saved_messages(text: str) -> bool:
    """Доставка Daily Radar себе в «Избранное» — той же сессией, без отдельного бота."""
    if not settings.TELEGRAM_DELIVER_REPORT:
        return False
    try:
        client = build_client()
    except TelegramUnavailable as exc:
        log.warning("telegram_delivery_unavailable", error=str(exc))
        return False

    try:
        async with client:
            # 'me' — Saved Messages текущего пользователя.
            for chunk in _split_message(text):
                await client.send_message("me", chunk, link_preview=False)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram_delivery_failed", error=str(exc)[:200])
        return False


def _split_message(text: str, limit: int = 3900) -> list[str]:
    """Telegram режет сообщения длиннее 4096 символов."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > limit and current:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


__all__ = [
    "TelegramUnavailable",
    "build_client",
    "ensure_sources",
    "collect_source",
    "collect_all",
    "extract_message_payload",
    "send_to_saved_messages",
]
