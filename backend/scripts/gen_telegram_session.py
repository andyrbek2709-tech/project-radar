#!/usr/bin/env python3
"""Сгенерировать TELEGRAM_SESSION_STRING.

Запускать ЛОКАЛЬНО и один раз: на сервере интерактивный логин невозможен,
а перелогины Telegram наказывает нарастающим FloodWait.

    cd backend
    python scripts/gen_telegram_session.py

Строка печатается один раз. Скопируй её в переменную окружения
TELEGRAM_SESSION_STRING на Railway и НИКОМУ не показывай: она даёт
полный доступ к аккаунту.
"""
from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        from telethon.sessions import StringSession
        from telethon.sync import TelegramClient
    except ImportError:
        print("Нужен Telethon: pip install 'telethon>=1.38'", file=sys.stderr)
        return 1

    api_id = os.environ.get("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH") or input("TELEGRAM_API_HASH: ").strip()

    if not (api_id and api_hash):
        print("api_id и api_hash обязательны. Получить: https://my.telegram.org/apps")
        return 1

    print("\nСейчас Telegram пришлёт код. Если включена 2FA — понадобится пароль.\n")

    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        me = client.get_me()
        session_string = client.session.save()

    print("\n" + "=" * 70)
    print(f"Авторизован как: {me.first_name} (@{me.username or 'без username'})")
    print("=" * 70)
    print("\nTELEGRAM_SESSION_STRING=" + session_string)
    print("\n" + "=" * 70)
    print("Эта строка = полный доступ к аккаунту.")
    print("Положи её в переменные окружения. В git она попасть не должна.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
