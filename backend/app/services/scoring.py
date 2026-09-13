"""Скоринг: независимые метрики + итоговый radar_score.

Главное: звёзды — лишь один из факторов и входят только в maturity/activity
с логарифмическим насыщением. Репозиторий на 50 000 звёзд не должен
перебивать точное попадание в текущую задачу проекта.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

# Веса итоговой формулы. Дублируются в settings-таблице и могут
# переопределяться без редеплоя.
DEFAULT_WEIGHTS: dict[str, float] = {
    "relevance": 0.25,
    "project_fit": 0.20,
    "improvement": 0.20,
    "novelty": 0.15,
    "maturity": 0.10,
    "activity": 0.10,
    "penalty_cost": 0.15,
    "penalty_risk": 0.15,
}

# Лицензии, которые для внутреннего продукта означают реальный юридический риск.
RISKY_LICENSES = frozenset({"AGPL-3.0", "SSPL-1.0", "BUSL-1.1", "ELASTIC-2.0", "OSL-3.0"})
PERMISSIVE_LICENSES = frozenset({"MIT", "APACHE-2.0", "BSD-3-CLAUSE", "BSD-2-CLAUSE", "ISC", "MPL-2.0"})


def clamp01(value: float) -> float:
    if value != value:  # NaN
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _saturate(value: float, half: float) -> float:
    """Логарифмическое насыщение: half → 0.5, дальше растёт всё медленнее."""
    if value <= 0:
        return 0.0
    return clamp01(math.log10(1 + value) / math.log10(1 + half) * 0.5)


@dataclass(slots=True)
class ScoreSet:
    relevance_score: float = 0.0
    novelty_score: float = 0.0
    project_fit_score: float = 0.0
    improvement_score: float = 0.0
    maturity_score: float = 0.0
    activity_score: float = 0.0
    implementation_cost_score: float = 0.0
    risk_score: float = 0.0
    confidence_score: float = 0.0
    radar_score: float = 0.0

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def compute_maturity(repo: dict[str, Any] | None) -> tuple[float, dict[str, float]]:
    """Зрелость: возраст, релизы, контрибьюторы, лицензия, звёзды (с насыщением)."""
    if not repo:
        return 0.4, {"no_repo_default": 0.4}

    parts: dict[str, float] = {}

    age_days = _days_since(repo.get("created_at_gh"))
    # Меньше месяца — сырое; год и больше — зрелое.
    parts["age"] = clamp01((age_days or 0) / 365.0) if age_days is not None else 0.3

    parts["release"] = 1.0 if repo.get("latest_release_tag") else 0.25

    contributors = repo.get("contributors_count") or 0
    # 12 контрибьюторов → ~1.0, дальше насыщение.
    parts["contributors"] = clamp01(_saturate(contributors, half=12) * 2)

    license_spdx = (repo.get("license_spdx") or "").upper()
    if license_spdx in PERMISSIVE_LICENSES:
        parts["license"] = 1.0
    elif not license_spdx:
        parts["license"] = 0.2
    elif license_spdx in RISKY_LICENSES:
        parts["license"] = 0.3
    else:
        parts["license"] = 0.6

    # Звёзды: 500 → ~0.5, дальше насыщение.
    parts["stars"] = _saturate(int(repo.get("stars") or 0), half=500) * 2
    parts["stars"] = clamp01(parts["stars"])

    weights = {"age": 0.25, "release": 0.2, "contributors": 0.2, "license": 0.2, "stars": 0.15}
    score = sum(parts[k] * w for k, w in weights.items())
    return clamp01(score), parts


def compute_activity(repo: dict[str, Any] | None) -> tuple[float, dict[str, float]]:
    """Живость: свежесть push, коммиты за неделю, прирост звёзд из снапшотов."""
    if not repo:
        return 0.4, {"no_repo_default": 0.4}

    parts: dict[str, float] = {}

    pushed_days = _days_since(repo.get("pushed_at_gh"))
    if pushed_days is None:
        parts["push_freshness"] = 0.3
    elif pushed_days <= 7:
        parts["push_freshness"] = 1.0
    elif pushed_days <= 30:
        parts["push_freshness"] = 0.8
    elif pushed_days <= 90:
        parts["push_freshness"] = 0.55
    elif pushed_days <= 270:
        parts["push_freshness"] = 0.3
    else:
        parts["push_freshness"] = 0.05

    commits = repo.get("commits_last_week")
    parts["commits"] = _saturate(commits, half=10) * 2 if commits is not None else 0.4
    parts["commits"] = clamp01(parts["commits"])

    # Прирост звёзд из наших снапшотов — единственный доступный способ
    # детектировать рост: /stargazers закрыт с 30.06.2026.
    growth = repo.get("stars_growth_ratio")
    if growth is None:
        parts["growth"] = 0.4
    else:
        # Удвоение за окно → 1.0
        parts["growth"] = clamp01(float(growth))

    weights = {"push_freshness": 0.45, "commits": 0.25, "growth": 0.30}
    score = sum(parts[k] * w for k, w in weights.items())
    return clamp01(score), parts


def compute_risk(repo: dict[str, Any] | None, llm_risk: float | None = None) -> tuple[float, dict[str, float]]:
    """Риск: больше = хуже."""
    parts: dict[str, float] = {}

    if repo:
        license_spdx = (repo.get("license_spdx") or "").upper()
        if license_spdx in RISKY_LICENSES:
            parts["license"] = 0.9
        elif not license_spdx:
            parts["license"] = 0.7  # без лицензии использовать нельзя
        elif license_spdx in PERMISSIVE_LICENSES:
            parts["license"] = 0.05
        else:
            parts["license"] = 0.35

        contributors = repo.get("contributors_count")
        # Bus factor: один автор — риск заброшенности.
        parts["bus_factor"] = 0.75 if (contributors is not None and contributors <= 1) else 0.15

        pushed_days = _days_since(repo.get("pushed_at_gh")) or 0
        parts["abandonment"] = clamp01(pushed_days / 540.0)

        parts["archived"] = 1.0 if repo.get("archived") else 0.0
    else:
        parts["unknown_source"] = 0.5

    base = sum(parts.values()) / max(len(parts), 1)
    if llm_risk is not None:
        # Модель видит то, чего нет в метаданных (например, «требует свой кластер»).
        base = 0.5 * base + 0.5 * clamp01(llm_risk)
    return clamp01(base), parts


def compute_novelty(
    llm_novelty: float | None,
    max_similarity_to_past: float | None,
) -> float:
    """Новизна = оценка модели, придавленная похожестью на то, что уже видели."""
    base = clamp01(llm_novelty if llm_novelty is not None else 0.5)
    if max_similarity_to_past is not None:
        base *= clamp01(1.0 - max_similarity_to_past)
    return clamp01(base)


def compute_improvement(
    llm_improvement: float | None,
    max_similarity_to_features: float | None,
) -> float:
    """Если находка почти совпадает с существующей фичей — улучшения нет по определению."""
    base = clamp01(llm_improvement if llm_improvement is not None else 0.0)
    if max_similarity_to_features is not None:
        sim = clamp01(max_similarity_to_features)
        if sim >= settings.DUPLICATE_FEATURE_THRESHOLD:
            return 0.0
        base *= clamp01(1.0 - sim * 0.7)
    return clamp01(base)


def compute_confidence(
    llm_confidence: float | None,
    *,
    seen_count: int = 1,
    has_readme: bool = False,
    has_repo_meta: bool = False,
) -> float:
    """Уверенность растёт от полноты данных и от повторного появления
    находки в разных источниках."""
    base = clamp01(llm_confidence if llm_confidence is not None else 0.5)
    bonus = 0.0
    if has_readme:
        bonus += 0.10
    if has_repo_meta:
        bonus += 0.10
    # Второе и третье появление добавляют, дальше — насыщение.
    bonus += min(0.15, 0.075 * max(0, seen_count - 1))
    return clamp01(base + bonus)


def compute_radar_score(scores: ScoreSet, weights: dict[str, float] | None = None) -> float:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    base = (
        w["relevance"] * scores.relevance_score
        + w["project_fit"] * scores.project_fit_score
        + w["improvement"] * scores.improvement_score
        + w["novelty"] * scores.novelty_score
        + w["maturity"] * scores.maturity_score
        + w["activity"] * scores.activity_score
    )
    penalised = base - w["penalty_cost"] * scores.implementation_cost_score \
                     - w["penalty_risk"] * scores.risk_score
    # Низкая уверенность не обнуляет находку, но и не даёт ей выстрелить в CRITICAL.
    return clamp01(clamp01(penalised) * (0.6 + 0.4 * scores.confidence_score))


def build_scores(
    *,
    llm: dict[str, Any],
    repo: dict[str, Any] | None,
    max_similarity_to_features: float | None,
    max_similarity_to_past: float | None,
    seen_count: int = 1,
    weights: dict[str, float] | None = None,
) -> tuple[ScoreSet, dict[str, Any]]:
    """Собрать все метрики. Возвращает (скоры, разбивку для объяснимости в UI)."""
    maturity, maturity_parts = compute_maturity(repo)
    activity, activity_parts = compute_activity(repo)
    risk, risk_parts = compute_risk(repo, llm.get("risk"))

    scores = ScoreSet(
        relevance_score=clamp01(llm.get("relevance", 0.0)),
        project_fit_score=clamp01(llm.get("project_fit", 0.0)),
        improvement_score=compute_improvement(llm.get("improvement"), max_similarity_to_features),
        novelty_score=compute_novelty(llm.get("novelty"), max_similarity_to_past),
        maturity_score=maturity,
        activity_score=activity,
        implementation_cost_score=clamp01(llm.get("implementation_cost", 0.5)),
        risk_score=risk,
        confidence_score=compute_confidence(
            llm.get("confidence"),
            seen_count=seen_count,
            has_readme=bool(repo and repo.get("readme_text")),
            has_repo_meta=bool(repo),
        ),
    )
    scores.radar_score = compute_radar_score(scores, weights)

    breakdown = {
        "weights": {**DEFAULT_WEIGHTS, **(weights or {})},
        "maturity_parts": {k: round(v, 4) for k, v in maturity_parts.items()},
        "activity_parts": {k: round(v, 4) for k, v in activity_parts.items()},
        "risk_parts": {k: round(v, 4) for k, v in risk_parts.items()},
        "similarity": {
            "to_features": max_similarity_to_features,
            "to_past": max_similarity_to_past,
        },
        "seen_count": seen_count,
        "scores": {k: round(v, 4) for k, v in scores.as_dict().items()},
    }
    return scores, breakdown


def _days_since(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds() / 86400.0


__all__ = [
    "ScoreSet",
    "DEFAULT_WEIGHTS",
    "RISKY_LICENSES",
    "PERMISSIVE_LICENSES",
    "clamp01",
    "build_scores",
    "compute_radar_score",
    "compute_maturity",
    "compute_activity",
    "compute_risk",
    "compute_novelty",
    "compute_improvement",
    "compute_confidence",
]
