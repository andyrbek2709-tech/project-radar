"""Тесты нормализации и дешёвого фильтра. БД и сеть не нужны."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.normalizer import (
    NormalizedItem,
    cheap_filter,
    extract_github_repos,
    extract_urls,
    normalize_telegram_message,
    normalize_title,
    normalize_url,
)


class TestNormalizeUrl:
    def test_adds_scheme_and_strips_www(self):
        assert normalize_url("www.example.com/page") == "https://example.com/page"

    def test_strips_tracking_params(self):
        got = normalize_url("https://example.com/a?utm_source=tg&id=5&fbclid=xx")
        assert got == "https://example.com/a?id=5"

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/a/") == "https://example.com/a"

    def test_same_link_from_different_channels_collapses(self):
        """Ключевое свойство: одна ссылка из разных источников — одна находка."""
        a = normalize_url("http://www.github.com/owner/repo/?utm_medium=social")
        b = normalize_url("https://github.com/owner/repo")
        assert a == b

    def test_handles_none_and_garbage(self):
        assert normalize_url(None) is None
        assert normalize_url("") is None
        assert normalize_url("не ссылка") is None


class TestNormalizeTitle:
    def test_case_and_punctuation_do_not_create_duplicates(self):
        assert normalize_title("Docling: PDF → Markdown!") == normalize_title(
            "docling  pdf   markdown"
        )


class TestExtractors:
    def test_extracts_urls_and_strips_trailing_punctuation(self):
        urls = extract_urls("Смотри https://github.com/a/b, полезно.")
        assert urls == ["https://github.com/a/b"]

    def test_extracts_github_repos(self):
        urls = ["https://github.com/docling-project/docling", "https://example.com"]
        assert extract_github_repos(urls) == ["docling-project/docling"]

    def test_ignores_github_service_paths(self):
        """github.com/trending — не репозиторий."""
        urls = [
            "https://github.com/trending/python",
            "https://github.com/features/copilot",
            "https://github.com/marketplace/actions",
        ]
        assert extract_github_repos(urls) == []

    def test_strips_dot_git(self):
        assert extract_github_repos(["https://github.com/a/b.git"]) == ["a/b"]


class TestCheapFilter:
    def _item(self, text: str, **kw) -> NormalizedItem:
        return NormalizedItem(
            kind="discussion",
            title=text[:100],
            url=None,
            normalized_url=None,
            content_text=text,
            **kw,
        )

    def test_rejects_too_short(self):
        verdict = cheap_filter(self._item("коротко"))
        assert not verdict.passed
        assert verdict.reason == "too_short"

    def test_rejects_job_posting(self):
        text = "Мы ищем разработчика Python в команду, удалёнка, зарплата по итогам собеседования, пишите в личку"
        verdict = cheap_filter(self._item(text))
        assert not verdict.passed
        assert verdict.reason == "noise_pattern"

    def test_rejects_negative_keyword(self):
        text = "Новый фреймворк для торговли crypto на бирже с автоматическими стратегиями и бэктестом"
        verdict = cheap_filter(self._item(text), negative_keywords={"crypto"})
        assert not verdict.passed
        assert verdict.reason.startswith("negative_keyword")

    def test_passes_meaningful_content(self):
        text = (
            "Вышла библиотека для извлечения таблиц из PDF с сохранением структуры "
            "и поддержкой сканов через OCR, работает на docling"
        )
        assert cheap_filter(self._item(text)).passed

    def test_rejects_archived_repository(self):
        item = self._item("x" * 200, extra={"repo": {"archived": True, "stars": 5000}})
        verdict = cheap_filter(item)
        assert not verdict.passed
        assert verdict.reason == "repo_archived"

    def test_rejects_stale_repository(self):
        old = datetime.now(timezone.utc) - timedelta(days=900)
        item = self._item(
            "x" * 200,
            extra={"repo": {"stars": 5000, "pushed_at_gh": old.isoformat()}},
        )
        verdict = cheap_filter(item)
        assert not verdict.passed
        assert verdict.reason.startswith("stale_push")

    def test_passes_fresh_popular_repository(self):
        fresh = datetime.now(timezone.utc) - timedelta(days=2)
        item = self._item(
            "x" * 200,
            extra={"repo": {"stars": 5000, "pushed_at_gh": fresh.isoformat()}},
        )
        assert cheap_filter(item).passed


class TestTelegramNormalization:
    def test_github_link_makes_it_a_repository_finding(self):
        payload = {
            "text": "Отличная штука для парсинга PDF: https://github.com/docling-project/docling",
            "date": "2026-09-13T10:00:00+00:00",
            "urls": ["https://github.com/docling-project/docling"],
        }
        item = normalize_telegram_message(payload, "telegram")
        assert item.kind == "repository"
        assert item.github_full_name == "docling-project/docling"
        assert item.normalized_url == "https://github.com/docling-project/docling"

    def test_plain_message_is_discussion(self):
        payload = {"text": "Просто размышление про архитектуру микросервисов", "date": None}
        item = normalize_telegram_message(payload, "telegram")
        assert item.kind == "discussion"
        assert item.github_full_name is None
