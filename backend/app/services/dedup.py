"""Дедупликация.

Порядок — от дешёвого к дорогому:
  1. github_id         (стабилен при переименовании репозитория)
  2. github_full_name
  3. normalized_url
  4. url
  5. title_hash
  6. семантика через pgvector (cosine)

Находка из Telegram и из GitHub Radar — ОДНА сущность с несколькими источниками.
Повторное появление повышает seen_count и, через него, confidence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.finding import Finding, FindingKey, FindingSource, KeyType
from app.services.normalizer import NormalizedItem

log = get_logger("dedup")


@dataclass(slots=True)
class DedupResult:
    finding: Finding
    is_new: bool
    method: str
    score: float | None = None


def build_keys(item: NormalizedItem, github_id: int | None = None) -> list[tuple[str, str]]:
    """Ключи в порядке убывания надёжности."""
    keys: list[tuple[str, str]] = []
    if github_id:
        keys.append((KeyType.GITHUB_ID, str(github_id)))
    if item.github_full_name:
        keys.append((KeyType.GITHUB_FULL_NAME, item.github_full_name.lower()))
    if item.normalized_url:
        keys.append((KeyType.NORMALIZED_URL, item.normalized_url))
    if item.url and item.url != item.normalized_url:
        keys.append((KeyType.URL, item.url))
    if item.title:
        keys.append((KeyType.TITLE_HASH, item.title_hash))
    return keys


def find_by_keys(session: Session, keys: list[tuple[str, str]]) -> tuple[Finding, str] | None:
    for key_type, key_value in keys:
        row = session.execute(
            select(FindingKey).where(
                FindingKey.key_type == key_type, FindingKey.key_value == key_value
            )
        ).scalar_one_or_none()
        if row is not None:
            finding = session.get(Finding, row.finding_id)
            if finding is not None:
                return finding, key_type
    return None


def find_semantic_duplicate(
    session: Session,
    embedding: list[float] | None,
    *,
    threshold: float | None = None,
    exclude_id: uuid.UUID | None = None,
) -> tuple[Finding, float] | None:
    """Ближайший сосед по косинусу. Порог — DEDUP_COSINE_THRESHOLD (расстояние!)."""
    if not embedding:
        return None
    threshold = threshold if threshold is not None else settings.DEDUP_COSINE_THRESHOLD

    distance = Finding.embedding.cosine_distance(embedding)
    stmt = (
        select(Finding, distance.label("distance"))
        .where(Finding.embedding.isnot(None))
        .order_by(distance)
        .limit(1)
    )
    if exclude_id is not None:
        stmt = stmt.where(Finding.id != exclude_id)

    row = session.execute(stmt).first()
    if row is None:
        return None

    finding, dist = row[0], float(row[1])
    if dist <= threshold:
        return finding, dist
    return None


def attach_source(
    session: Session,
    finding: Finding,
    *,
    raw_item_id: uuid.UUID,
    source_id: uuid.UUID | None,
    source_kind: str,
    method: str,
    score: float | None = None,
) -> None:
    """Привязать сырьё к находке. Повтор из другого источника = +confidence."""
    existing = session.get(FindingSource, {"finding_id": finding.id, "raw_item_id": raw_item_id})
    if existing is not None:
        return

    session.add(
        FindingSource(
            finding_id=finding.id,
            raw_item_id=raw_item_id,
            source_id=source_id,
            dedup_method=method,
            dedup_score=score,
        )
    )

    if source_kind and source_kind not in (finding.found_via or []):
        finding.found_via = list(finding.found_via or []) + [source_kind]
    finding.seen_count = (finding.seen_count or 1) + 1
    finding.last_seen_at = datetime.now(timezone.utc)


def upsert_keys(session: Session, finding: Finding, keys: list[tuple[str, str]]) -> None:
    """Ключи добавляются идемпотентно: чужие не перехватываем."""
    for key_type, key_value in keys:
        existing = session.execute(
            select(FindingKey).where(
                FindingKey.key_type == key_type, FindingKey.key_value == key_value
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                FindingKey(finding_id=finding.id, key_type=key_type, key_value=key_value)
            )
        elif existing.finding_id != finding.id:
            log.warning(
                "dedup_key_conflict",
                key_type=key_type,
                owner=str(existing.finding_id),
                candidate=str(finding.id),
            )


def resolve_finding(
    session: Session,
    item: NormalizedItem,
    *,
    embedding: list[float] | None,
    github_id: int | None = None,
    repository_id: uuid.UUID | None = None,
) -> DedupResult:
    """Найти существующую находку или создать новую."""
    keys = build_keys(item, github_id)

    hit = find_by_keys(session, keys)
    if hit is not None:
        finding, method = hit
        upsert_keys(session, finding, keys)
        _enrich(finding, item, embedding, repository_id)
        return DedupResult(finding=finding, is_new=False, method=method)

    semantic = find_semantic_duplicate(session, embedding)
    if semantic is not None:
        finding, distance = semantic
        upsert_keys(session, finding, keys)
        _enrich(finding, item, embedding, repository_id)
        log.info("dedup_semantic_hit", finding_id=str(finding.id), distance=round(distance, 4))
        return DedupResult(
            finding=finding, is_new=False, method="semantic", score=1.0 - distance
        )

    finding = Finding(
        kind=item.kind,
        title=item.title,
        url=item.url,
        normalized_url=item.normalized_url,
        content_text=item.content_text,
        embedding=embedding,
        repository_id=repository_id,
        found_via=[item.source_kind] if item.source_kind else [],
        seen_count=1,
    )
    session.add(finding)
    session.flush()
    upsert_keys(session, finding, keys)
    return DedupResult(finding=finding, is_new=True, method="new")


def _enrich(
    finding: Finding,
    item: NormalizedItem,
    embedding: list[float] | None,
    repository_id: uuid.UUID | None,
) -> None:
    """Дубль может принести данные, которых не было: репозиторий, текст, вектор."""
    if repository_id and not finding.repository_id:
        finding.repository_id = repository_id
    if embedding and finding.embedding is None:
        finding.embedding = embedding
    if item.content_text and len(item.content_text) > len(finding.content_text or ""):
        finding.content_text = item.content_text
    if item.url and not finding.url:
        finding.url = item.url
        finding.normalized_url = item.normalized_url


__all__ = [
    "DedupResult",
    "build_keys",
    "find_by_keys",
    "find_semantic_duplicate",
    "attach_source",
    "upsert_keys",
    "resolve_finding",
]
