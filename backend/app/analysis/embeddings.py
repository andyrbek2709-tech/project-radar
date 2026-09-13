"""Провайдеры эмбеддингов.

Решение (автономное): по умолчанию OpenAI `text-embedding-3-small` (1536).
Причина — на Railway RAM стоит ~$10/GB в месяц, а локальная multilingual-модель
съедает ~2 GB. $0.02 за 1M токенов дешевле держать, чем эти 2 GB.
Локальный провайдер реализован и включается одной переменной, если тексты
принципиально нельзя отдавать наружу.

Размерность фиксируется в миграции и менять её задним числом нельзя —
это пересчёт всех векторов.
"""
from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("embeddings")

# Цена OpenAI text-embedding-3-small, USD за 1M токенов.
OPENAI_EMBEDDING_PRICE_PER_1M = 0.02


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


class OpenAIEmbeddings:
    """HTTP напрямую — не тянем весь openai-SDK ради одного эндпоинта."""

    name = "openai"

    def __init__(self) -> None:
        self.dim = settings.EMBEDDING_DIM
        self.model = settings.EMBEDDING_MODEL
        self._api_key = settings.OPENAI_API_KEY
        self._url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/embeddings"
        self.last_tokens = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY не задан, а EMBEDDING_PROVIDER=openai")

        out: list[list[float]] = []
        self.last_tokens = 0
        batch = settings.EMBEDDING_BATCH_SIZE

        with httpx.Client(timeout=60.0) as client:
            for i in range(0, len(texts), batch):
                chunk = [t[:8000] if t else " " for t in texts[i : i + batch]]
                resp = client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={"model": self.model, "input": chunk},
                )
                resp.raise_for_status()
                data = resp.json()
                self.last_tokens += int(data.get("usage", {}).get("total_tokens", 0))
                for row in sorted(data["data"], key=lambda d: d["index"]):
                    out.append(_l2_normalize(row["embedding"]))
        return out

    def last_cost_usd(self) -> float:
        return self.last_tokens / 1_000_000 * OPENAI_EMBEDDING_PRICE_PER_1M


class LocalEmbeddings:
    """sentence-transformers. Модель грузится лениво — импорт не должен
    тащить torch в api-процесс, которому эмбеддинги не нужны."""

    name = "local"

    def __init__(self) -> None:
        self.dim = settings.EMBEDDING_DIM
        self.model_name = settings.EMBEDDING_MODEL
        self._model = None
        self.last_tokens = 0

    def _load(self):  # noqa: ANN202
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("loading_local_embedding_model", model=self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            [t[:8000] if t else " " for t in texts],
            batch_size=settings.EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vectors]

    def last_cost_usd(self) -> float:
        return 0.0


class NullEmbeddings:
    """Детерминированный псевдо-эмбеддинг из хеша.

    Не для качества — для того, чтобы pipeline, тесты и локальный запуск
    работали без единого ключа. Семантическая дедупликация с ним бесполезна,
    но точные ключи (github_id, url) продолжают ловить дубли.
    """

    name = "null"

    def __init__(self) -> None:
        self.dim = settings.EMBEDDING_DIM
        self.last_tokens = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            seed = (text or "").encode("utf-8")
            buf = bytearray()
            counter = 0
            while len(buf) < self.dim * 4:
                buf.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
                counter += 1
            floats = struct.unpack(f"{self.dim}f", bytes(buf[: self.dim * 4]))
            cleaned = [0.0 if (math.isnan(f) or math.isinf(f)) else f for f in floats]
            out.append(_l2_normalize(cleaned))
        return out

    def last_cost_usd(self) -> float:
        return 0.0


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is not None:
        return _provider

    choice = settings.EMBEDDING_PROVIDER
    if choice == "openai" and not settings.OPENAI_API_KEY:
        log.warning("embedding_fallback_to_null", reason="OPENAI_API_KEY отсутствует")
        choice = "null"

    if choice == "openai":
        _provider = OpenAIEmbeddings()
    elif choice == "local":
        _provider = LocalEmbeddings()
    else:
        _provider = NullEmbeddings()

    log.info("embedding_provider_selected", provider=_provider.name, dim=_provider.dim)
    return _provider


def embed_one(text: str) -> list[float]:
    return get_embedding_provider().embed([text])[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


__all__ = [
    "EmbeddingProvider",
    "OpenAIEmbeddings",
    "LocalEmbeddings",
    "NullEmbeddings",
    "get_embedding_provider",
    "embed_one",
    "cosine_similarity",
]
