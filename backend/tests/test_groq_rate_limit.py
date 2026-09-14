"""Поведение вокруг 429 от Groq.

На проде это выглядело так: сотни запросов подряд, каждый ловит 429 с
retry-after до 29 секунд, ни один не доходит, 435 находок не разобраны.
Пауза по retry-after должна быть общей на процесс — иначе следующий запрос
из того же воркера лезет в то же закрытое окно.
"""
from __future__ import annotations

import time

import pytest

from app.analysis.llm import GroqClient, LLMError, LLMRateLimited, _retry_after_seconds


@pytest.fixture(autouse=True)
def _reset_gate():
    """Состояние лимита живёт на классе — между тестами его надо гасить."""
    GroqClient._blocked_until = 0.0
    GroqClient._last_call_at = 0.0
    yield
    GroqClient._blocked_until = 0.0
    GroqClient._last_call_at = 0.0


class TestRetryAfter:
    def test_reads_whole_and_fractional_seconds(self):
        assert _retry_after_seconds({"retry-after": "20"}) == 20.0
        assert _retry_after_seconds({"retry-after": "29.5"}) == 29.5

    def test_missing_header_falls_back(self):
        assert _retry_after_seconds({}, default=60.0) == 60.0

    def test_garbage_does_not_raise(self):
        assert _retry_after_seconds({"retry-after": "Mon, 01 Jan 2027 00:00:00 GMT"}, 7.0) == 7.0

    def test_negative_is_clamped(self):
        assert _retry_after_seconds({"retry-after": "-5"}) == 0.0


class TestGate:
    def test_block_is_shared_between_instances(self):
        """Лимит выдаётся на аккаунт, а не на объект клиента."""
        GroqClient()._block_for(30.0)
        assert GroqClient._blocked_until > time.monotonic() + 29

        slept: list[float] = []
        original = time.sleep
        time.sleep = slept.append  # type: ignore[assignment]
        try:
            GroqClient()._wait_for_slot()
        finally:
            time.sleep = original

        assert slept and slept[0] > 29, "второй клиент обязан дождаться чужой паузы"

    def test_block_never_shrinks(self):
        GroqClient._block_for(60.0)
        far = GroqClient._blocked_until
        GroqClient._block_for(1.0)
        assert GroqClient._blocked_until == far

    def test_no_wait_when_window_is_open(self):
        slept: list[float] = []
        original = time.sleep
        time.sleep = slept.append  # type: ignore[assignment]
        try:
            GroqClient()._wait_for_slot()
        finally:
            time.sleep = original
        assert not slept


class TestException:
    def test_rate_limit_carries_retry_after_and_is_an_llm_error(self):
        exc = LLMRateLimited("Groq rate limit, retry-after=20s", retry_after=20.0)
        assert exc.retry_after == 20.0
        # Пайплайн ловит LLMError широко — новый тип не должен из него выпасть.
        assert isinstance(exc, LLMError)
