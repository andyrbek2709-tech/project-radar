"""Проверка того, что секреты не утекают в логи. Требование, а не пожелание."""
from __future__ import annotations

from app.core.logging import _SECRET_VALUES, redact_processor


def test_redacts_by_key_name():
    event = {"event": "call", "api_key": "gsk_supersecretvalue123456", "ok": True}
    cleaned = redact_processor(None, "", dict(event))
    assert cleaned["api_key"] == "***REDACTED***"
    assert cleaned["ok"] is True


def test_redacts_nested():
    event = {"event": "x", "ctx": {"headers": {"authorization": "Bearer abcdef1234567890"}}}
    cleaned = redact_processor(None, "", event)
    assert cleaned["ctx"]["headers"]["authorization"] == "***REDACTED***"


def test_redacts_token_in_free_text():
    event = {"event": "err", "message": "failed with token gsk_abcdefghijklmnop12345"}
    cleaned = redact_processor(None, "", event)
    assert "gsk_abcdefghijklmnop12345" not in cleaned["message"]


def test_redacts_connection_strings():
    event = {"event": "db", "detail": "postgresql://user:password@host:5432/db failed"}
    cleaned = redact_processor(None, "", event)
    assert "password" not in cleaned["detail"]


def test_redacts_known_secret_values_anywhere():
    secret = "a-very-long-telegram-session-string-value"
    _SECRET_VALUES.add(secret)
    try:
        event = {"event": "x", "note": f"session={secret} used"}
        cleaned = redact_processor(None, "", event)
        assert secret not in cleaned["note"]
    finally:
        _SECRET_VALUES.discard(secret)


def test_keeps_normal_fields():
    event = {"event": "ok", "finding_id": "abc-123", "radar_score": 0.87, "count": 5}
    cleaned = redact_processor(None, "", dict(event))
    assert cleaned == event
