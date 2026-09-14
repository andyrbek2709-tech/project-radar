"""Строка сессии Telegram, побитая при копировании в переменные окружения.

Живой случай: девять прогонов подряд падали на «Incorrect padding» — Railway
принял строку без хвостовых '=', и коллектор молчал сутками при внешне
заполненных переменных.
"""
from __future__ import annotations

import base64
import struct

import pytest

from app.collectors.telegram_collector import _normalize_session_string


def _make_session_string() -> str:
    """Тот же формат, что отдаёт StringSession.save(): '1' + base64url."""
    payload = struct.pack(">B4sH", 2, bytes([149, 154, 175, 155]), 443) + b"k" * 256
    return "1" + base64.urlsafe_b64encode(payload).decode("ascii")


@pytest.fixture()
def valid() -> str:
    session = _make_session_string()
    assert session.endswith("="), "фикстура должна содержать padding — иначе тест ничего не проверяет"
    return session


def _decodes(session: str) -> bool:
    try:
        base64.urlsafe_b64decode(session[1:])
    except Exception:
        return False
    return True


def test_valid_string_survives_untouched(valid: str) -> None:
    assert _normalize_session_string(valid) == valid


def test_lost_padding_is_restored(valid: str) -> None:
    """Формы и шеллы съедают хвостовые '=' — байты сессии при этом целы."""
    stripped = valid.rstrip("=")
    assert not _decodes(stripped)
    assert _normalize_session_string(stripped) == valid


@pytest.mark.parametrize(
    "wrap",
    [
        lambda s: f"  {s}  ",
        lambda s: f"{s}\n",
        lambda s: f'"{s}"',
        lambda s: f"'{s}'",
        lambda s: s[:100] + "\n" + s[100:],  # перенос строки после копирования из терминала
    ],
    ids=["пробелы", "перевод строки", "двойные кавычки", "одинарные кавычки", "разрыв внутри"],
)
def test_copy_paste_damage_is_repaired(valid: str, wrap) -> None:
    assert _normalize_session_string(wrap(valid)) == valid


def test_truncated_string_is_not_silently_accepted(valid: str) -> None:
    """Реально обрезанную строку padding не чинит — распаковка обязана упасть дальше."""
    truncated = valid[: len(valid) // 2]
    normalized = _normalize_session_string(truncated)
    payload = base64.urlsafe_b64decode(normalized[1:])
    with pytest.raises(struct.error):
        struct.unpack(">B4sH", payload[:7]) and struct.unpack(">B4sH256s", payload)


def test_empty_string_returns_empty() -> None:
    assert _normalize_session_string("   ") == ""
