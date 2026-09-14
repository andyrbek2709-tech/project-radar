#!/usr/bin/env python3
"""Превратить @username в численные -100… id.

Численные id устойчивее: username можно сменить, id — нет.

    cd backend
    python scripts/resolve_telegram_ids.py @ai_newz @seeallochnaya
    python scripts/resolve_telegram_ids.py --file channels.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Запуск как скрипта кладёт в sys.path каталог scripts/, а не корень backend/,
# поэтому `python scripts/<name>.py` падал с ModuleNotFoundError: No module named 'app'
# — и локально, и в контейнере (WORKDIR /app, PYTHONPATH не задан).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def resolve(names: list[str]) -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    from app.core.config import settings

    if not settings.TELEGRAM_SESSION_STRING:
        print("TELEGRAM_SESSION_STRING не задан. Сначала: python scripts/gen_telegram_session.py")
        raise SystemExit(1)

    client = TelegramClient(
        StringSession(settings.TELEGRAM_SESSION_STRING),
        settings.TELEGRAM_API_ID,
        settings.TELEGRAM_API_HASH,
    )

    resolved: list[str] = []
    async with client:
        for name in names:
            try:
                entity = await client.get_entity(name)
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {name}: {exc}", file=sys.stderr)
                continue

            entity_id = entity.id
            # Каналы и супергруппы в Bot API-формате имеют префикс -100.
            if type(entity).__name__ in {"Channel", "Chat"}:
                entity_id = int(f"-100{entity.id}")

            title = getattr(entity, "title", None) or getattr(entity, "username", name)
            print(f"  ✓ {title}: {entity_id}")
            resolved.append(str(entity_id))

    if resolved:
        print("\nTELEGRAM_SOURCE_IDS=" + ",".join(resolved))


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve Telegram channel ids")
    parser.add_argument("names", nargs="*", help="@username или ссылки t.me/...")
    parser.add_argument("--file", help="файл со списком, по одному в строке")
    args = parser.parse_args()

    names = list(args.names)
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            names.extend(line.strip() for line in fh if line.strip() and not line.startswith("#"))

    if not names:
        parser.error("нужен хотя бы один канал")

    asyncio.run(resolve(names))


if __name__ == "__main__":
    main()
