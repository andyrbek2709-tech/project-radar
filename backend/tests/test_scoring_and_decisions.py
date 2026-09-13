"""Тесты скоринга и движка решений — самая важная логика Radar."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.analysis import DecisionStatus, ReasonCode, TriggerType
from app.services.decision_engine import _is_major_bump, check_trigger_fired, evaluate
from app.services.scoring import (
    ScoreSet,
    build_scores,
    compute_activity,
    compute_improvement,
    compute_maturity,
    compute_radar_score,
    compute_risk,
)


def _repo(**kw) -> dict:
    base = {
        "stars": 500,
        "forks": 40,
        "language": "Python",
        "license_spdx": "MIT",
        "created_at_gh": datetime.now(timezone.utc) - timedelta(days=400),
        "pushed_at_gh": datetime.now(timezone.utc) - timedelta(days=3),
        "latest_release_tag": "v1.2.0",
        "contributors_count": 15,
        "commits_last_week": 12,
        "archived": False,
    }
    base.update(kw)
    return base


class TestStarsAreNotKing:
    def test_huge_stars_do_not_dominate(self):
        """50k звёзд не должны перебивать точное попадание в задачу."""
        popular_irrelevant, _ = build_scores(
            llm={"relevance": 0.3, "project_fit": 0.2, "improvement": 0.1,
                 "novelty": 0.2, "implementation_cost": 0.5, "confidence": 0.8},
            repo=_repo(stars=50_000),
            max_similarity_to_features=None,
            max_similarity_to_past=None,
        )
        modest_relevant, _ = build_scores(
            llm={"relevance": 0.95, "project_fit": 0.9, "improvement": 0.85,
                 "novelty": 0.8, "implementation_cost": 0.2, "confidence": 0.8},
            repo=_repo(stars=120),
            max_similarity_to_features=None,
            max_similarity_to_past=None,
        )
        assert modest_relevant.radar_score > popular_irrelevant.radar_score

    def test_stars_saturate(self):
        low = compute_maturity(_repo(stars=500))[1]["stars"]
        high = compute_maturity(_repo(stars=50_000))[1]["stars"]
        # Стократная разница в звёздах не даёт стократной разницы в оценке.
        assert high - low < 0.5


class TestMaturityAndRisk:
    def test_missing_license_is_risky(self):
        risk, parts = compute_risk(_repo(license_spdx=None))
        assert parts["license"] >= 0.6

    def test_agpl_is_risky(self):
        _, parts = compute_risk(_repo(license_spdx="AGPL-3.0"))
        assert parts["license"] >= 0.8

    def test_mit_is_safe(self):
        _, parts = compute_risk(_repo(license_spdx="MIT"))
        assert parts["license"] <= 0.1

    def test_single_contributor_raises_bus_factor_risk(self):
        _, parts = compute_risk(_repo(contributors_count=1))
        assert parts["bus_factor"] > 0.5

    def test_abandoned_repo_has_low_activity(self):
        old = datetime.now(timezone.utc) - timedelta(days=800)
        activity, _ = compute_activity(_repo(pushed_at_gh=old, commits_last_week=0))
        assert activity < 0.2


class TestImprovement:
    def test_duplicate_feature_zeroes_improvement(self):
        """Если у нас это уже есть — улучшения нет по определению."""
        assert compute_improvement(0.9, 0.95) == 0.0

    def test_partial_similarity_reduces_improvement(self):
        full = compute_improvement(0.8, None)
        partial = compute_improvement(0.8, 0.5)
        assert partial < full


class TestDecisionEngine:
    def _ctx(self, **kw) -> dict:
        base = {"max_similarity_to_features": None, "nearest_feature_name": None}
        base.update(kw)
        return base

    def test_duplicate_is_rejected_with_reason(self):
        scores = ScoreSet(relevance_score=0.9, improvement_score=0.8, activity_score=0.9,
                          risk_score=0.1, radar_score=0.8, confidence_score=0.9)
        verdict = evaluate(
            scores=scores,
            repo=_repo(),
            match_context=self._ctx(
                max_similarity_to_features=0.93, nearest_feature_name="Парсинг PDF через pdfplumber"
            ),
            project_name="EngHub",
        )
        assert verdict.status == DecisionStatus.REJECTED
        assert verdict.reason_code == ReasonCode.DUPLICATES_EXISTING
        assert "pdfplumber" in verdict.reason
        # Отклонение не навсегда: триггеры переоценки взведены.
        assert verdict.triggers

    def test_no_advantage_is_rejected(self):
        scores = ScoreSet(relevance_score=0.9, improvement_score=0.1, activity_score=0.9,
                          risk_score=0.1, radar_score=0.5, confidence_score=0.8)
        verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                           project_name="EngHub")
        assert verdict.status == DecisionStatus.REJECTED
        assert verdict.reason_code == ReasonCode.NO_REAL_ADVANTAGE

    def test_agpl_rejected_on_license(self):
        scores = ScoreSet(relevance_score=0.95, improvement_score=0.9, activity_score=0.9,
                          risk_score=0.2, radar_score=0.9, confidence_score=0.9)
        verdict = evaluate(scores=scores, repo=_repo(license_spdx="AGPL-3.0"),
                           match_context=self._ctx(), project_name="EngHub")
        assert verdict.status == DecisionStatus.REJECTED
        assert verdict.reason_code == ReasonCode.LICENSE_RISK

    def test_inactive_project_rejected(self):
        scores = ScoreSet(relevance_score=0.9, improvement_score=0.8, activity_score=0.05,
                          risk_score=0.3, radar_score=0.6, confidence_score=0.8)
        verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                           project_name="EngHub")
        assert verdict.status == DecisionStatus.REJECTED
        assert verdict.reason_code == ReasonCode.PROJECT_INACTIVE

    def test_high_score_is_critical(self):
        scores = ScoreSet(relevance_score=0.95, improvement_score=0.9, activity_score=0.9,
                          maturity_score=0.9, project_fit_score=0.9, novelty_score=0.8,
                          implementation_cost_score=0.2, risk_score=0.1,
                          confidence_score=0.85, radar_score=0.85)
        verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                           project_name="EngHub")
        assert verdict.status == DecisionStatus.CRITICAL

    def test_low_confidence_prevents_critical(self):
        scores = ScoreSet(relevance_score=0.95, improvement_score=0.9, activity_score=0.9,
                          risk_score=0.1, confidence_score=0.4, radar_score=0.85)
        verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                           project_name="EngHub")
        assert verdict.status == DecisionStatus.RECOMMENDED

    def test_every_verdict_has_non_empty_reason(self):
        """Инвариант: решения без причины не существует."""
        for improvement, activity, radar in [
            (0.1, 0.9, 0.5), (0.9, 0.05, 0.6), (0.9, 0.9, 0.85),
            (0.6, 0.7, 0.65), (0.5, 0.6, 0.45), (0.3, 0.5, 0.2),
        ]:
            scores = ScoreSet(relevance_score=0.9, improvement_score=improvement,
                              activity_score=activity, risk_score=0.1,
                              confidence_score=0.8, radar_score=radar)
            verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                               project_name="EngHub")
            assert verdict.reason.strip()
            assert verdict.status in DecisionStatus.ALL

    def test_reason_mentions_project(self):
        scores = ScoreSet(relevance_score=0.9, improvement_score=0.1, activity_score=0.9,
                          risk_score=0.1, radar_score=0.5, confidence_score=0.8)
        verdict = evaluate(scores=scores, repo=_repo(), match_context=self._ctx(),
                           project_name="VFORMATE")
        assert "VFORMATE" in verdict.reason


class _FakeTrigger:
    def __init__(self, condition: dict, baseline: dict) -> None:
        self.condition = condition
        self.baseline = baseline


class TestReassessment:
    def test_stars_surge_fires(self):
        trigger = _FakeTrigger(
            {"field": "stars", "op": ">=", "value": 2200}, {"stars": 1100}
        )
        fired, evidence = check_trigger_fired(trigger, {"stars": 2400})
        assert fired
        assert evidence["now"] == 2400

    def test_stars_surge_does_not_fire_early(self):
        trigger = _FakeTrigger(
            {"field": "stars", "op": ">=", "value": 2200}, {"stars": 1100}
        )
        fired, _ = check_trigger_fired(trigger, {"stars": 1500})
        assert not fired

    def test_license_change_fires(self):
        trigger = _FakeTrigger(
            {"field": "license_spdx", "op": "changed"}, {"license_spdx": "AGPL-3.0"}
        )
        fired, _ = check_trigger_fired(trigger, {"license_spdx": "MIT"})
        assert fired

    @pytest.mark.parametrize(
        "old,new,expected",
        [("v1.4.2", "v2.0.0", True), ("v1.4.2", "v1.5.0", False),
         ("1.9.9", "2.0.0", True), (None, "v1.0.0", True), ("v2.0.0", "v2.1.0", False)],
    )
    def test_major_bump_detection(self, old, new, expected):
        assert _is_major_bump(old, new) is expected


class TestRadarScore:
    def test_penalties_reduce_score(self):
        cheap = ScoreSet(relevance_score=0.9, project_fit_score=0.9, improvement_score=0.9,
                         novelty_score=0.8, maturity_score=0.8, activity_score=0.8,
                         implementation_cost_score=0.1, risk_score=0.1, confidence_score=0.9)
        expensive = ScoreSet(**{**cheap.as_dict(),
                                "implementation_cost_score": 0.9, "risk_score": 0.9})
        assert compute_radar_score(cheap) > compute_radar_score(expensive)

    def test_score_always_in_range(self):
        for value in (0.0, 0.5, 1.0):
            scores = ScoreSet(**{k: value for k in ScoreSet().as_dict() if k != "radar_score"})
            assert 0.0 <= compute_radar_score(scores) <= 1.0
