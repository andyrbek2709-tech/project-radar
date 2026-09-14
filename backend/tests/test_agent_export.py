"""Формат выгрузки для агента: порядок разделов и отсутствие пустых заголовков."""
from __future__ import annotations

from datetime import datetime

from app.services.agent_export import (
    _analysis_sections,
    _header_line,
    _match_sections,
)

FULL = {
    "what_it_does": "Парсит PDF и достаёт таблицы.",
    "what_we_have": "Свой парсер на pdfplumber.",
    "expected_benefit": "Меньше ручной правки таблиц.",
    "verdict_reason": "Закрывает узкое место нормоконтроля.",
    "replaces": ["pdfplumber"],
    "complements": ["doc-review"],
    "implementation_complexity": "medium",
    "maintenance_burden": "low",
    "migration_complexity": "high",
    "vendor_lock_in": "partial",
    "new_services_required": ["worker для OCR"],
    "new_dependencies": ["camelot"],
    "security_risks": ["читает произвольные файлы"],
    "license_risks": "GPL-3.0 — несовместима с закрытым кодом",
}


def test_sections_follow_fixed_order() -> None:
    """Порядок обязан быть стабильным: иначе diff двух выгрузок нечитаем."""
    text = "\n".join(_analysis_sections(FULL))
    order = [
        "### Что делает",
        "### Что у нас уже есть",
        "### Ожидаемая польза",
        "### Почему такой вердикт",
        "### Отношение к текущему стеку",
        "### Цена внедрения",
        "### Риски",
    ]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)


def test_levels_are_translated() -> None:
    text = "\n".join(_analysis_sections(FULL))
    assert "внедрение средняя" in text
    assert "поддержка низкая" in text
    assert "миграция высокая" in text
    assert "vendor lock-in частичный" in text
    assert "medium" not in text and "high" not in text


def test_empty_analysis_produces_no_headings() -> None:
    """Пустой разбор не должен рожать скелет из заголовков без содержимого."""
    assert _analysis_sections({}) == []


def test_blank_strings_are_skipped() -> None:
    result = dict.fromkeys(("what_it_does", "what_we_have", "expected_benefit"), "   ")
    assert _analysis_sections(result) == []


def test_partial_analysis_keeps_only_filled_sections() -> None:
    text = "\n".join(_analysis_sections({"what_it_does": "Считает сметы."}))
    assert "### Что делает" in text
    assert "### Риски" not in text
    assert "### Цена внедрения" not in text


def test_header_line_lists_what_was_exported() -> None:
    line = _header_line(
        datetime(2026, 9, 14, 23, 15), ["RECOMMENDED", "REVIEW_LATER"], "enghub", 48
    )
    assert line == (
        "Выгружено 14.09.2026 23:15 · статусы: RECOMMENDED, REVIEW_LATER "
        "· проект: enghub · находок: 48"
    )


def test_header_line_without_project() -> None:
    line = _header_line(datetime(2026, 9, 14, 23, 15), ["RECOMMENDED"], None, 6)
    assert "проект" not in line
    assert line.endswith("находок: 6")


MATCH = {
    "why_relevant": "Закрывает разбор сканов, которого у нас нет.",
    "what_we_have": "Построчный парсер PDF на pdfplumber.",
    "what_it_offers": "Конвертация сканов в редактируемые форматы.",
    "advantages": ["держит таблицы", "MIT"],
    "disadvantages": ["тянет torch", "медленно на больших файлах"],
    "integration_complexity": "high",
}


def test_project_match_analysis_is_not_lost() -> None:
    """Живой случай: у находок лежал project_match, а выгрузка читала ключи
    глубокого разбора — в файл попадал единственный совпавший what_we_have."""
    text = "\n".join(_match_sections(MATCH))

    assert "### Что предлагает" in text
    assert "### Что у нас уже есть" in text
    assert "### Почему это нам близко" in text
    assert "- держит таблицы" in text
    assert "- тянет torch" in text
    assert "сложность интеграции высокая" in text


def test_match_sections_skip_what_model_left_empty() -> None:
    assert _match_sections({}) == []


def test_deep_keys_do_not_leak_into_match_output() -> None:
    """Ключи разных разборов не должны перемешиваться."""
    text = "\n".join(_match_sections({**MATCH, "expected_benefit": "не отсюда"}))
    assert "не отсюда" not in text
