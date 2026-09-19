"""Pipeline: нормализация → дедупликация → cheap filter → Groq → matching →
similarity → decision engine.

Каждая стадия отмечается в findings.pipeline_stage, чтобы упавший прогон
не терял работу предыдущих.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.analysis.embeddings import get_embedding_provider
from app.analysis.llm import (
    GroqClient,
    LLMError,
    LLMRateLimited,
    LLMSchemaError,
    OpenAIClient,
    get_analysis_client,
)
from app.analysis.prompts import (
    CLASSIFICATION_SYSTEM,
    PROJECT_MATCH_SYSTEM,
    PROMPT_VERSION,
    build_classification_prompt,
    build_project_match_prompt,
)
from app.analysis.schemas import GroqClassification, ProjectMatchAnalysis
from app.core.config import settings
from app.core.logging import get_logger
from app.models.analysis import (
    AnalysisType,
    Decision,
    DecisionStatus,
    FindingAnalysis,
    ReasonCode,
    Setting,
)
from app.models.finding import Finding, FindingProjectMatch, FindingStatus
from app.models.project import Project, ProjectFeature
from app.models.repository import Repository
from app.models.source import ProcessingStatus, RawItem, Source
from app.services import usage
from app.services.decision_engine import Verdict, apply_verdict, evaluate
from app.services.dedup import attach_source, resolve_finding
from app.services.normalizer import (
    NormalizedItem,
    cheap_filter,
    normalize_github_repo,
    normalize_telegram_message,
)
from app.services.scoring import ScoreSet, build_scores

log = get_logger("collector")


@dataclass(slots=True)
class PipelineStats:
    processed: int = 0
    filtered_out: int = 0
    duplicates: int = 0
    new_findings: int = 0
    classified: int = 0
    rejected_by_relevance: int = 0
    matches_created: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    errors: int = 0

    def bump(self, status: str) -> None:
        self.decisions[status] = self.decisions.get(status, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "processed": self.processed,
            "filtered_out": self.filtered_out,
            "duplicates": self.duplicates,
            "new_findings": self.new_findings,
            "classified": self.classified,
            "rejected_by_relevance": self.rejected_by_relevance,
            "matches_created": self.matches_created,
            "decisions": self.decisions,
            "errors": self.errors,
        }


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _record_failure(
    session: Session,
    exc: Exception,
    *,
    finding_id: uuid.UUID,
    project_id: uuid.UUID | None,
    analysis_type: str,
    model: str,
    digest: str,
    provider: str = "groq",
) -> None:
    """Записать неудачную попытку анализа, не плодя строк.

    Ключ идемпотентности покрывает только status='ok' (см. модель), поэтому
    отказ провайдера повторную попытку больше не блокирует. Но сам по себе он
    и не должен накапливаться строкой на каждый прогон: при затяжном rate limit
    это тысячи записей за ночь. Существующую запись о провале по тому же входу
    обновляем на месте — видно последнюю причину и сколько раз не вышло.
    """
    status = "schema_error" if isinstance(exc, LLMSchemaError) else "api_error"
    message = str(exc)[:500]

    previous = session.execute(
        select(FindingAnalysis).where(
            FindingAnalysis.finding_id == finding_id,
            FindingAnalysis.project_id == project_id,
            FindingAnalysis.analysis_type == analysis_type,
            FindingAnalysis.prompt_version == PROMPT_VERSION,
            FindingAnalysis.input_digest == digest,
            FindingAnalysis.status != "ok",
        )
    ).scalars().first()

    if previous is not None:
        attempts = int((previous.result or {}).get("attempts", 1)) + 1
        previous.status = status
        previous.error = message
        previous.model = model
        previous.result = {"attempts": attempts}
        return

    session.add(
        FindingAnalysis(
            finding_id=finding_id,
            project_id=project_id,
            analysis_type=analysis_type,
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            input_digest=digest,
            result={"attempts": 1},
            status=status,
            error=message,
        )
    )


def _load_active_projects(session: Session) -> list[Project]:
    return list(
        session.execute(select(Project).where(Project.is_active.is_(True))).scalars().all()
    )


def _negative_keywords(projects: list[Project]) -> set[str]:
    return {kw.lower() for p in projects for kw in (p.negative_keywords or []) if kw}


def project_to_dict(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "business_purpose": project.business_purpose,
        "existing_architecture": project.existing_architecture,
        "current_stack": project.current_stack or {},
        "current_problems": list(project.current_problems or []),
        "planned_features": list(project.planned_features or []),
        "priority_areas": list(project.priority_areas or []),
        "things_not_needed": list(project.things_not_needed or []),
        "technology_interests": list(project.technology_interests or []),
    }


def repo_to_dict(repo: Repository | None, growth: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if repo is None:
        return None
    data = {
        "full_name": repo.full_name,
        "stars": repo.stars,
        "forks": repo.forks,
        "watchers": repo.watchers,
        "language": repo.language,
        "topics": list(repo.topics or []),
        "license_spdx": repo.license_spdx,
        "archived": repo.archived,
        "disabled": repo.disabled,
        "created_at_gh": repo.created_at_gh,
        "pushed_at_gh": repo.pushed_at_gh,
        "latest_release_tag": repo.latest_release_tag,
        "latest_release_at": repo.latest_release_at,
        "contributors_count": repo.contributors_count,
        "commits_last_week": repo.commits_last_week,
        "readme_text": repo.readme_text,
    }
    if growth:
        data.update(growth)
    return data


# ------------------------------------------------------------- нормализация


def normalize_raw_item(item: RawItem, source: Source) -> NormalizedItem | None:
    if item.kind == "telegram_message":
        return normalize_telegram_message(item.payload or {}, source.kind)
    if item.kind == "github_repo":
        return normalize_github_repo(item.payload or {}, source.kind)
    log.warning("unknown_raw_kind", kind=item.kind, raw_item_id=str(item.id))
    return None


# ---------------------------------------------------------------- обработка


def process_raw_item(
    session: Session,
    item: RawItem,
    *,
    projects: list[Project],
    negative_keywords: set[str],
    groq: GroqClient | OpenAIClient,
    stats: PipelineStats,
) -> None:
    source = session.get(Source, item.source_id)
    if source is None:
        item.processing_status = ProcessingStatus.ERROR
        item.error = "source missing"
        stats.errors += 1
        return

    normalized = normalize_raw_item(item, source)
    if normalized is None:
        item.processing_status = ProcessingStatus.ERROR
        item.error = f"no normalizer for kind={item.kind}"
        stats.errors += 1
        return

    # --- cheap filter: без единого обращения к LLM -------------------------
    verdict = cheap_filter(normalized, negative_keywords=negative_keywords)
    if not verdict.passed:
        item.processing_status = ProcessingStatus.FILTERED_OUT
        item.filter_reason = verdict.reason
        stats.filtered_out += 1
        return

    # --- репозиторий (если находка о нём) ----------------------------------
    repository: Repository | None = None
    github_id: int | None = None
    if normalized.github_full_name:
        # Регистр в ссылке из Telegram произвольный (github.com/Owner/Repo),
        # а в БД лежит то, что вернул GitHub — сравниваем без учёта регистра,
        # иначе репозиторий не найдётся и скоринг уйдёт на repo=None.
        repository = session.execute(
            select(Repository)
            .where(func.lower(Repository.full_name) == normalized.github_full_name.lower())
            .limit(1)
        ).scalars().first()
        if repository is not None:
            github_id = repository.github_id

    # --- эмбеддинг ---------------------------------------------------------
    embedding: list[float] | None = None
    try:
        provider = get_embedding_provider()
        text_for_embedding = f"{normalized.title}\n\n{normalized.content_text}"[:8000]
        embedding = provider.embed([text_for_embedding])[0]
        cost = getattr(provider, "last_cost_usd", lambda: 0.0)()
        if cost:
            usage.record(
                session,
                provider="embeddings",
                operation="embed",
                model=settings.EMBEDDING_MODEL,
                cost_usd=cost,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("embedding_failed", raw_item_id=str(item.id), error=str(exc)[:200])

    # --- дедупликация ------------------------------------------------------
    result = resolve_finding(
        session,
        normalized,
        embedding=embedding,
        github_id=github_id,
        repository_id=repository.id if repository else None,
    )
    finding = result.finding
    attach_source(
        session,
        finding,
        raw_item_id=item.id,
        source_id=item.source_id,
        source_kind=source.kind,
        method=result.method,
        score=result.score,
    )

    if result.is_new:
        stats.new_findings += 1
    else:
        stats.duplicates += 1

    item.processing_status = ProcessingStatus.PROCESSED
    stats.processed += 1

    # Уже разобранную находку повторно через LLM не гоняем —
    # повторное появление лишь подняло seen_count и confidence.
    if not result.is_new and finding.status == FindingStatus.DECIDED:
        finding.pipeline_stage = "reseen"
        return

    analyse_finding(
        session,
        finding,
        repository=repository,
        projects=projects,
        groq=groq,
        stats=stats,
    )


# ------------------------------------------------------- Groq + matching


def analyse_finding(
    session: Session,
    finding: Finding,
    *,
    repository: Repository | None,
    projects: list[Project],
    groq: GroqClient | OpenAIClient,
    stats: PipelineStats,
) -> None:
    finding.status = FindingStatus.ANALYZING
    finding.pipeline_stage = "groq_classification"

    repo_data = repo_to_dict(repository)
    project_dicts = [project_to_dict(p) for p in projects]

    classification = _classify(session, finding, repo_data, project_dicts, groq)
    if classification is None:
        finding.pipeline_stage = "groq_failed"
        finding.status = FindingStatus.ANALYZED
        stats.errors += 1
        return

    stats.classified += 1
    if classification.summary:
        finding.summary = classification.summary[:2000]
    if classification.noise:
        finding.is_noise = True

    if classification.relevance < settings.MIN_RELEVANCE_SCORE or classification.noise:
        _reject_globally(session, finding, classification)
        stats.rejected_by_relevance += 1
        stats.bump(DecisionStatus.REJECTED)
        return

    # Проекты, названные моделью; если не назвала — берём все активные,
    # чтобы не потерять находку из-за промаха классификатора.
    named = {p.lower() for p in classification.projects}
    targets = [p for p in projects if p.slug.lower() in named] or projects

    finding.pipeline_stage = "project_matching"
    matched_any = False
    for project in targets:
        try:
            _match_project(
                session,
                finding,
                project=project,
                repository=repository,
                repo_data=repo_data,
                classification=classification,
                groq=groq,
                stats=stats,
            )
            matched_any = True
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "project_match_failed",
                finding_id=str(finding.id),
                project=project.slug,
                error=str(exc)[:200],
            )
            stats.errors += 1

    if matched_any:
        finding.status = FindingStatus.DECIDED
        finding.pipeline_stage = "decided"
    else:
        # Ни одного решения не принято — не помечаем DECIDED, иначе находка
        # выпадет из обработки навсегда и вернуть её сможет только человек.
        finding.status = FindingStatus.ANALYZED
        finding.pipeline_stage = "matching_failed"


def _classify(
    session: Session,
    finding: Finding,
    repo_data: dict[str, Any] | None,
    project_dicts: list[dict[str, Any]],
    groq: GroqClient | OpenAIClient,
) -> GroqClassification | None:
    user_prompt = build_classification_prompt(
        title=finding.title,
        content=finding.content_text,
        url=finding.url,
        source_kind=", ".join(finding.found_via or []) or "unknown",
        repo_meta=repo_data,
        projects=project_dicts,
    )
    digest = _digest({"p": user_prompt, "v": PROMPT_VERSION})

    cached = session.execute(
        select(FindingAnalysis).where(
            FindingAnalysis.finding_id == finding.id,
            FindingAnalysis.analysis_type == AnalysisType.GROQ_CLASSIFICATION,
            FindingAnalysis.input_digest == digest,
            FindingAnalysis.status == "ok",
        )
    ).scalar_one_or_none()
    if cached is not None:
        return GroqClassification.model_validate(cached.result)

    if not groq.enabled:
        # Без Groq pipeline не встаёт: пропускаем всё дальше с нейтральной оценкой,
        # решение примет движок по метаданным репозитория.
        log.warning("groq_disabled_neutral_classification", finding_id=str(finding.id))
        return GroqClassification(
            relevance=settings.MIN_RELEVANCE_SCORE,
            projects=[],
            categories=[],
            novelty=5,
            potential_value=5,
            recommend_deep_analysis=False,
            noise=False,
            summary=(finding.content_text or "")[:300],
        )

    try:
        result = groq.complete_structured(
            system=CLASSIFICATION_SYSTEM,
            user=user_prompt,
            schema_model=GroqClassification,
            schema_name="radar_classification",
        )
    except LLMRateLimited:
        # Не поломка находки, а закрытое окно провайдера. Записывать её как
        # неудачный анализ нельзя: вернёмся к ней следующим прогоном.
        raise
    except (LLMError, LLMSchemaError) as exc:
        _record_failure(
            session,
            exc,
            finding_id=finding.id,
            project_id=None,
            analysis_type=AnalysisType.GROQ_CLASSIFICATION,
            model=groq.model,
            digest=digest,
            provider=groq.provider,
        )
        log.warning("groq_classification_failed", finding_id=str(finding.id), error=str(exc)[:200])
        return None

    parsed: GroqClassification = result.parsed
    session.add(
        FindingAnalysis(
            finding_id=finding.id,
            analysis_type=AnalysisType.GROQ_CLASSIFICATION,
            provider=result.provider,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            input_digest=digest,
            result=parsed.model_dump(),
            raw_response=result.raw[:8000],
            status="ok",
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )
    usage.record(
        session,
        provider=result.provider,
        operation="classification",
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.cost_usd,
        finding_id=finding.id,
    )
    return parsed


def _match_project(
    session: Session,
    finding: Finding,
    *,
    project: Project,
    repository: Repository | None,
    repo_data: dict[str, Any] | None,
    classification: GroqClassification,
    groq: GroqClient | OpenAIClient,
    stats: PipelineStats,
) -> None:
    features = session.execute(
        select(ProjectFeature).where(ProjectFeature.project_id == project.id)
    ).scalars().all()

    sim_feature, nearest_feature = _nearest_feature(finding.embedding, features)
    sim_past, nearest_past = _nearest_past_finding(session, finding)

    analysis = _analyse_match(
        session,
        finding,
        project=project,
        repo_data=repo_data,
        features=features,
        classification=classification,
        groq=groq,
    )

    llm_payload: dict[str, Any] = {
        "relevance": analysis.relevance if analysis else classification.relevance,
        "project_fit": analysis.project_fit if analysis else 0.4,
        "improvement": analysis.improvement if analysis else 0.3,
        "novelty": (analysis.novelty if analysis else classification.novelty / 10.0),
        "implementation_cost": analysis.implementation_cost if analysis else 0.5,
        "risk": analysis.risk if analysis else None,
        "confidence": analysis.confidence if analysis else 0.35,
    }

    scores, breakdown = build_scores(
        llm=llm_payload,
        repo=repo_data,
        max_similarity_to_features=sim_feature,
        max_similarity_to_past=sim_past,
        seen_count=finding.seen_count or 1,
    )

    match = session.execute(
        select(FindingProjectMatch).where(
            FindingProjectMatch.finding_id == finding.id,
            FindingProjectMatch.project_id == project.id,
        )
    ).scalar_one_or_none()
    if match is None:
        match = FindingProjectMatch(finding_id=finding.id, project_id=project.id)
        session.add(match)
        stats.matches_created += 1

    for key, value in scores.as_dict().items():
        setattr(match, key, value)
    match.score_breakdown = breakdown
    match.max_similarity_to_features = sim_feature
    match.nearest_feature_id = nearest_feature.id if nearest_feature else None
    match.max_similarity_to_past = sim_past
    match.nearest_past_finding_id = nearest_past.id if nearest_past else None

    if analysis is not None:
        match.categories = analysis.categories or classification.categories
        match.why_relevant = analysis.why_relevant
        match.what_we_have = analysis.what_we_have
        match.what_it_offers = analysis.what_it_offers
        match.advantages = analysis.advantages
        match.disadvantages = analysis.disadvantages
        match.integration_complexity = analysis.integration_complexity
        match.recommend_deep_analysis = analysis.recommend_deep_analysis
    else:
        match.categories = classification.categories
        match.recommend_deep_analysis = classification.recommend_deep_analysis

    session.flush()

    verdict = evaluate(
        scores=scores,
        repo=repo_data,
        match_context={
            "max_similarity_to_features": sim_feature,
            "nearest_feature_name": nearest_feature.name if nearest_feature else None,
            # Нужен движку, чтобы причина рекомендации называла суть,
            # а не пересказывала порог.
            "what_it_offers": analysis.what_it_offers if analysis is not None else None,
        },
        project_name=project.name,
    )
    apply_verdict(session, finding=finding, match=match, verdict=verdict, scores=scores)
    stats.bump(verdict.status)


def _analyse_match(
    session: Session,
    finding: Finding,
    *,
    project: Project,
    repo_data: dict[str, Any] | None,
    features: list[ProjectFeature],
    classification: GroqClassification,
    groq: GroqClient | OpenAIClient,
) -> ProjectMatchAnalysis | None:
    if not groq.enabled:
        return None

    user_prompt = build_project_match_prompt(
        project=project_to_dict(project),
        finding={
            "title": finding.title,
            "url": finding.url,
            "summary": finding.summary or classification.summary,
            "content": finding.content_text,
            "repo_meta": repo_data,
        },
        existing_features=[
            {"name": f.name, "description": f.description} for f in features
        ],
        similar_past=_past_decisions(session, finding, project),
    )
    digest = _digest({"p": user_prompt, "v": PROMPT_VERSION})

    cached = session.execute(
        select(FindingAnalysis).where(
            FindingAnalysis.finding_id == finding.id,
            FindingAnalysis.project_id == project.id,
            FindingAnalysis.analysis_type == AnalysisType.PROJECT_MATCH,
            FindingAnalysis.input_digest == digest,
            FindingAnalysis.status == "ok",
        )
    ).scalar_one_or_none()
    if cached is not None:
        return ProjectMatchAnalysis.model_validate(cached.result)

    try:
        result = groq.complete_structured(
            system=PROJECT_MATCH_SYSTEM,
            user=user_prompt,
            schema_model=ProjectMatchAnalysis,
            schema_name="project_match",
        )
    except LLMRateLimited:
        raise
    except (LLMError, LLMSchemaError) as exc:
        _record_failure(
            session,
            exc,
            finding_id=finding.id,
            project_id=project.id,
            analysis_type=AnalysisType.PROJECT_MATCH,
            model=groq.model,
            digest=digest,
            provider=groq.provider,
        )
        log.warning(
            "project_match_llm_failed",
            finding_id=str(finding.id),
            project=project.slug,
            error=str(exc)[:200],
        )
        return None

    parsed: ProjectMatchAnalysis = result.parsed
    session.add(
        FindingAnalysis(
            finding_id=finding.id,
            project_id=project.id,
            analysis_type=AnalysisType.PROJECT_MATCH,
            provider=result.provider,
            model=result.model,
            prompt_version=PROMPT_VERSION,
            input_digest=digest,
            result=parsed.model_dump(),
            raw_response=result.raw[:8000],
            status="ok",
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
        )
    )
    usage.record(
        session,
        provider=result.provider,
        operation="project_match",
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost_usd=result.cost_usd,
        finding_id=finding.id,
    )
    return parsed


# ------------------------------------------------------------- similarity


def _nearest_feature(
    embedding: list[float] | None, features: list[ProjectFeature]
) -> tuple[float | None, ProjectFeature | None]:
    """Ближайшая существующая фича — ответ на вопрос «а у нас это уже есть?»."""
    # pgvector отдаёт embedding numpy-массивом: `not array` при длине > 1
    # кидает ValueError («truth value is ambiguous»), поэтому None/длина
    # проверяются явно, а не булевостью самого массива.
    if embedding is None or len(embedding) == 0 or not features:
        return None, None

    from app.analysis.embeddings import cosine_similarity

    best_sim, best = -1.0, None
    for feature in features:
        if feature.embedding is None or len(feature.embedding) == 0:
            continue
        sim = cosine_similarity(embedding, list(feature.embedding))
        if sim > best_sim:
            best_sim, best = sim, feature
    return (best_sim, best) if best is not None else (None, None)


def _nearest_past_finding(
    session: Session, finding: Finding
) -> tuple[float | None, Finding | None]:
    if finding.embedding is None or len(finding.embedding) == 0:
        return None, None

    distance = Finding.embedding.cosine_distance(finding.embedding)
    row = session.execute(
        select(Finding, distance.label("d"))
        .where(
            Finding.embedding.isnot(None),
            Finding.id != finding.id,
            Finding.status == FindingStatus.DECIDED,
        )
        .order_by(distance)
        .limit(1)
    ).first()
    if row is None:
        return None, None
    return 1.0 - float(row[1]), row[0]


def _past_decisions(
    session: Session, finding: Finding, project: Project, limit: int = 8
) -> list[dict[str, Any]]:
    """Прошлые решения по похожим находкам — контекст для модели."""
    if finding.embedding is None or len(finding.embedding) == 0:
        return []

    distance = Finding.embedding.cosine_distance(finding.embedding)
    rows = session.execute(
        select(Finding.title, Decision.status, Decision.reason)
        .join(Decision, Decision.finding_id == Finding.id)
        .where(
            Finding.embedding.isnot(None),
            Finding.id != finding.id,
            Decision.project_id == project.id,
            Decision.is_current.is_(True),
        )
        .order_by(distance)
        .limit(limit)
    ).all()
    return [{"title": t, "status": s, "reason": r} for t, s, r in rows]


# -------------------------------------------------------------- отклонение


def _reject_globally(
    session: Session, finding: Finding, classification: GroqClassification
) -> None:
    """Шум и нерелевантное отклоняются без привязки к проекту."""
    reason = (
        "классифицировано как шум (реклама/вакансия/новость без технической сути)"
        if classification.noise
        else f"релевантность {classification.relevance:.2f} ниже порога "
        f"{settings.MIN_RELEVANCE_SCORE:.2f} — к задачам проектов отношения не имеет"
    )
    session.add(
        Decision(
            finding_id=finding.id,
            project_id=None,
            status=DecisionStatus.REJECTED,
            reason=reason,
            reason_code=ReasonCode.LOW_RELEVANCE,
            decided_by="engine",
            radar_score_at_decision=classification.relevance,
            score_snapshot={"relevance": classification.relevance, "noise": classification.noise},
            is_current=True,
        )
    )
    finding.status = FindingStatus.DECIDED
    finding.pipeline_stage = "rejected_low_relevance"


# ------------------------------------------------------------------ запуск


def reset_errors(session: Session) -> int:
    """Вернуть сырьё из ERROR в очередь.

    ERROR — конечный статус: обычный прогон конвейера выбирает только PENDING
    (см. run_pipeline ниже) и никогда не подхватывает то, что уже упало.
    Любой временный сбой конфигурации (не тот Groq-модель, битый embedding)
    навсегда хоронит попавшие под него находки, если их не вернуть руками.
    """
    result = session.execute(
        update(RawItem)
        .where(RawItem.processing_status == ProcessingStatus.ERROR)
        .values(processing_status=ProcessingStatus.PENDING, error=None)
    )
    session.commit()
    return result.rowcount or 0


def is_pipeline_paused(session: Session) -> bool:
    """Ручной стоп-кран — включается кнопкой на дашборде, без редеплоя.

    Нужен, чтобы можно было остановить траты на LLM немедленно (например,
    пока разбираются с ценой/моделью), не трогая Celery beat и не выключая
    сбор сырья: коллекторы продолжают работать, очередь просто не тает.
    """
    setting = session.get(Setting, "pipeline_paused")
    return bool(setting and setting.value.get("paused"))


def run_pipeline(session: Session, *, batch_size: int | None = None) -> dict[str, Any]:
    """Обработать очередь pending. Идемпотентно: обработанное не берётся повторно."""
    if is_pipeline_paused(session):
        return {"status": "paused"}

    batch_size = batch_size or settings.PIPELINE_BATCH_SIZE
    stats = PipelineStats()

    projects = _load_active_projects(session)
    if not projects:
        log.warning("pipeline_no_projects")
        return {"status": "skipped", "reason": "no_active_projects"}

    negative_keywords = _negative_keywords(projects)

    # Берём идентификаторы, а не объекты. Любой rollback внутри цикла гасит
    # identity map, и сложенные заранее ORM-объекты после него протухают:
    # обращение к откаченной находке роняло весь прогон с ObjectDeletedError.
    item_ids = session.execute(
        select(RawItem.id)
        .where(RawItem.processing_status == ProcessingStatus.PENDING)
        .order_by(RawItem.collected_at.asc())
        .limit(batch_size)
    ).scalars().all()

    groq = get_analysis_client()
    rate_limited = False

    for item_id in item_ids:
        item = session.get(RawItem, item_id)
        if item is None or item.processing_status != ProcessingStatus.PENDING:
            continue
        try:
            process_raw_item(
                session,
                item,
                projects=list(projects),
                negative_keywords=negative_keywords,
                groq=groq,
                stats=stats,
            )
            # Коммит на КАЖДЫЙ элемент, не flush.
            #
            # Пачка — это не транзакция. Элементы независимы, и разобранное
            # обязано пережить сбой на следующем. При flush работа остаётся
            # незафиксированной, и rollback ниже сносил всю пачку целиком:
            # пять разобранных находок исчезали из-за шестой, упёршейся
            # в лимит Groq, сырьё возвращалось в pending, а следующий прогон
            # брал те же элементы и повторял всё с начала. Очередь стояла
            # намертво при исправно работающем пайплайне.
            session.commit()
        except LLMRateLimited as exc:
            # Окно провайдера закрыто — остальная пачка упрётся в то же самое.
            # Откатывается только текущий элемент: он остаётся в pending и
            # достанется следующему прогону. Всё разобранное до него уже
            # зафиксировано и не теряется.
            session.rollback()
            projects = _load_active_projects(session)
            rate_limited = True
            log.warning(
                "pipeline_rate_limited_stop",
                raw_item_id=str(item_id),
                processed=stats.processed,
                retry_after=exc.retry_after,
            )
            break
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            # После отката identity map пуст, а список projects — детачнут.
            projects = _load_active_projects(session)
            fresh = session.get(RawItem, item_id)
            if fresh is not None:
                fresh.processing_status = ProcessingStatus.ERROR
                fresh.error = str(exc)[:500]
                # Пометку тоже фиксируем сразу: иначе её унесёт откат,
                # вызванный любым следующим элементом пачки.
                session.commit()
            stats.errors += 1
            log.warning("pipeline_item_failed", raw_item_id=str(item_id), error=str(exc)[:300])

    # Второй проход: находки, возвращённые на стол.
    #
    # Без него вся ветка «REJECTED не навсегда» мертва: сработавший
    # reassessment-триггер и promote_review_queue ставят статус ANALYZED,
    # но первый проход смотрит только на новое сырьё и таких находок не видит.
    reanalysed = 0
    if not rate_limited:
        try:
            reanalysed = reanalyse_pending(
                session,
                projects=list(projects),
                groq=groq,
                stats=stats,
                limit=max(10, batch_size // 4),
            )
        except LLMRateLimited as exc:
            session.rollback()
            rate_limited = True
            log.warning("pipeline_rate_limited_stop", stage="reanalyse", retry_after=exc.retry_after)

    log.info("pipeline_done", reanalysed=reanalysed, rate_limited=rate_limited, **stats.as_dict())
    return {
        "status": "rate_limited" if rate_limited else "ok",
        "reanalysed": reanalysed,
        **stats.as_dict(),
    }


def reanalyse_pending(
    session: Session,
    *,
    projects: list[Project],
    groq: GroqClient | OpenAIClient,
    stats: PipelineStats,
    limit: int = 30,
) -> int:
    """Переоценить находки в статусе ANALYZED.

    Сюда попадают: сработавшие триггеры переоценки, вернувшиеся из REVIEW LATER
    и те, у кого в прошлый раз не удалось ни одно сопоставление с проектом.
    """
    # Идентификаторы, а не объекты: rollback внутри цикла гасит identity map.
    finding_ids = session.execute(
        select(Finding.id)
        .where(Finding.status == FindingStatus.ANALYZED)
        .order_by(Finding.updated_at.asc())
        .limit(limit)
    ).scalars().all()

    done = 0
    for finding_id in finding_ids:
        finding = session.get(Finding, finding_id)
        if finding is None:
            continue
        repository = (
            session.get(Repository, finding.repository_id) if finding.repository_id else None
        )
        try:
            analyse_finding(
                session,
                finding,
                repository=repository,
                projects=projects,
                groq=groq,
                stats=stats,
            )
            # Как и в основном цикле: каждая находка фиксируется отдельно,
            # чтобы сбой на следующей не унёс уже сделанную работу.
            session.commit()
            done += 1
        except LLMRateLimited:
            # Пробрасываем: решение остановиться принимает run_pipeline.
            # Широкий except ниже проглотил бы лимит и погнал бы остаток
            # пачки в то же закрытое окно.
            session.rollback()
            raise
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            stats.errors += 1
            log.warning("reanalyse_failed", finding_id=str(finding_id), error=str(exc)[:300])

    return done


__all__ = [
    "run_pipeline",
    "reanalyse_pending",
    "process_raw_item",
    "analyse_finding",
    "PipelineStats",
]
