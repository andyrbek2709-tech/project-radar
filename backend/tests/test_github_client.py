"""README приезжает сырым текстом под «джейсоновым» Content-Type.

GitHub отвечает на Accept: application/vnd.github.raw+json заголовком
Content-Type: application/vnd.github.raw+json, а телом отдаёт markdown.
Проверка «json в content-type» на этом ломалась: json.loads падал на первом
же символе, README не сохранялся, и находка оценивалась по одному описанию репо.
"""
from __future__ import annotations

import httpx

from app.collectors.github_client import GitHubClient

README = "# Project Radar\n\nПерсональный технический радар.\n"


def _client(handler) -> GitHubClient:
    return GitHubClient(token="t", transport=httpx.MockTransport(handler))


class TestReadme:
    def test_raw_markdown_under_json_content_type(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Accept"] == "application/vnd.github.raw+json"
            return httpx.Response(
                200,
                text=README,
                headers={"content-type": "application/vnd.github.raw+json; charset=utf-8"},
            )

        resp = _client(handler).get_readme("owner/repo")
        assert resp.status == 200
        assert resp.data == README

    def test_plain_text_content_type_still_works(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text=README, headers={"content-type": "text/plain; charset=utf-8"}
            )

        assert _client(handler).get_readme("owner/repo").data == README

    def test_missing_readme_is_not_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        resp = _client(handler).get_readme("owner/repo")
        assert resp.status == 404
        assert resp.data is None


class TestJsonEndpoints:
    def test_real_json_is_still_parsed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"tag_name": "v2.0.0"},
                headers={"content-type": "application/json; charset=utf-8"},
            )

        assert _client(handler).get_latest_release("owner/repo") == {"tag_name": "v2.0.0"}

    def test_broken_json_body_degrades_to_text_instead_of_raising(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="not json at all", headers={"content-type": "application/json"}
            )

        assert _client(handler).get_repo("owner/repo").data == "not json at all"
