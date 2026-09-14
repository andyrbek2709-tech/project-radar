"""Пачка — не транзакция: разобранное обязано пережить сбой на следующем.

Этот тест сторожит конкретную регрессию. Цикл run_pipeline фиксировал работу
через flush, а обработчик лимита провайдера делал rollback — и сносил всю
пачку целиком. На проде это выглядело так: пайплайн исправно разбирал по
пять-шесть находок за прогон, а очередь три прогона подряд показывала
одно и то же число. Разобранное откатывалось, следующий прогон брал те же
элементы, и так по кругу.

Единственный тест в наборе, которому нужна живая БД: проверяется именно
граница транзакции, а на моках её не увидеть. Без БД — пропускается,
так что `pytest` без окружения по-прежнему проходит целиком.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="нужна живая PostgreSQL с pgvector: DATABASE_URL не задан",
)

N_ITEMS = 5
# Каждый элемент тратит два вызова Groq: классификацию и сопоставление
# с проектом. Значит первые три элемента пройдут целиком, а четвёртый
# упрётся в лимит.
FAIL_FROM_CALL = 7


@pytest.fixture
def db():
    from app.core.db import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


def _reset(session) -> None:
    from sqlalchemy import text

    for table in (
        "decisions", "finding_project_matches", "finding_analyses", "finding_sources",
        "finding_keys", "findings", "raw_items", "repository_snapshots", "repositories",
    ):
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()


def _seed(session):
    from app.models.project import Project
    from app.models.source import ProcessingStatus, RawItem, Source, SourceKind

    project = session.query(Project).filter(Project.is_active.is_(True)).first()
    if project is None:
        project = Project(slug="test-project", name="Test", is_active=True)
        session.add(project)
        session.commit()

    source = session.query(Source).filter(Source.kind == SourceKind.GITHUB_SEARCH).first()
    if source is None:
        source = Source(kind=SourceKind.GITHUB_SEARCH, external_id="test", title="test")
        session.add(source)
        session.commit()

    for i in range(N_ITEMS):
        session.add(
            RawItem(
                source_id=source.id,
                external_id=f"gh:batch-test:{i}",
                kind="github_repo",
                processing_status=ProcessingStatus.PENDING,
                payload={
                    "full_name": f"owner/pkg-{i}",
                    "description": f"Библиотека для разбора PDF номер {i}",
                    "topics": ["pdf", "rag"],
                    "url": f"https://github.com/owner/pkg-{i}",
                    "stars": 1200 + i,
                },
                content_text=f"Библиотека для разбора PDF номер {i}",
            )
        )
    session.commit()


def _patch_groq(monkeypatch) -> None:
    """Успешные ответы до FAIL_FROM_CALL, дальше — лимит провайдера."""
    from app.analysis.llm import GroqClient, LLMRateLimited, LLMResult
    from app.analysis.schemas import GroqClassification

    classification = {
        "relevance": 0.9, "projects": [], "categories": ["RAG"],
        "novelty": 7, "potential_value": 8, "recommend_deep_analysis": False,
        "noise": False, "summary": "разбор PDF",
    }
    match = {
        "project_fit": 0.8, "improvement": 0.7, "implementation_cost": 0.3,
        "risk": 0.2, "confidence": 0.8, "why_relevant": "парсинг PDF",
        "what_we_have": "pdfplumber", "what_it_offers": "извлечение таблиц",
        "advantages": ["точнее"], "disadvantages": ["ещё одна зависимость"],
        "integration_complexity": "medium", "recommend_deep_analysis": False,
    }
    calls = {"n": 0}

    def fake(self, *, system, user, schema_model, schema_name, **kwargs):
        calls["n"] += 1
        if calls["n"] >= FAIL_FROM_CALL:
            raise LLMRateLimited("Groq rate limit, retry-after=20s", retry_after=20.0)
        payload = classification if schema_model is GroqClassification else match
        return LLMResult(
            parsed=schema_model.model_validate(payload),
            model="fake", provider="groq", raw="{}",
            prompt_tokens=10, completion_tokens=10, cost_usd=0.0, latency_ms=1,
        )

    monkeypatch.setattr(GroqClient, "complete_structured", fake)
    monkeypatch.setattr(GroqClient, "enabled", property(lambda self: True))


class TestRateLimitDoesNotUndoTheBatch:
    def test_processed_items_survive_a_limit_on_a_later_item(self, db, monkeypatch):
        from app.core.db import session_scope
        from app.models.finding import Finding
        from app.models.source import ProcessingStatus, RawItem

        _reset(db)
        _seed(db)
        _patch_groq(monkeypatch)

        pending = lambda: db.query(RawItem).filter(  # noqa: E731
            RawItem.processing_status == ProcessingStatus.PENDING
        ).count()

        db.expire_all()
        before = pending()
        assert before == N_ITEMS

        from app.services.pipeline import run_pipeline

        with session_scope() as session:
            result = run_pipeline(session, batch_size=N_ITEMS)

        db.expire_all()
        after = pending()

        # Пачка остановлена — это штатно.
        assert result["status"] == "rate_limited"
        # А вот разобранное до остановки обязано остаться в базе.
        assert after < before, (
            "очередь не сдвинулась: работа, сделанная до лимита, откатилась вместе "
            "со сбойным элементом — ровно та регрессия, ради которой этот тест"
        )
        assert db.query(Finding).count() == before - after

    def test_remaining_items_stay_pending_for_the_next_run(self, db, monkeypatch):
        """Упёршийся элемент не помечается ошибкой: лимит пройдёт сам."""
        from app.core.db import session_scope
        from app.models.source import ProcessingStatus, RawItem

        _reset(db)
        _seed(db)
        _patch_groq(monkeypatch)

        from app.services.pipeline import run_pipeline

        with session_scope() as session:
            run_pipeline(session, batch_size=N_ITEMS)

        db.expire_all()
        errored = db.query(RawItem).filter(
            RawItem.processing_status == ProcessingStatus.ERROR
        ).count()
        assert errored == 0, "лимит провайдера — не поломка сырья, помечать ERROR нельзя"
