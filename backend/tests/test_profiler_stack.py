"""Тесты детекта стека по манифестам. БД и сеть не нужны.

История: 14.09.2026 радар выдал 48 находок, где EngHub описан как
«Python, FastAPI, PostgreSQL». Проект написан на Express и TypeScript, но
техревизия читала только корневой package.json монорепозитория — а там одна
строка про exceljs. Пустой current_stack уходил в промпт, и модель дописывала
стек сама. Ошибка не роняет сборку и не даёт исключения, поэтому она и
прожила незамеченной — эти тесты её фиксируют.
"""
from __future__ import annotations

import json

from app.services.profiler import detect_stack_heuristically

# Корень ai-institut: заглушка, по которой нельзя понять ничего.
AI_INSTITUT_ROOT = json.dumps(
    {
        "scripts": {"build": "cd enghub-main && npm install && npm run build"},
        "dependencies": {"exceljs": "^4.4.0"},
    }
)

# Настоящий сервис — уровнем ниже.
AI_INSTITUT_API = json.dumps(
    {
        "name": "api-server",
        "dependencies": {
            "express": "^4.18.2",
            "typescript": "^5.3.3",
            "@supabase/supabase-js": "^2.39.0",
            "tesseract.js": "^7.0.0",
            "openai": "^4.20.0",
            "dxf-parser": "^1.1.2",
        },
    }
)

SAS_VFORMATE_API = json.dumps(
    {"name": "@vformate/api", "dependencies": {"@nestjs/core": "^10.0.0", "prisma": "^5.22.0"}}
)


def _flat(stack: dict[str, list[str]]) -> set[str]:
    return {label for labels in stack.values() for label in labels}


class TestMonorepoStack:
    def test_root_only_sees_almost_nothing(self):
        """Прежнее поведение: по одному корню стек не определяется."""
        stack = detect_stack_heuristically({"package.json": AI_INSTITUT_ROOT})
        assert "Express" not in _flat(stack)
        assert "TypeScript" not in _flat(stack)

    def test_workspace_manifest_reveals_real_stack(self):
        stack = detect_stack_heuristically(
            {
                "package.json": AI_INSTITUT_ROOT,
                "services/api-server/package.json": AI_INSTITUT_API,
            }
        )
        found = _flat(stack)
        assert "Express" in found
        assert "TypeScript" in found
        assert "Supabase" in found
        assert "Tesseract.js" in found

    def test_nested_path_counts_as_manifest(self):
        """Ключ приходит полным путём — сравнение должно идти по имени файла."""
        stack = detect_stack_heuristically(
            {"services/cad-engine/requirements.txt": "cadquery==2.5.2\nezdxf==1.4.4\n"}
        )
        assert _flat(stack) >= {"CadQuery", "ezdxf"}

    def test_cad_is_its_own_area(self):
        stack = detect_stack_heuristically(
            {"requirements.txt": "ifcopenshell==0.8.5\nezdxf==1.4.4\n"}
        )
        assert set(stack.get("cad", [])) >= {"IfcOpenShell", "ezdxf"}


class TestSignatureBoundaries:
    def test_scoped_npm_package_is_found(self):
        r"""\b вокруг «@nestjs/core» не работает: рядом с @ и / нет границы слова."""
        stack = detect_stack_heuristically({"apps/api/package.json": SAS_VFORMATE_API})
        found = _flat(stack)
        assert "NestJS" in found
        assert "Prisma" in found

    def test_substring_does_not_match_unrelated_word(self):
        """«next» не должно срабатывать внутри «nextcloud-client»."""
        stack = detect_stack_heuristically(
            {"requirements.txt": "nextcloud-client==1.0.0\n"}
        )
        assert "Next.js" not in _flat(stack)

    def test_files_outside_manifest_set_are_ignored(self):
        stack = detect_stack_heuristically({"README.md": "мы используем express и fastapi"})
        assert stack == {}
