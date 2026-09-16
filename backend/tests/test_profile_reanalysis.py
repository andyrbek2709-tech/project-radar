"""Правка профиля обязана возвращать находки на переоценку. БД не нужна.

История: 14.09.2026 профили всех семи проектов были починены, но выгрузка от
16.09 всё равно описывала EngHub как «Python, FastAPI, PostgreSQL» и советовала
pydantic, стоящий в трёх его сервисах. Причина не в промптах и не в кэше:
`reanalyse_pending` выбирает находки со `status='analyzed'`, а разобранная
находка лежит в `decided` — вернуть её оттуда умел только reassessment-триггер,
следящий за репозиторием находки, а не за профилем проекта. Сбой молчаливый:
ничего не падает, выгрузка просто продолжает описывать проект таким, каким он
был до правки.
"""
from __future__ import annotations

from app.services.pipeline import project_to_dict
from app.services.profile_reanalysis import (
    PROFILE_PROMPT_FIELDS,
    profile_changes_need_reanalysis,
)


class _FakeProject:
    """Достаточно атрибутов, чтобы project_to_dict отработал без БД."""

    id = "0" * 8
    slug = "enghub"
    name = "EngHub"
    description = "Инженерная платформа"
    business_purpose = "Выпуск рабочей документации"
    existing_architecture = "Express 4 на TypeScript, Node 22"
    current_stack = {"backend": ["Express 4"]}
    current_problems = ["нет единого роутера LLM"]
    planned_features = ["S-кривая план/факт"]
    priority_areas = ["RAG по нормативам"]
    things_not_needed = ["Vercel"]
    technology_interests = ["pgvector"]


def test_prompt_fields_match_project_to_dict() -> None:
    """Набор полей не должен разъехаться с тем, что уходит в промпт.

    Если в `project_to_dict` добавят поле, а в `PROFILE_PROMPT_FIELDS` — нет,
    правка этого поля снова перестанет возвращать находки на переоценку, и
    выгрузка снова начнёт описывать проект по-старому. Молча.
    """
    prompt_keys = set(project_to_dict(_FakeProject()).keys()) - {"id"}
    assert prompt_keys == set(PROFILE_PROMPT_FIELDS)


def test_stack_change_triggers_reanalysis() -> None:
    assert profile_changes_need_reanalysis(["current_stack"]) is True
    assert profile_changes_need_reanalysis(["existing_architecture", "name"]) is True


def test_collection_only_fields_do_not_trigger() -> None:
    """Ключевые слова меняют сбор и дешёвый фильтр, но не текст промпта.

    `input_digest` от них не зависит, повторный разбор вернулся бы из кэша —
    переоценка вышла бы холостой и оплаченной впустую.
    """
    assert profile_changes_need_reanalysis(["search_keywords"]) is False
    assert profile_changes_need_reanalysis(["negative_keywords", "integrations"]) is False
    assert profile_changes_need_reanalysis([]) is False
