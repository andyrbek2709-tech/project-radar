"""Нормализация: сырьё → кандидат в находки, плюс дешёвый фильтр без LLM.

Cheap filter срезает основную массу до того, как за неё заплачено.
Всё, что срезано, помечается filter_reason и в отчёт не попадает.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.core.config import settings
from app.models.finding import FindingKind

# Параметры-мусор, которые не влияют на содержимое страницы.
_TRACKING_PREFIXES = ("utm_", "ref_", "mc_", "pk_", "yclid", "fbclid", "gclid", "_ga")

# Хост: минимум одна точка, без пробелов и служебных символов.
# Без этой проверки urlparse("https://" + "не ссылка") отдаёт netloc "не ссылка",
# и любой кусок текста превращался бы в «ссылку».
_HOSTNAME_RE = re.compile(r"^[^\s/@:]+(?:\.[^\s/@:.]+)+$")

_GITHUB_REPO_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)",
    re.IGNORECASE,
)

# Служебные пути github.com, которые не являются репозиториями.
_GITHUB_RESERVED = frozenset({
    "features", "topics", "trending", "collections", "events", "about", "pricing",
    "marketplace", "sponsors", "settings", "notifications", "explore", "orgs",
    "login", "join", "apps", "readme", "security", "enterprise", "customer-stories",
})

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'`]+")

# Шум, который видно без модели.
_NOISE_PATTERNS = [
    re.compile(r"(?i)\b(вакансия|ваканси[ия]|мы ищем|ищем разработчика|резюме|hiring|we're hiring)\b"),
    re.compile(r"(?i)\b(скидк[аи]|промокод|розыгрыш|курс со скидкой|записывайтесь|реклама)\b"),
    re.compile(r"(?i)\b(подпис(ывайтесь|ка)|реферальн|партнёрск|erid[: ])"),
    re.compile(r"(?i)^#?(вебинар|митап|конференци)"),
]

MIN_CONTENT_LENGTH = 60


@dataclass(slots=True)
class NormalizedItem:
    """Кандидат в находки до дедупликации."""

    kind: str
    title: str
    url: str | None
    normalized_url: str | None
    content_text: str
    github_full_name: str | None = None
    github_urls: list[str] = field(default_factory=list)
    source_kind: str = ""
    published_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def title_hash(self) -> str:
        return hashlib.sha256(normalize_title(self.title).encode("utf-8")).hexdigest()


def normalize_url(url: str | None) -> str | None:
    """Схема → https, www срезан, tracking-параметры убраны, хвостовой слэш убран.

    Нужно, чтобы одна и та же ссылка из разных каналов схлопнулась в одну находку.
    """
    if not url:
        return None
    url = url.strip().rstrip(".,;:!?)»\"'")
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url

    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if not parsed.netloc:
        return None

    netloc = parsed.netloc.lower()
    if not _HOSTNAME_RE.match(netloc.split("@")[-1].split(":")[0]):
        return None
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.endswith(":443"):
        netloc = netloc[:-4]
    if netloc.endswith(":80"):
        netloc = netloc[:-3]

    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    path = parsed.path.rstrip("/") or "/"

    return urlunparse(("https", netloc, path, "", urlencode(sorted(query)), ""))


def normalize_title(title: str) -> str:
    """Для title_hash: регистр, пунктуация и пробелы не должны создавать дубли."""
    text = (title or "").lower().strip()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for raw in _URL_RE.findall(text):
        cleaned = raw.rstrip(".,;:!?)»\"'")
        if cleaned not in seen:
            seen.append(cleaned)
    return seen


def extract_github_repos(urls: list[str]) -> list[str]:
    """`owner/name` из github-ссылок. Служебные пути отсеиваются."""
    repos: list[str] = []
    for url in urls:
        m = _GITHUB_REPO_RE.match(url)
        if not m:
            continue
        owner, name = m.group(1), m.group(2)
        if owner.lower() in _GITHUB_RESERVED:
            continue
        if name.endswith(".git"):
            name = name[:-4]
        full = f"{owner}/{name}"
        if full not in repos:
            repos.append(full)
    return repos


# ------------------------------------------------------------- cheap filter


@dataclass(slots=True)
class FilterVerdict:
    passed: bool
    reason: str | None = None


def cheap_filter(
    item: NormalizedItem,
    *,
    negative_keywords: set[str] | None = None,
) -> FilterVerdict:
    """Фильтр без единого обращения к LLM.

    Порядок проверок — от самых дешёвых к более дорогим.
    """
    text = (item.content_text or "") + " " + (item.title or "")
    stripped = text.strip()

    if len(stripped) < MIN_CONTENT_LENGTH and not item.github_full_name:
        return FilterVerdict(False, "too_short")

    lowered = stripped.lower()

    for pattern in _NOISE_PATTERNS:
        if pattern.search(lowered):
            return FilterVerdict(False, "noise_pattern")

    if negative_keywords:
        for kw in negative_keywords:
            kw = kw.strip().lower()
            if kw and len(kw) > 2 and kw in lowered:
                return FilterVerdict(False, f"negative_keyword:{kw[:40]}")

    repo = item.extra.get("repo")
    if repo:
        verdict = _filter_repository(repo)
        if not verdict.passed:
            return verdict

    return FilterVerdict(True)


def _filter_repository(repo: dict[str, Any]) -> FilterVerdict:
    if repo.get("archived"):
        return FilterVerdict(False, "repo_archived")
    if repo.get("disabled"):
        return FilterVerdict(False, "repo_disabled")

    stars = int(repo.get("stars") or 0)
    if stars < settings.GITHUB_MIN_STARS:
        return FilterVerdict(False, f"stars_below_{settings.GITHUB_MIN_STARS}")

    pushed = repo.get("pushed_at_gh")
    if isinstance(pushed, str):
        pushed = _parse_dt(pushed)
    if isinstance(pushed, datetime):
        if pushed.tzinfo is None:
            pushed = pushed.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - pushed
        if age > timedelta(days=settings.GITHUB_STALE_PUSH_DAYS):
            return FilterVerdict(False, f"stale_push_{age.days}d")

    return FilterVerdict(True)


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------ normalization


def normalize_telegram_message(payload: dict[str, Any], source_kind: str) -> NormalizedItem:
    text = (payload.get("text") or "").strip()
    urls = payload.get("urls") or extract_urls(text)
    github_repos = extract_github_repos(urls)

    primary_url = None
    if github_repos:
        primary_url = f"https://github.com/{github_repos[0]}"
    elif urls:
        primary_url = urls[0]

    title = _first_meaningful_line(text) or (github_repos[0] if github_repos else "Сообщение Telegram")

    return NormalizedItem(
        kind=FindingKind.REPOSITORY if github_repos else FindingKind.DISCUSSION,
        title=title[:480],
        url=primary_url,
        normalized_url=normalize_url(primary_url),
        content_text=text,
        github_full_name=github_repos[0] if github_repos else None,
        github_urls=[f"https://github.com/{r}" for r in github_repos],
        source_kind=source_kind,
        published_at=_parse_dt(payload["date"]) if isinstance(payload.get("date"), str) else payload.get("date"),
        extra={"telegram": {k: payload.get(k) for k in ("chat_id", "message_id", "author")}},
    )


def normalize_github_repo(repo: dict[str, Any], source_kind: str) -> NormalizedItem:
    full_name = repo["full_name"]
    url = repo.get("url") or f"https://github.com/{full_name}"

    body_parts = [repo.get("description") or ""]
    if repo.get("topics"):
        body_parts.append("Topics: " + ", ".join(repo["topics"]))
    if repo.get("readme_text"):
        body_parts.append(repo["readme_text"][: settings.GITHUB_README_MAX_CHARS])

    return NormalizedItem(
        kind=FindingKind.REPOSITORY,
        title=f"{full_name} — {repo.get('description') or 'без описания'}"[:480],
        url=url,
        normalized_url=normalize_url(url),
        content_text="\n\n".join(p for p in body_parts if p).strip(),
        github_full_name=full_name,
        github_urls=[url],
        source_kind=source_kind,
        published_at=repo.get("created_at_gh"),
        extra={"repo": repo},
    )


def _first_meaningful_line(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip(" *#>—-•\t")
        if len(cleaned) >= 12:
            return cleaned
    return (text or "").strip()[:200]


__all__ = [
    "NormalizedItem",
    "FilterVerdict",
    "normalize_url",
    "normalize_title",
    "extract_urls",
    "extract_github_repos",
    "cheap_filter",
    "normalize_telegram_message",
    "normalize_github_repo",
]
