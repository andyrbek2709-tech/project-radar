"""Клиенты LLM: Groq (массовый слой) и OpenAI (deep analysis).

Ключевые факты, под которые написан код (проверено на первоисточниках, сентябрь 2026):
  * strict JSON-схему у Groq поддерживают ТОЛЬКО openai/gpt-oss-20b и openai/gpt-oss-120b;
    у остальных моделей strict молча игнорируется;
  * параметр называется max_completion_tokens, не max_tokens;
  * лимиты читаются из заголовков x-ratelimit-remaining-*, при 429 — retry-after;
  * эмбеддингов у Groq нет.

Работаем по HTTP напрямую: у Groq OpenAI-совместимый эндпоинт, и один httpx-клиент
на оба провайдера дешевле, чем два SDK со своими версиями.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.logging import get_logger

log_groq = get_logger("groq")
log_openai = get_logger("openai")

T = TypeVar("T", bound=BaseModel)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Модели Groq со strict-режимом. Остальные strict игнорируют — JSON не гарантирован.
GROQ_STRICT_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})

# USD за 1M токенов (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-20b": (0.075, 0.30),
    "openai/gpt-oss-120b": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    inp, out = PRICING.get(model, (0.0, 0.0))
    return prompt_tokens / 1_000_000 * inp + completion_tokens / 1_000_000 * out


class LLMError(RuntimeError):
    pass


class LLMSchemaError(LLMError):
    """Модель вернула то, что не легло в pydantic-схему."""


@dataclass(slots=True)
class LLMResult:
    parsed: Any
    model: str
    provider: str
    raw: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str | None = None
    rate_limit: dict[str, str] = field(default_factory=dict)


def _extract_json(text: str) -> dict[str, Any]:
    """Модель иногда оборачивает JSON в ```json ... ``` или добавляет префикс."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class GroqClient:
    """Первый дешёвый массовый слой. Финального решения о внедрении не принимает."""

    provider = "groq"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.GROQ_API_KEY
        self.model = model or settings.GROQ_MODEL
        self.escalation_model = settings.GROQ_ESCALATION_MODEL

    @property
    def enabled(self) -> bool:
        return bool(settings.GROQ_ENABLED and self.api_key)

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        model: str | None = None,
        temperature: float = 0.1,
        allow_escalation: bool = True,
    ) -> LLMResult:
        if not self.enabled:
            raise LLMError("Groq выключен или GROQ_API_KEY не задан")

        model = model or self.model
        try:
            return self._call(system, user, schema_model, schema_name, model, temperature)
        except LLMSchemaError as exc:
            if not allow_escalation or model == self.escalation_model:
                raise
            log_groq.warning(
                "groq_schema_error_escalating", model=model, to=self.escalation_model, error=str(exc)
            )
            return self._call(
                system, user, schema_model, schema_name, self.escalation_model, temperature
            )

    def _call(
        self,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        model: str,
        temperature: float,
    ) -> LLMResult:
        strict = model in GROQ_STRICT_MODELS
        if not strict:
            log_groq.warning("groq_strict_unsupported", model=model)

        response_format: dict[str, Any]
        if strict:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema_model.json_schema_for_llm(),  # type: ignore[attr-defined]
                    "strict": True,
                },
            }
        else:
            response_format = {"type": "json_object"}

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": settings.GROQ_MAX_COMPLETION_TOKENS,
            "response_format": response_format,
        }

        started = time.monotonic()
        with httpx.Client(timeout=settings.GROQ_TIMEOUT_SECONDS) as client:
            resp = client.post(
                GROQ_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        rate_limit = {
            k: v for k, v in resp.headers.items() if k.lower().startswith("x-ratelimit")
        }

        if resp.status_code == 429:
            retry_after = resp.headers.get("retry-after", "60")
            raise LLMError(f"Groq rate limit, retry-after={retry_after}s")
        if resp.status_code >= 400:
            raise LLMError(f"Groq HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        try:
            parsed = schema_model.model_validate(_extract_json(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMSchemaError(str(exc)[:500]) from exc

        return LLMResult(
            parsed=parsed,
            model=model,
            provider=self.provider,
            raw=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost(model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
            rate_limit=rate_limit,
        )


class OpenAIClient:
    """Deep analysis. По умолчанию выключен (OPENAI_DEEP_ANALYSIS_ENABLED=false)."""

    provider = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.OPENAI_MODEL
        self.url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions"

    @property
    def enabled(self) -> bool:
        return bool(settings.OPENAI_DEEP_ANALYSIS_ENABLED and self.api_key)

    def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema_model: type[T],
        schema_name: str,
        temperature: float = 0.2,
    ) -> LLMResult:
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY не задан")

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "schema": schema_model.json_schema_for_llm(),  # type: ignore[attr-defined]
                    "strict": True,
                },
            },
        }

        started = time.monotonic()
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.monotonic() - started) * 1000)

        if resp.status_code >= 400:
            raise LLMError(f"OpenAI HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage", {}) or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        try:
            parsed = schema_model.model_validate(_extract_json(content))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMSchemaError(str(exc)[:500]) from exc

        return LLMResult(
            parsed=parsed,
            model=self.model,
            provider=self.provider,
            raw=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost(self.model, prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
        )


__all__ = [
    "GroqClient",
    "OpenAIClient",
    "LLMResult",
    "LLMError",
    "LLMSchemaError",
    "estimate_cost",
    "PRICING",
    "GROQ_STRICT_MODELS",
]
