"""Промпты. Версия фиксируется в PROMPT_VERSION — результаты разных версий
не смешиваются в finding_analyses.

Главное правило, зашитое во все системные промпты: оценка всегда относительно
конкретного проекта. Формулировка «это интересный проект» запрещена.
"""
from __future__ import annotations

from typing import Any

PROMPT_VERSION = "v2"

MAX_CONTENT_CHARS = 6000
MAX_README_CHARS = 8000


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n…[обрезано]"


# --------------------------------------------------------------- classification

CLASSIFICATION_SYSTEM = """\
Ты — фильтр технического радара. Твоя задача: отсеять шум и назвать кандидатов.
Ты НЕ принимаешь решения о внедрении — это делает отдельный движок.

ЯЗЫК ОТВЕТА — ТОЛЬКО РУССКИЙ. Это правило важнее всех остальных.
Исходный материал почти всегда на английском: описания репозиториев, README,
названия. Ты всё равно пишешь по-русски. Английскими остаются только имена
собственные: названия репозиториев, библиотек, языков, форматов и лицензий
(pdfplumber, FastAPI, Apache-2.0). Всё остальное — предложения, оценки,
причины, перечисления — по-русски. Ответ, скопированный из английского
описания, считается невыполненным заданием.

Правила:
1. relevance — вероятность, что находка полезна ХОТЯ БЫ ОДНОМУ из перечисленных
   проектов. Общие новости, релизы популярных продуктов без отношения к задачам
   проектов, вакансии, реклама, курсы, «подборки» — это шум, relevance < 0.2.
2. projects — только те slug'и из списка, к которым находка реально относится.
   Пустой список допустим и это нормально.
3. noise=true для: рекламы, вакансий, мемов, новостей о компаниях, общих рассуждений
   без конкретной технологии, дубликатов известных вещей.
4. recommend_deep_analysis=true только если находка похожа на то, что может
   ЗАМЕНИТЬ или СУЩЕСТВЕННО УЛУЧШИТЬ существующий механизм в одном из проектов.
5. summary — одна-две фразы ПО-РУССКИ: что это и что оно делает. Без оценок.
   Даже если описание репозитория на английском — пересказываешь по-русски,
   а не копируешь.

Отвечай строго JSON по схеме.
"""


def build_classification_prompt(
    *,
    title: str,
    content: str | None,
    url: str | None,
    source_kind: str,
    repo_meta: dict[str, Any] | None,
    projects: list[dict[str, Any]],
) -> str:
    lines: list[str] = ["# ПРОЕКТЫ ПОЛЬЗОВАТЕЛЯ", ""]
    for p in projects:
        lines.append(f"## {p['slug']} — {p.get('name', '')}")
        if p.get("description"):
            lines.append(f"Назначение: {p['description']}")
        stack = p.get("current_stack") or {}
        if stack:
            flat = ", ".join(
                f"{k}: {', '.join(v)}" for k, v in stack.items() if v
            )
            lines.append(f"Стек: {flat}")
        if p.get("current_problems"):
            lines.append(f"Текущие проблемы: {'; '.join(p['current_problems'])}")
        if p.get("priority_areas"):
            lines.append(f"Приоритеты: {', '.join(p['priority_areas'])}")
        if p.get("things_not_needed"):
            lines.append(f"НЕ нужно: {', '.join(p['things_not_needed'])}")
        lines.append("")

    lines += ["# НАХОДКА", "", f"Источник: {source_kind}", f"Заголовок: {title}"]
    if url:
        lines.append(f"URL: {url}")
    if repo_meta:
        lines.append(
            "Репозиторий: ★{stars} · forks {forks} · {language} · "
            "лицензия {license} · последний push {pushed}".format(
                stars=repo_meta.get("stars", 0),
                forks=repo_meta.get("forks", 0),
                language=repo_meta.get("language") or "n/a",
                license=repo_meta.get("license_spdx") or "не указана",
                pushed=repo_meta.get("pushed_at") or "n/a",
            )
        )
        if repo_meta.get("topics"):
            lines.append(f"Topics: {', '.join(repo_meta['topics'][:15])}")
    lines += ["", "Содержимое:", _truncate(content, MAX_CONTENT_CHARS)]
    return "\n".join(lines)


# ------------------------------------------------------------- project match

PROJECT_MATCH_SYSTEM = """\
Ты — технический аналитик радара. Оцениваешь находку ОТНОСИТЕЛЬНО ОДНОГО
конкретного проекта.

ЗАПРЕЩЕНО писать «это интересный проект», «полезный инструмент», «стоит посмотреть».
ОБЯЗАТЕЛЬНЫЙ ход рассуждения в полях why_relevant / what_we_have / what_it_offers:
  1) что технология делает;
  2) что у проекта есть СЕЙЧАС для этой задачи (бери из списка существующих фич);
  3) что новое предлагает находка;
  4) в чём преимущество относительно текущего подхода — конкретно, а не абстрактно;
  5) цена внедрения.

Если у проекта уже есть решение той же задачи и находка не даёт заметного
преимущества — improvement близок к 0, и так и напиши в what_we_have.

Оценки:
  relevance          — про нас ли это вообще
  project_fit        — попадание в стек, проблемы и приоритеты проекта
  improvement        — насколько лучше того, что есть сейчас; 0 если не лучше
  novelty            — новизна подхода, а не «свежесть релиза»
  implementation_cost— БОЛЬШЕ = ДОРОЖЕ внедрять
  risk               — БОЛЬШЕ = РИСКОВАННЕЕ (лицензия, вендор-лок, заброшенность)
  confidence         — насколько ты уверен в оценке при имеющихся данных

Отвечай строго JSON по схеме.

ЯЗЫК ОТВЕТА — ТОЛЬКО РУССКИЙ. Это правило важнее всех остальных.
Исходный материал почти всегда на английском: описания репозиториев, README,
названия. Ты всё равно пишешь по-русски. Английскими остаются только имена
собственные: названия репозиториев, библиотек, языков, форматов и лицензий
(pdfplumber, FastAPI, Apache-2.0). Всё остальное — предложения, оценки,
причины, перечисления — по-русски. Ответ, скопированный из английского
описания, считается невыполненным заданием.
"""


def build_project_match_prompt(
    *,
    project: dict[str, Any],
    finding: dict[str, Any],
    existing_features: list[dict[str, Any]],
    similar_past: list[dict[str, Any]],
) -> str:
    lines = [f"# ПРОЕКТ: {project['slug']} — {project.get('name', '')}", ""]
    if project.get("description"):
        lines.append(project["description"])
    if project.get("business_purpose"):
        lines.append(f"\nБизнес-назначение: {project['business_purpose']}")
    if project.get("existing_architecture"):
        lines.append(f"\nАрхитектура: {project['existing_architecture']}")

    stack = project.get("current_stack") or {}
    if stack:
        lines.append("\n## Текущий стек")
        for area, items in stack.items():
            if items:
                lines.append(f"- {area}: {', '.join(items)}")

    if existing_features:
        lines.append("\n## Что уже реализовано")
        for f in existing_features[:40]:
            desc = f" — {f['description']}" if f.get("description") else ""
            lines.append(f"- {f['name']}{desc}")

    for label, key in (
        ("Текущие проблемы", "current_problems"),
        ("Запланированные фичи", "planned_features"),
        ("Приоритетные области", "priority_areas"),
        ("НЕ нужно проекту", "things_not_needed"),
    ):
        if project.get(key):
            lines.append(f"\n{label}: {'; '.join(project[key])}")

    lines += ["", "# НАХОДКА", "", f"Название: {finding.get('title')}"]
    if finding.get("url"):
        lines.append(f"URL: {finding['url']}")
    if finding.get("repo_meta"):
        rm = finding["repo_meta"]
        lines.append(
            f"★ {rm.get('stars', 0)} (прирост за 14 дней: {rm.get('stars_delta', 'n/a')}) · "
            f"forks {rm.get('forks', 0)} · {rm.get('language') or 'n/a'} · "
            f"лицензия {rm.get('license_spdx') or 'не указана'} · "
            f"последний push {rm.get('pushed_at') or 'n/a'} · "
            f"контрибьюторов {rm.get('contributors_count', 'n/a')}"
        )
        if rm.get("latest_release_tag"):
            lines.append(f"Последний релиз: {rm['latest_release_tag']} ({rm.get('latest_release_at')})")
    if finding.get("summary"):
        lines.append(f"\nКраткое описание: {finding['summary']}")
    lines.append("\nОписание/README:")
    lines.append(_truncate(finding.get("content"), MAX_README_CHARS))

    if similar_past:
        lines.append("\n# ПОХОЖИЕ ПРОШЛЫЕ НАХОДКИ И РЕШЕНИЯ ПО НИМ")
        for s in similar_past[:8]:
            lines.append(
                f"- {s.get('title')} → {s.get('status', 'без решения')}"
                f"{': ' + s['reason'] if s.get('reason') else ''}"
            )

    lines.append(
        "\nОцени находку относительно проекта "
        f"{project['slug']} и верни JSON по схеме."
    )
    return "\n".join(lines)


# ------------------------------------------------------------- deep analysis

DEEP_ANALYSIS_SYSTEM = """\
Ты — старший инженер, принимающий решение о внедрении технологии в существующий
работающий проект. Ты скептичен: по умолчанию считаешь, что менять работающее
не надо, и ищешь конкретные причины, почему всё-таки надо.

Разбери находку по пунктам схемы. Особое внимание:
  * что именно она ЗАМЕНЯЕТ в текущем стеке, а что ДОПОЛНЯЕТ;
  * какие НОВЫЕ сервисы/зависимости придётся поднять и обслуживать;
  * maintenance burden, vendor lock-in, риски лицензии и безопасности;
  * сложность миграции с того, что есть сейчас.

verdict:
  CRITICAL     — решает острую текущую проблему проекта, выгода явно перевешивает
  RECOMMENDED  — заметное улучшение, цена внедрения приемлемая
  REVIEW_LATER — потенциал есть, но рано (незрелость, мало данных, не приоритет)
  REJECTED     — не даёт реального преимущества либо цена/риск слишком велики

verdict_reason — одна-две фразы, человеческим языком, с конкретикой.
Строго JSON по схеме.

ЯЗЫК ОТВЕТА — ТОЛЬКО РУССКИЙ. Это правило важнее всех остальных.
Исходный материал почти всегда на английском: описания репозиториев, README,
названия. Ты всё равно пишешь по-русски. Английскими остаются только имена
собственные: названия репозиториев, библиотек, языков, форматов и лицензий
(pdfplumber, FastAPI, Apache-2.0). Всё остальное — предложения, оценки,
причины, перечисления — по-русски. Ответ, скопированный из английского
описания, считается невыполненным заданием.
"""


def build_deep_analysis_prompt(
    *,
    project: dict[str, Any],
    finding: dict[str, Any],
    match: dict[str, Any],
    existing_features: list[dict[str, Any]],
) -> str:
    stack = project.get("current_stack") or {}
    stack_flat = " · ".join(
        f"{k}: {', '.join(v)}" for k, v in stack.items() if v
    ) or "не указан"
    features = "\n".join(
        f"- {f['name']}" + (f" — {f['description']}" if f.get("description") else "")
        for f in existing_features[:40]
    ) or "- (не заполнено)"

    rm = finding.get("repo_meta") or {}
    return f"""\
# ПРОЕКТ {project['slug']} — {project.get('name', '')}
{project.get('description') or ''}

Архитектура: {project.get('existing_architecture') or 'не описана'}
Текущий стек: {stack_flat}
Текущие проблемы: {'; '.join(project.get('current_problems') or []) or 'не заданы'}
НЕ нужно проекту: {'; '.join(project.get('things_not_needed') or []) or '—'}

## Что уже реализовано
{features}

# НАХОДКА
{finding.get('title')}
{finding.get('url') or ''}
★ {rm.get('stars', 0)} · {rm.get('language') or 'n/a'} · лицензия {rm.get('license_spdx') or 'не указана'}
Последний push: {rm.get('pushed_at') or 'n/a'} · релиз: {rm.get('latest_release_tag') or 'нет'}

## Почему радар её выбрал
relevance {match.get('relevance_score', 0):.2f} · project_fit {match.get('project_fit_score', 0):.2f} \
· improvement {match.get('improvement_score', 0):.2f} · risk {match.get('risk_score', 0):.2f}
{match.get('why_relevant') or ''}

Ближайшая существующая фича: {match.get('nearest_feature_name') or '—'} \
(similarity {match.get('max_similarity_to_features') or 0:.2f})

## README / описание
{_truncate(finding.get('content'), MAX_README_CHARS)}

Дай разбор строго по схеме.
"""


# ---------------------------------------------------------------- repo audit

REPO_AUDIT_SYSTEM = """\
Ты — технический ревизор. По содержимому репозитория восстанови профиль проекта:
что он делает, на чём написан, что в нём уже реализовано.

Правила:
  * current_stack — только то, что реально подтверждается файлами (зависимости,
    docker-compose, конфиги). Не додумывай.
  * existing_features — конкретные реализованные механизмы, а не общие слова.
    «Парсинг PDF через pdfplumber», а не «работа с документами».
  * search_keywords — 10–20 английских терминов, по которым имеет смысл искать
    на GitHub улучшения ДЛЯ ЭТОГО проекта.
  * negative_keywords — термины, которые точно дадут шум для этого проекта.
  * current_problems — только если в README/issues есть явные указания. Иначе пусто.

Строго JSON по схеме. description, business_purpose, existing_architecture,
названия и описания фич, current_problems — по-русски. Ключевые слова — по-английски.
"""


def build_repo_audit_prompt(*, repo_full_name: str, files: dict[str, str]) -> str:
    parts = [f"# РЕПОЗИТОРИЙ {repo_full_name}", ""]
    budget = 24_000
    for path, content in files.items():
        if budget <= 0:
            break
        chunk = _truncate(content, min(6000, budget))
        budget -= len(chunk)
        parts += [f"## {path}", "```", chunk, "```", ""]
    parts.append("Восстанови профиль проекта и верни JSON по схеме.")
    return "\n".join(parts)


__all__ = [
    "PROMPT_VERSION",
    "CLASSIFICATION_SYSTEM",
    "PROJECT_MATCH_SYSTEM",
    "DEEP_ANALYSIS_SYSTEM",
    "REPO_AUDIT_SYSTEM",
    "build_classification_prompt",
    "build_project_match_prompt",
    "build_deep_analysis_prompt",
    "build_repo_audit_prompt",
]
