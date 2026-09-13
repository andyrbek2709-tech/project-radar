"""Тесты не требуют ни БД, ни ключей: проверяется чистая логика."""
from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "local")
os.environ.setdefault("EMBEDDING_PROVIDER", "null")
os.environ.setdefault("GROQ_ENABLED", "false")
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("LOG_JSON", "false")
