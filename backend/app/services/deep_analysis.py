"""Deep Analysis — только для перспективных кандидатов, не по каждому сообщению.

Два режима, оба поддержаны архитектурно и пишут ОДНУ И ТУ ЖЕ структуру,
поэтому UI и decision engine их не различают:
  * AUTO   — OPENAI_DEEP_ANALYSIS_ENABLED=true, вызывается OpenAI;
  * MANUAL — кнопка COPY ANALYSIS CONTEXT, ответ из ChatGPT вставляется
             обратно через POST /findings/{id}/manual-analysis.

По умолчанию AUTO выключен.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.llm import LLMError, LLMSchemaError, OpenAIClient
from app.analysis.prompts import DEEP_ANALYSIS_SYSTEM, PROMPT_VERSION, build_deep_analysis_prompt
from app.analysis.schemas import DeepAnalysis
from app.core.config import settings
from app.core.logging import get_logger
from app.models.analysis import (
    AnalysisType,
    DecisionStatus,
    FindingAnalysis,
    ReasonCode,
)
from app.models.finding import Finding, FindingProjectMatch
from app.models.project import Project, ProjectFeature
from app.services import usage
from app.services.decision_engine import Verdict, apply_verdict
from app.services.pipeline import project_to_dict, repo_to_dict
from app.services.scoring import ScoreSet

log = get_logger("openai")


def select_candidates(session: Session, *, limit: int | None = None) -> list[FindingProjectMatch]:
    """Кандидаты: высокий radar_score, рекомендация модели, ещё не разобранные."""
    budget = usage.deep_budget_left(session)
    if budget <= 0:
        log.info("deep_budget_exhausted", limit=settings.MAX_DEEP_ANALYSES_PER_DAY)
        return []

    limit = min(limit or budget, budget)

    already = select(FindingAnalysis.finding_id).where(
        FindingAnalysis.analysis_type.in_(
            [AnalysisType.DEEP_ANALYSIS, AnalysisType.MANUAL_CHATGPT]
        ),
        FindingAnalysis.status == "ok",
    )

    return list(
        session.execute(
            select(FindingProjectMatch)
            .where(
                FindingProjectMatch.radar_score >= settings.MIN_DEEP_ANALYSIS_SCORE,
                FindingProjectMatch.recommend_deep_analysis.is_(True),
                FindingProjectMatch.finding_id.notin_(already),
            )
            .order_by(FindingProjectMatch.radar_score.desc())
            .limit(limit)
        ).scalars().all()
    )


def build_context(
    session: Session, match: FindingProjectMatch
) -> tuple[Finding, Project, dict[str, Any], list[dict[str, Any]]] | None:
    finding = session.get(Finding, match.finding_id)
    project = session.get(Project, match.project_id)
    if finding is None or project is None:
        return None

    features = session.execute(
        select(ProjectFeature).where(ProjectFeature.project_id == project.id)
    ).scalars().all()

    nearest_name = None
    if match.nearest_feature_id:
        nearest = session.get(ProjectFeature, match.nearest_feature_id)
        nearest_name = nearest.name if nearest else None

    match_ctx = {
        "relevance_score": match.relevance_score,
        "project_fit_score": match.project_fit_score,
        "improvement_score": match.improvement_score,
        "risk_score": match.risk_score,
        "why_relevant": match.why_relevant,
        "max_similarity_to_features": match.max_similarity_to_features,
        "nearest_feature_name": nearest_name,
    }
    feature_dicts = [{"name": f.name, "description": f.description} for f in features]
    return finding, project, match_ctx, feature_dicts


def run_deep_analysis(session: Session, match: FindingProjectMatch) -> DeepAnalysis | None:
    """AUTO-режим. Требует OPENAI_DEEP_ANALYSIS_ENABLED=true."""
    client = OpenAIClient()
    if not client.enabled:
        log.info("deep_analysis_auto_disabled")
        return None

    context = build_context(session, match)
    if context is None:
        return None
    finding, project, match_ctx, features = context

    prompt = build_deep_analysis_prompt(
        project=project_to_dict(project),
        finding={
            "title": finding.title,
            "url": finding.url,
            "content": (finding.repository.readme_text if finding.repository else None)
            or finding.content_text,
            "repo_meta": repo_to_dict(finding.repository),
        },
        match=match_ctx,
        existing_features=features,
    )
    digest = hashlib.sha256(f"{prompt}|{PROMPT_VERSION}".encode("utf-8")).hexdigest()

    cached = session.execute(
        select(FindingAnalysis).where(
            FindingAnalysis.finding_id == finding.id,
            FindingAnalysis.project_id == project.id,
            FindingAnalysis.analysis_type == AnalysisType.DEEP_ANALYSIS,
            FindingAnalysis.input_digest == digest,
            FindingAnalysis.status == "ok",
        )
    ).scalar_one_or_none()
    if cached is not None:
        return DeepAnalysis.model_validate(cached.result)

    try:
        result = client.complete_structured(
            system=DEEP_ANALYSIS_SYSTEM,
            user=prompt,
            schema_model=DeepAnalysis,
            schema_name="deep_analysis",
        )
    except (LLMError, LLMSchemaError) as exc:
        session.add(
            FindingAnalysis(
                finding_id=finding.id,
                project_id=project.id,
                analysis_type=AnalysisType.DEEP_ANALYSIS,
                provider="openai",
                model=client.model,
                prompt_version=PROMPT_VERSION,
                input_digest=digest,
                result={},
                status="schema_error" if isinstance(exc, LLMSchemaError) else "api_error",
                error=str(exc)[:500],
            )
        )
        log.warning("deep_analysis_failed", finding_id=str(finding.id), error=str(exc)[:200])
        return None

    parsed: DeepAnalysis = result.parsed
    _store(session, finding, project, digest, parsed, result=result, provider="openai")
    apply_deep_verdict(session, match, parsed, source="auto")
    return parsed


def store_manual_analysis(
    session: Session,
    *,
    finding_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: dict[str, Any],
) -> DeepAnalysis:
    """MANUAL-режим: ответ, вставленный из ChatGPT/Codex.

    Ложится в ту же таблицу с provider='manual' — разбор остаётся в базе,
    а не в чужом чате.
    """
    finding = session.get(Finding, finding_id)
    project = session.get(Project, project_id)
    if finding is None or project is None:
        raise ValueError("finding или project не найдены")

    parsed = DeepAnalysis.model_validate(payload)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    _store(session, finding, project, digest, parsed, result=None, provider="manual")

    match = session.execute(
        select(FindingProjectMatch).where(
            FindingProjectMatch.finding_id == finding_id,
            FindingProjectMatch.project_id == project_id,
        )
    ).scalar_one_or_none()
    if match is not None:
        apply_deep_verdict(session, match, parsed, source="manual")
    return parsed


def _store(
    session: Session,
    finding: Finding,
    project: Project,
    digest: str,
    parsed: DeepAnalysis,
    *,
    result: Any,
    provider: str,
) -> None:
    analysis_type = (
        AnalysisType.DEEP_ANALYSIS if provider == "openai" else AnalysisType.MANUAL_CHATGPT
    )
    session.add(
        FindingAnalysis(
            finding_id=finding.id,
            project_id=project.id,
            analysis_type=analysis_type,
            provider=provider,
            model=getattr(result, "model", "manual"),
            prompt_version=PROMPT_VERSION,
            input_digest=digest,
            result=parsed.model_dump(),
            raw_response=(getattr(result, "raw", "") or "")[:16000],
            status="ok",
            prompt_tokens=getattr(result, "prompt_tokens", 0),
            completion_tokens=getattr(result, "completion_tokens", 0),
            cost_usd=getattr(result, "cost_usd", 0.0),
            latency_ms=getattr(result, "latency_ms", None),
        )
    )
    if result is not None and getattr(result, "cost_usd", 0):
        usage.record(
            session,
            provider=provider,
            operation="deep_analysis",
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            finding_id=finding.id,
        )


def apply_deep_verdict(
    session: Session,
    match: FindingProjectMatch,
    analysis: DeepAnalysis,
    *,
    source: str,
) -> None:
    """Глубокий разбор переопределяет решение движка — он видел больше."""
    finding = session.get(Finding, match.finding_id)
    if finding is None:
        return

    scores = ScoreSet(
        relevance_score=match.relevance_score,
        novelty_score=match.novelty_score,
        project_fit_score=match.project_fit_score,
        improvement_score=match.improvement_score,
        maturity_score=match.maturity_score,
        activity_score=match.activity_score,
        implementation_cost_score=match.implementation_cost_score,
        risk_score=match.risk_score,
        confidence_score=min(1.0, match.confidence_score + 0.2),
        radar_score=match.radar_score,
    )

    reason_code = {
        DecisionStatus.CRITICAL: ReasonCode.HIGH_VALUE,
        DecisionStatus.RECOMMENDED: ReasonCode.HIGH_VALUE,
        DecisionStatus.REVIEW_LATER: ReasonCode.WORTH_REVIEW,
        DecisionStatus.REJECTED: ReasonCode.NO_REAL_ADVANTAGE,
    }.get(analysis.verdict, ReasonCode.WORTH_REVIEW)

    reason = analysis.verdict_reason.strip() or (
        f"глубокий разбор ({source}): "
        + ("реальное преимущество есть" if analysis.real_advantage else "реального преимущества нет")
    )
    if analysis.new_services_required:
        reason += f"; требует новых сервисов: {', '.join(analysis.new_services_required[:3])}"

    verdict = Verdict(
        status=analysis.verdict,
        reason=reason[:2000],
        reason_code=reason_code,
        review_in_days=30 if analysis.verdict == DecisionStatus.REVIEW_LATER else None,
        priority="high" if analysis.real_advantage else "medium",
    )
    apply_verdict(
        session,
        finding=finding,
        match=match,
        verdict=verdict,
        scores=scores,
        decided_by=f"deep:{source}",
    )

    # Обогащаем карточку тем, что увидел глубокий разбор.
    if analysis.what_it_does and not match.what_it_offers:
        match.what_it_offers = analysis.what_it_does
    if analysis.what_we_have and not match.what_we_have:
        match.what_we_have = analysis.what_we_have
    if analysis.security_risks:
        match.disadvantages = list(match.disadvantages or []) + analysis.security_risks[:3]
    match.integration_complexity = analysis.implementation_complexity


def run_batch(session: Session, *, limit: int | None = None) -> dict[str, Any]:
    """Часовая задача. Уважает MAX_DEEP_ANALYSES_PER_DAY."""
    if not settings.OPENAI_DEEP_ANALYSIS_ENABLED:
        pending = len(select_candidates(session, limit=limit))
        return {
            "status": "manual_mode",
            "pending_candidates": pending,
            "hint": "OPENAI_DEEP_ANALYSIS_ENABLED=false — разбор через COPY ANALYSIS CONTEXT",
        }

    candidates = select_candidates(session, limit=limit)
    done = failed = 0
    for match in candidates:
        try:
            if run_deep_analysis(session, match) is not None:
                done += 1
            session.flush()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.warning("deep_batch_item_failed", match_id=str(match.id), error=str(exc)[:200])

    log.info("deep_batch_done", done=done, failed=failed, candidates=len(candidates))
    return {"status": "ok", "analysed": done, "failed": failed, "candidates": len(candidates)}


__all__ = [
    "select_candidates",
    "run_deep_analysis",
    "store_manual_analysis",
    "apply_deep_verdict",
    "run_batch",
]
