"""Pydantic-схемы ответов LLM.

Groq отдаёт строгий JSON по схеме, но «строгий» — это контракт с моделью,
а не гарантия. Всё, что пришло, валидируется здесь; невалидное не попадает
в базу молча, а помечается status='schema_error' и уходит в REVIEW_LATER.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


class GroqClassification(BaseModel):
    """Первый дешёвый массовый слой. Решения о внедрении НЕ принимает."""

    relevance: float = Field(ge=0.0, le=1.0)
    projects: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    novelty: int = Field(ge=0, le=10, default=0)
    potential_value: int = Field(ge=0, le=10, default=0)
    recommend_deep_analysis: bool = False
    noise: bool = False
    summary: str = ""

    # mode="before" обязателен: без него валидатор отработает ПОСЛЕ ge/le,
    # и relevance=1.02 от модели даст ValidationError вместо того, чтобы
    # быть прижатым к 1.0.
    @field_validator("relevance", mode="before")
    @classmethod
    def _cl(cls, v: float) -> float:
        return _clamp01(v)

    @field_validator("projects", "categories", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return [str(x) for x in v]

    @staticmethod
    def json_schema_for_llm() -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "relevance", "projects", "categories", "novelty",
                "potential_value", "recommend_deep_analysis", "noise", "summary",
            ],
            "properties": {
                "relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "projects": {"type": "array", "items": {"type": "string"}},
                "categories": {"type": "array", "items": {"type": "string"}},
                "novelty": {"type": "integer", "minimum": 0, "maximum": 10},
                "potential_value": {"type": "integer", "minimum": 0, "maximum": 10},
                "recommend_deep_analysis": {"type": "boolean"},
                "noise": {"type": "boolean"},
                "summary": {"type": "string"},
            },
        }


class ProjectMatchAnalysis(BaseModel):
    """Оценка относительно КОНКРЕТНОГО проекта.

    Формулировки обязаны быть в форме «что делает → что у нас есть →
    что предлагает нового → преимущество → цена внедрения», а не
    «это интересный проект».
    """

    project: str
    relevance: float = Field(ge=0.0, le=1.0, default=0.0)
    project_fit: float = Field(ge=0.0, le=1.0, default=0.0)
    improvement: float = Field(ge=0.0, le=1.0, default=0.0)
    novelty: float = Field(ge=0.0, le=1.0, default=0.0)
    implementation_cost: float = Field(ge=0.0, le=1.0, default=0.5)
    risk: float = Field(ge=0.0, le=1.0, default=0.5)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    why_relevant: str = ""
    what_we_have: str = ""
    what_it_offers: str = ""
    advantages: list[str] = Field(default_factory=list)
    disadvantages: list[str] = Field(default_factory=list)
    integration_complexity: Literal["low", "medium", "high"] = "medium"
    categories: list[str] = Field(default_factory=list)
    recommend_deep_analysis: bool = False

    @staticmethod
    def json_schema_for_llm() -> dict[str, Any]:
        num = {"type": "number", "minimum": 0, "maximum": 1}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "project", "relevance", "project_fit", "improvement", "novelty",
                "implementation_cost", "risk", "confidence", "why_relevant",
                "what_we_have", "what_it_offers", "advantages", "disadvantages",
                "integration_complexity", "categories", "recommend_deep_analysis",
            ],
            "properties": {
                "project": {"type": "string"},
                "relevance": num, "project_fit": num, "improvement": num,
                "novelty": num, "implementation_cost": num, "risk": num,
                "confidence": num,
                "why_relevant": {"type": "string"},
                "what_we_have": {"type": "string"},
                "what_it_offers": {"type": "string"},
                "advantages": {"type": "array", "items": {"type": "string"}},
                "disadvantages": {"type": "array", "items": {"type": "string"}},
                "integration_complexity": {"type": "string", "enum": ["low", "medium", "high"]},
                "categories": {"type": "array", "items": {"type": "string"}},
                "recommend_deep_analysis": {"type": "boolean"},
            },
        }


class DeepAnalysis(BaseModel):
    """Глубокий разбор. Одинаковая структура и для AUTO (OpenAI),
    и для MANUAL (вставленный ответ ChatGPT) — UI их не различает."""

    what_it_does: str = ""
    what_we_have: str = ""
    real_advantage: bool = False
    replaces: list[str] = Field(default_factory=list)
    complements: list[str] = Field(default_factory=list)
    implementation_complexity: Literal["low", "medium", "high"] = "medium"
    new_services_required: list[str] = Field(default_factory=list)
    new_dependencies: list[str] = Field(default_factory=list)
    maintenance_burden: Literal["low", "medium", "high"] = "medium"
    vendor_lock_in: Literal["none", "partial", "strong"] = "none"
    security_risks: list[str] = Field(default_factory=list)
    license_risks: str = ""
    migration_complexity: Literal["low", "medium", "high"] = "medium"
    expected_benefit: str = ""
    verdict: Literal["CRITICAL", "RECOMMENDED", "REVIEW_LATER", "REJECTED"] = "REVIEW_LATER"
    verdict_reason: str = ""

    @staticmethod
    def json_schema_for_llm() -> dict[str, Any]:
        lvl = {"type": "string", "enum": ["low", "medium", "high"]}
        arr = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "what_it_does", "what_we_have", "real_advantage", "replaces",
                "complements", "implementation_complexity", "new_services_required",
                "new_dependencies", "maintenance_burden", "vendor_lock_in",
                "security_risks", "license_risks", "migration_complexity",
                "expected_benefit", "verdict", "verdict_reason",
            ],
            "properties": {
                "what_it_does": {"type": "string"},
                "what_we_have": {"type": "string"},
                "real_advantage": {"type": "boolean"},
                "replaces": arr, "complements": arr,
                "implementation_complexity": lvl,
                "new_services_required": arr, "new_dependencies": arr,
                "maintenance_burden": lvl,
                "vendor_lock_in": {"type": "string", "enum": ["none", "partial", "strong"]},
                "security_risks": arr,
                "license_risks": {"type": "string"},
                "migration_complexity": lvl,
                "expected_benefit": {"type": "string"},
                "verdict": {
                    "type": "string",
                    "enum": ["CRITICAL", "RECOMMENDED", "REVIEW_LATER", "REJECTED"],
                },
                "verdict_reason": {"type": "string"},
            },
        }


# Ключи стека фиксированы: strict-режим structured outputs требует
# additionalProperties=false в КАЖДОМ объекте схемы, поэтому свободный
# словарь `dict[str, list[str]]` там невозможен — вернётся HTTP 400.
STACK_AREAS: tuple[str, ...] = (
    "backend", "frontend", "database", "infra", "ai", "parsing", "integrations",
)


class RepoAuditResult(BaseModel):
    """Автопрофилирование репозитория пользователя."""

    description: str = ""
    business_purpose: str = ""
    existing_architecture: str = ""
    current_stack: dict[str, list[str]] = Field(default_factory=dict)
    existing_features: list[dict[str, str]] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)
    technology_interests: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    negative_keywords: list[str] = Field(default_factory=list)
    current_problems: list[str] = Field(default_factory=list)

    @staticmethod
    def json_schema_for_llm() -> dict[str, Any]:
        arr = {"type": "array", "items": {"type": "string"}}
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "description", "business_purpose", "existing_architecture",
                "current_stack", "existing_features", "integrations",
                "technology_interests", "search_keywords", "negative_keywords",
                "current_problems",
            ],
            "properties": {
                "description": {"type": "string"},
                "business_purpose": {"type": "string"},
                "existing_architecture": {"type": "string"},
                "current_stack": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(STACK_AREAS),
                    "properties": {
                        area: {"type": "array", "items": {"type": "string"}}
                        for area in STACK_AREAS
                    },
                },
                "existing_features": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "description", "category"],
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "category": {"type": "string"},
                        },
                    },
                },
                "integrations": arr,
                "technology_interests": arr,
                "search_keywords": arr,
                "negative_keywords": arr,
                "current_problems": arr,
            },
        }


__all__ = [
    "GroqClassification",
    "ProjectMatchAnalysis",
    "DeepAnalysis",
    "RepoAuditResult",
    "STACK_AREAS",
]
