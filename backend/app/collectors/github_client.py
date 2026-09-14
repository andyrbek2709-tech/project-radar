"""Тонкий клиент GitHub API.

Написан руками осознанно: поведение вокруг лимитов здесь — не деталь,
а суть коллектора, и прятать его за чужой абстракцией дороже, чем держать.

Факты, проверенные на docs.github.com (сентябрь 2026), под которые он написан:
  * core для PAT — 5000 запросов/час; SEARCH — 30 запросов/МИНУТУ (отдельная квота);
  * `/search/repositories` отдаёт не более 1000 результатов на запрос —
    пагинация за этот предел бессмысленна, надо резать запрос на окна;
  * ответ 304 Not Modified НЕ расходует основной лимит → ETag обязателен;
  * `/stats/commit_activity` может вернуть 202 «считаю» — это не ошибка;
  * `/releases/latest` отдаёт 404, если релизов нет;
  * официального trending API не существует;
  * `/stargazers` с 30.06.2026 доступен только админам/коллабораторам,
    поэтому starred_at мы не используем — рост считаем своими снапшотами.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("github")

API = settings.GITHUB_API_URL.rstrip("/")
SEARCH_MAX_RESULTS = 1000
SEARCH_PER_PAGE = 100
# 30 запросов в минуту → 2 секунды между поисковыми запросами с запасом.
SEARCH_MIN_INTERVAL = 2.1


class GitHubError(RuntimeError):
    pass


class RateLimited(GitHubError):
    def __init__(self, reset_at: datetime | None, resource: str = "core") -> None:
        self.reset_at = reset_at
        self.resource = resource
        super().__init__(f"GitHub rate limit ({resource}), reset at {reset_at}")


@dataclass(slots=True)
class Response:
    status: int
    data: Any
    etag: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.token = token or settings.GITHUB_TOKEN
        self.requests_made = 0
        self.rate_limit_remaining: int | None = None
        self._last_search_at = 0.0
        self._client = httpx.Client(
            timeout=30.0,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "project-radar/1.0",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- core

    def _request(
        self,
        method: str,
        path: str,
        *,
        etag: str | None = None,
        accept: str | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_404: bool = False,
        raw: bool = False,
    ) -> Response:
        url = path if path.startswith("http") else f"{API}{path}"
        headers: dict[str, str] = {}
        if etag:
            # 304 не расходует основной лимит — это главная экономия коллектора.
            headers["If-None-Match"] = etag
        if accept:
            headers["Accept"] = accept

        resp = self._client.request(method, url, headers=headers, params=params, json=json_body)
        self.requests_made += 1

        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None:
            self.rate_limit_remaining = int(remaining)

        if resp.status_code == 304:
            return Response(304, None, etag, dict(resp.headers))

        if resp.status_code == 404 and allow_404:
            return Response(404, None, None, dict(resp.headers))

        if resp.status_code in (403, 429):
            self._raise_rate_limit(resp)

        if resp.status_code >= 400:
            raise GitHubError(f"GitHub {resp.status_code} {method} {url}: {resp.text[:300]}")

        data: Any = None
        if resp.content:
            ctype = resp.headers.get("content-type", "")
            if raw or "json" not in ctype:
                data = resp.text
            else:
                try:
                    data = resp.json()
                except ValueError:
                    # Сырой контент под «джейсоновым» Content-Type: GitHub отдаёт
                    # README как application/vnd.github.raw+json — суффикс +json
                    # в заголовке есть, JSON внутри нет. Проверено на живом API.
                    data = resp.text

        return Response(resp.status_code, data, resp.headers.get("etag"), dict(resp.headers))

    def _raise_rate_limit(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("x-ratelimit-remaining")
        resource = resp.headers.get("x-ratelimit-resource", "core")
        retry_after = resp.headers.get("retry-after")

        if retry_after:
            reset_at = datetime.now(timezone.utc) + timedelta(seconds=int(retry_after))
            raise RateLimited(reset_at, resource)
        if remaining == "0":
            reset = resp.headers.get("x-ratelimit-reset")
            reset_at = (
                datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset else None
            )
            raise RateLimited(reset_at, resource)
        raise GitHubError(f"GitHub {resp.status_code}: {resp.text[:300]}")

    # ----------------------------------------------------------- search

    def search_repositories(
        self,
        query: str,
        *,
        sort: str = "stars",
        order: str = "desc",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Поиск с уважением к лимиту 30/мин и к потолку в 1000 результатов."""
        limit = min(limit, SEARCH_MAX_RESULTS)
        collected: list[dict[str, Any]] = []
        page = 1

        while len(collected) < limit:
            self._throttle_search()
            per_page = min(SEARCH_PER_PAGE, limit - len(collected))
            resp = self._request(
                "GET",
                "/search/repositories",
                params={
                    "q": query,
                    "sort": sort,
                    "order": order,
                    "per_page": per_page,
                    "page": page,
                },
            )
            payload = resp.data or {}
            items = payload.get("items") or []
            collected.extend(items)

            if payload.get("incomplete_results"):
                log.warning("github_search_incomplete", query=query)
            if len(items) < per_page:
                break
            page += 1
            # Дальше 1000 GitHub всё равно не отдаст.
            if page * SEARCH_PER_PAGE > SEARCH_MAX_RESULTS:
                break

        log.info("github_search", query=query, found=len(collected))
        return collected

    def _throttle_search(self) -> None:
        elapsed = time.monotonic() - self._last_search_at
        if elapsed < SEARCH_MIN_INTERVAL:
            time.sleep(SEARCH_MIN_INTERVAL - elapsed)
        self._last_search_at = time.monotonic()

    # ------------------------------------------------------------ repo

    def get_repo(self, full_name: str, *, etag: str | None = None) -> Response:
        return self._request("GET", f"/repos/{full_name}", etag=etag, allow_404=True)

    def get_readme(self, full_name: str, *, etag: str | None = None) -> Response:
        return self._request(
            "GET",
            f"/repos/{full_name}/readme",
            etag=etag,
            accept="application/vnd.github.raw+json",
            allow_404=True,
            raw=True,
        )

    def get_latest_release(self, full_name: str) -> dict[str, Any] | None:
        """404 здесь — нормальное состояние «релизов нет», а не ошибка."""
        resp = self._request("GET", f"/repos/{full_name}/releases/latest", allow_404=True)
        return resp.data if resp.status == 200 else None

    def get_contributors_count(self, full_name: str) -> int | None:
        """Считаем по Link: rel="last" — одна страница вместо всех."""
        try:
            resp = self._request(
                "GET",
                f"/repos/{full_name}/contributors",
                params={"per_page": 1, "anon": "1"},
                allow_404=True,
            )
        except GitHubError:
            return None
        if resp.status != 200:
            return None

        link = resp.headers.get("link", "")
        if 'rel="last"' in link:
            for part in link.split(","):
                if 'rel="last"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    for chunk in url.split("?", 1)[-1].split("&"):
                        if chunk.startswith("page="):
                            try:
                                return int(chunk[5:])
                            except ValueError:
                                return None
        return len(resp.data) if isinstance(resp.data, list) else None

    def get_commit_activity(self, full_name: str, *, retries: int = 2) -> int | None:
        """202 = GitHub считает статистику. Ждём и пробуем ещё раз."""
        for attempt in range(retries + 1):
            resp = self._request(
                "GET", f"/repos/{full_name}/stats/commit_activity", allow_404=True
            )
            if resp.status == 202:
                if attempt < retries:
                    time.sleep(2.5)
                    continue
                return None
            if resp.status != 200 or not isinstance(resp.data, list) or not resp.data:
                return None
            return int(resp.data[-1].get("total", 0))
        return None

    # --------------------------------------------------------- graphql

    def graphql_repo_metrics(self, full_names: list[str]) -> dict[str, dict[str, Any]]:
        """Метрики пачкой: 50 репозиториев ≈ 1 point вместо 50 REST-вызовов."""
        if not full_names:
            return {}

        aliases: list[str] = []
        for i, full in enumerate(full_names[:50]):
            owner, _, name = full.partition("/")
            aliases.append(
                f'  r{i}: repository(owner: "{owner}", name: "{name}") {{\n'
                f"    nameWithOwner stargazerCount forkCount "
                f"watchers {{ totalCount }} issues(states: OPEN) {{ totalCount }}\n"
                f"    pushedAt updatedAt isArchived isDisabled\n"
                f"    licenseInfo {{ spdxId }}\n"
                f"    latestRelease {{ tagName publishedAt }}\n"
                f"  }}"
            )
        query = "query {\n" + "\n".join(aliases) + "\n  rateLimit { remaining resetAt }\n}"

        resp = self._client.post(f"{API}/graphql", json={"query": query})
        self.requests_made += 1
        if resp.status_code >= 400:
            raise GitHubError(f"GraphQL {resp.status_code}: {resp.text[:300]}")

        body = resp.json()
        if body.get("errors"):
            log.warning("github_graphql_errors", errors=str(body["errors"])[:300])

        out: dict[str, dict[str, Any]] = {}
        for key, value in (body.get("data") or {}).items():
            if key == "rateLimit" or not value:
                continue
            out[value["nameWithOwner"]] = {
                "stars": value.get("stargazerCount", 0),
                "forks": value.get("forkCount", 0),
                "watchers": (value.get("watchers") or {}).get("totalCount", 0),
                "open_issues": (value.get("issues") or {}).get("totalCount", 0),
                "pushed_at_gh": _parse(value.get("pushedAt")),
                "updated_at_gh": _parse(value.get("updatedAt")),
                "archived": value.get("isArchived", False),
                "disabled": value.get("isDisabled", False),
                "license_spdx": (value.get("licenseInfo") or {}).get("spdxId"),
                "latest_release_tag": (value.get("latestRelease") or {}).get("tagName"),
                "latest_release_at": _parse((value.get("latestRelease") or {}).get("publishedAt")),
            }
        return out


def map_repo_payload(item: dict[str, Any]) -> dict[str, Any]:
    """REST-ответ GitHub → плоская структура под нашу модель Repository."""
    full_name = item.get("full_name") or ""
    owner = (item.get("owner") or {}).get("login") or full_name.partition("/")[0]
    return {
        "github_id": item.get("id"),
        "full_name": full_name,
        "owner": owner,
        "name": item.get("name") or full_name.partition("/")[2],
        "url": item.get("html_url") or f"https://github.com/{full_name}",
        "description": item.get("description"),
        "homepage": item.get("homepage"),
        "language": item.get("language"),
        "topics": item.get("topics") or [],
        "license_spdx": ((item.get("license") or {}) or {}).get("spdx_id"),
        "default_branch": item.get("default_branch"),
        "is_fork": bool(item.get("fork")),
        "parent_full_name": ((item.get("parent") or {}) or {}).get("full_name"),
        "archived": bool(item.get("archived")),
        "disabled": bool(item.get("disabled")),
        "created_at_gh": _parse(item.get("created_at")),
        "updated_at_gh": _parse(item.get("updated_at")),
        "pushed_at_gh": _parse(item.get("pushed_at")),
        "stars": item.get("stargazers_count") or 0,
        "forks": item.get("forks_count") or 0,
        "watchers": item.get("watchers_count") or 0,
        "open_issues": item.get("open_issues_count") or 0,
    }


def _parse(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "GitHubClient",
    "GitHubError",
    "RateLimited",
    "Response",
    "map_repo_payload",
    "SEARCH_MAX_RESULTS",
]
