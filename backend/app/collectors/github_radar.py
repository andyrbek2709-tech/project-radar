"""GitHub Radar — собственный поиск, а не только то, что всплыло в Telegram.

Стратегии: новые репы по темам, быстрый рост (по нашим снапшотам), свежие
релизы отслеживаемых реп, поиск по search-профилям проектов, высокая
commit-активность, интересные форки.

Рост звёзд считается ТОЛЬКО собственными снапшотами: /stargazers закрыт
с 30.06.2026, официального trending API не существует.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.collectors.github_client import GitHubClient, RateLimited, map_repo_payload
from app.models.project import Project
from app.models.repository import Repository, RepositorySnapshot
from app.models.source import CollectorRun, ProcessingStatus, RawItem, Source, SourceKind

log = get_logger("github")

GROWTH_WINDOW_DAYS = 14

# Базовые темы радара. Дополняются search_keywords из профилей проектов.
BASE_TOPICS = [
    "rag", "llm", "vector-database", "document-ai", "ocr",
    "fastapi", "celery", "postgresql", "nextjs", "pgvector",
    "agents", "embeddings", "pdf", "structured-output",
]


@dataclass(slots=True)
class SearchStrategy:
    name: str
    query: str
    sort: str = "stars"
    limit: int = 60


def build_strategies(session: Session) -> list[SearchStrategy]:
    """Собрать набор запросов. Окна по датам — чтобы не упираться в потолок 1000."""
    today = date.today()
    week_ago = (today - timedelta(days=7)).isoformat()
    two_days = (today - timedelta(days=2)).isoformat()
    month_ago = (today - timedelta(days=settings.GITHUB_MAX_REPO_AGE_DAYS)).isoformat()
    min_stars = settings.GITHUB_MIN_STARS

    strategies: list[SearchStrategy] = []

    # 1. Новое по базовым темам.
    for topic in BASE_TOPICS:
        strategies.append(
            SearchStrategy(
                name=f"new:{topic}",
                query=f"topic:{topic} created:>{month_ago} stars:>{min_stars}",
                sort="stars",
                limit=40,
            )
        )

    # 2. Поиск по профилям проектов — то, что нужно именно нам.
    projects = session.execute(
        select(Project).where(Project.is_active.is_(True))
    ).scalars().all()
    for project in projects:
        for keyword in (project.search_keywords or [])[:12]:
            keyword = keyword.strip()
            if len(keyword) < 3:
                continue
            strategies.append(
                SearchStrategy(
                    name=f"project:{project.slug}:{keyword}",
                    query=f'"{keyword}" created:>{month_ago} stars:>{max(10, min_stars // 2)}',
                    sort="stars",
                    limit=30,
                )
            )

    # 3. Высокая активность у уже заметных проектов.
    strategies.append(
        SearchStrategy(
            name="high-activity",
            query=f"pushed:>{two_days} stars:>200 language:python",
            sort="updated",
            limit=50,
        )
    )
    strategies.append(
        SearchStrategy(
            name="high-activity-ts",
            query=f"pushed:>{two_days} stars:>200 language:typescript",
            sort="updated",
            limit=40,
        )
    )

    # 4. Резкий старт: молодое и уже заметное.
    strategies.append(
        SearchStrategy(
            name="fast-start",
            query=f"created:>{week_ago} stars:>100",
            sort="stars",
            limit=60,
        )
    )

    return strategies


def get_or_create_source(session: Session, kind: str, external_id: str, title: str) -> Source:
    source = session.execute(
        select(Source).where(Source.kind == kind, Source.external_id == external_id)
    ).scalar_one_or_none()
    if source is None:
        source = Source(kind=kind, external_id=external_id, title=title, is_active=True)
        session.add(source)
        session.flush()
    return source


def upsert_repository(session: Session, payload: dict[str, Any]) -> Repository:
    """Репозиторий ищется по github_id — он стабилен при переименовании."""
    github_id = payload.get("github_id")
    repo: Repository | None = None

    if github_id:
        repo = session.execute(
            select(Repository).where(Repository.github_id == github_id)
        ).scalar_one_or_none()
    if repo is None and payload.get("full_name"):
        repo = session.execute(
            select(Repository).where(Repository.full_name == payload["full_name"])
        ).scalar_one_or_none()

    if repo is None:
        repo = Repository(**{k: v for k, v in payload.items() if v is not None})
        session.add(repo)
        session.flush()
        return repo

    for key, value in payload.items():
        if value is not None and hasattr(repo, key):
            setattr(repo, key, value)
    repo.last_checked_at = datetime.now(timezone.utc)
    return repo


def snapshot_repository(session: Session, repo: Repository, *, on: date | None = None) -> None:
    """Один снапшот в сутки. Повторный вызов за те же сутки обновляет запись."""
    captured = on or date.today()
    stmt = (
        pg_insert(RepositorySnapshot)
        .values(
            repository_id=repo.id,
            captured_on=captured,
            stars=repo.stars,
            forks=repo.forks,
            watchers=repo.watchers,
            open_issues=repo.open_issues,
            contributors_count=repo.contributors_count,
            commits_last_week=repo.commits_last_week,
            latest_release_tag=repo.latest_release_tag,
            license_spdx=repo.license_spdx,
            pushed_at_gh=repo.pushed_at_gh,
        )
        .on_conflict_do_update(
            constraint="uq_repo_snapshot_day",
            set_={
                "stars": repo.stars,
                "forks": repo.forks,
                "watchers": repo.watchers,
                "open_issues": repo.open_issues,
                "contributors_count": repo.contributors_count,
                "commits_last_week": repo.commits_last_week,
                "latest_release_tag": repo.latest_release_tag,
                "license_spdx": repo.license_spdx,
                "pushed_at_gh": repo.pushed_at_gh,
            },
        )
    )
    session.execute(stmt)


def compute_growth(
    session: Session, repo: Repository, *, window_days: int = GROWTH_WINDOW_DAYS
) -> dict[str, Any]:
    """Прирост звёзд по нашим снапшотам. Единственный доступный способ."""
    cutoff = date.today() - timedelta(days=window_days)
    old = session.execute(
        select(RepositorySnapshot)
        .where(
            RepositorySnapshot.repository_id == repo.id,
            RepositorySnapshot.captured_on <= cutoff,
        )
        .order_by(RepositorySnapshot.captured_on.desc())
        .limit(1)
    ).scalar_one_or_none()

    if old is None:
        # Нет истории — вернём самый старый из имеющихся, если он не сегодняшний.
        old = session.execute(
            select(RepositorySnapshot)
            .where(RepositorySnapshot.repository_id == repo.id)
            .order_by(RepositorySnapshot.captured_on.asc())
            .limit(1)
        ).scalar_one_or_none()

    if old is None or old.captured_on >= date.today():
        return {"stars_delta": None, "stars_growth_ratio": None, "growth_window_days": None}

    delta = repo.stars - (old.stars or 0)
    ratio = delta / old.stars if old.stars else None
    return {
        "stars_delta": delta,
        "stars_growth_ratio": ratio,
        "growth_window_days": (date.today() - old.captured_on).days,
    }


_NO_GROWTH: dict[str, Any] = {
    "stars_delta": None,
    "stars_growth_ratio": None,
    "growth_window_days": None,
}


def compute_growth_bulk(
    session: Session,
    repos: Sequence[Repository],
    *,
    window_days: int = GROWTH_WINDOW_DAYS,
) -> dict[uuid.UUID, dict[str, Any]]:
    """То же самое, что compute_growth, но двумя запросами на всю страницу.

    В списке находок рост считался по каждой строке отдельно — это два SELECT
    на репозиторий, то есть под сотню лишних запросов на страницу в полсотни
    находок. Логика здесь повторяет compute_growth один в один, включая откат
    на самый старый снапшот, когда в окно ничего не попало.
    """
    if not repos:
        return {}

    today = date.today()
    cutoff = today - timedelta(days=window_days)
    ids = [r.id for r in repos]

    def _latest(where_older_than_cutoff: bool) -> dict[uuid.UUID, RepositorySnapshot]:
        stmt = select(RepositorySnapshot).where(RepositorySnapshot.repository_id.in_(ids))
        if where_older_than_cutoff:
            stmt = stmt.where(RepositorySnapshot.captured_on <= cutoff)
            order = RepositorySnapshot.captured_on.desc()
        else:
            order = RepositorySnapshot.captured_on.asc()
        # DISTINCT ON — по одной строке на репозиторий силами БД, без выборки
        # всей истории в питон. БД здесь всегда PostgreSQL (нужен pgvector).
        stmt = stmt.distinct(RepositorySnapshot.repository_id).order_by(
            RepositorySnapshot.repository_id, order
        )
        return {s.repository_id: s for s in session.execute(stmt).scalars().all()}

    in_window = _latest(True)
    oldest = _latest(False)

    result: dict[uuid.UUID, dict[str, Any]] = {}
    for repo in repos:
        old = in_window.get(repo.id) or oldest.get(repo.id)
        if old is None or old.captured_on >= today:
            result[repo.id] = dict(_NO_GROWTH)
            continue
        delta = repo.stars - (old.stars or 0)
        result[repo.id] = {
            "stars_delta": delta,
            "stars_growth_ratio": delta / old.stars if old.stars else None,
            "growth_window_days": (today - old.captured_on).days,
        }
    return result


def enrich_repository(
    client: GitHubClient, session: Session, repo: Repository, *, fetch_readme: bool = True
) -> None:
    """Добор того, чего нет в результатах поиска. ETag экономит лимит."""
    if fetch_readme and _readme_is_stale(repo):
        try:
            resp = client.get_readme(repo.full_name, etag=repo.readme_etag)
            if resp.status == 200 and isinstance(resp.data, str):
                repo.readme_text = resp.data[: settings.GITHUB_README_MAX_CHARS]
                repo.readme_etag = resp.etag
                repo.readme_fetched_at = datetime.now(timezone.utc)
            elif resp.not_modified:
                repo.readme_fetched_at = datetime.now(timezone.utc)
        except RateLimited:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("github_readme_failed", repo=repo.full_name, error=str(exc)[:200])

    if repo.latest_release_tag is None:
        try:
            release = client.get_latest_release(repo.full_name)
            if release:
                repo.latest_release_tag = release.get("tag_name")
                published = release.get("published_at")
                if published:
                    repo.latest_release_at = datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    )
        except RateLimited:
            raise
        except Exception:  # noqa: BLE001
            pass

    if repo.contributors_count is None:
        try:
            repo.contributors_count = client.get_contributors_count(repo.full_name)
        except RateLimited:
            raise
        except Exception:  # noqa: BLE001
            pass

    if repo.commits_last_week is None:
        try:
            repo.commits_last_week = client.get_commit_activity(repo.full_name)
        except RateLimited:
            raise
        except Exception:  # noqa: BLE001
            pass


def _readme_is_stale(repo: Repository) -> bool:
    if repo.readme_text is None:
        return True
    if repo.readme_fetched_at is None:
        return True
    age = datetime.now(timezone.utc) - repo.readme_fetched_at
    return age > timedelta(days=14)


def store_raw_item(
    session: Session, source: Source, repo: Repository, growth: dict[str, Any]
) -> RawItem | None:
    """UNIQUE(source_id, external_id) не даёт анализировать одно и то же дважды."""
    external_id = f"gh:repo:{repo.github_id}"
    existing = session.execute(
        select(RawItem).where(
            RawItem.source_id == source.id, RawItem.external_id == external_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    payload = {
        "github_id": repo.github_id,
        "full_name": repo.full_name,
        "url": repo.url,
        "description": repo.description,
        "language": repo.language,
        "topics": list(repo.topics or []),
        "license_spdx": repo.license_spdx,
        "stars": repo.stars,
        "forks": repo.forks,
        "watchers": repo.watchers,
        "open_issues": repo.open_issues,
        "contributors_count": repo.contributors_count,
        "commits_last_week": repo.commits_last_week,
        "archived": repo.archived,
        "disabled": repo.disabled,
        "is_fork": repo.is_fork,
        "parent_full_name": repo.parent_full_name,
        "created_at_gh": _iso(repo.created_at_gh),
        "pushed_at_gh": _iso(repo.pushed_at_gh),
        "latest_release_tag": repo.latest_release_tag,
        "latest_release_at": _iso(repo.latest_release_at),
        "readme_text": repo.readme_text,
        **growth,
    }

    item = RawItem(
        source_id=source.id,
        external_id=external_id,
        kind="github_repo",
        payload=json.loads(json.dumps(payload, default=str)),
        content_text=(repo.description or "") + "\n\n" + (repo.readme_text or "")[:4000],
        urls=[repo.url],
        github_urls=[repo.url],
        author=repo.owner,
        published_at=repo.created_at_gh,
        processing_status=ProcessingStatus.PENDING,
    )
    session.add(item)
    session.flush()
    return item


def run_github_radar(session: Session, *, strategies: Iterable[SearchStrategy] | None = None) -> dict[str, Any]:
    """Один полный прогон радара. Возвращает статистику для collector_runs."""
    if not (settings.GITHUB_SEARCH_ENABLED and settings.GITHUB_TOKEN):
        log.warning("github_radar_disabled", has_token=bool(settings.GITHUB_TOKEN))
        return {"status": "skipped", "reason": "disabled_or_no_token"}

    strategies = list(strategies or build_strategies(session))
    run = CollectorRun(collector="github_search", status="running")
    session.add(run)
    session.flush()

    fetched = new_items = skipped = 0
    status = "ok"
    error: str | None = None

    with GitHubClient() as client:
        for strategy in strategies:
            source = get_or_create_source(
                session, SourceKind.GITHUB_SEARCH, strategy.name, f"GitHub: {strategy.name}"
            )
            try:
                items = client.search_repositories(
                    strategy.query, sort=strategy.sort, limit=strategy.limit
                )
            except RateLimited as exc:
                log.warning("github_rate_limited", strategy=strategy.name, reset_at=str(exc.reset_at))
                status = "partial"
                error = f"rate limited on {strategy.name}"
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("github_search_failed", strategy=strategy.name, error=str(exc)[:200])
                status = "partial"
                continue

            source.last_run_at = datetime.now(timezone.utc)

            for item in items:
                fetched += 1
                payload = map_repo_payload(item)
                if not payload.get("github_id"):
                    skipped += 1
                    continue

                repo = upsert_repository(session, payload)
                try:
                    enrich_repository(client, session, repo)
                except RateLimited:
                    status = "partial"
                    error = "rate limited during enrich"
                    break

                snapshot_repository(session, repo)
                growth = compute_growth(session, repo)

                if store_raw_item(session, source, repo, growth) is not None:
                    new_items += 1
                else:
                    skipped += 1

            session.flush()
            if status == "partial" and error:
                break

        run.api_requests = client.requests_made
        run.rate_limit_remaining = client.rate_limit_remaining

    run.items_fetched = fetched
    run.items_new = new_items
    run.items_skipped = skipped
    run.status = status
    run.error = error
    run.finished_at = datetime.now(timezone.utc)

    log.info(
        "github_radar_done",
        fetched=fetched,
        new=new_items,
        skipped=skipped,
        api_requests=run.api_requests,
        rate_limit_remaining=run.rate_limit_remaining,
        status=status,
    )
    return {
        "status": status,
        "fetched": fetched,
        "new": new_items,
        "skipped": skipped,
        "api_requests": run.api_requests,
    }


def refresh_tracked_repositories(session: Session, *, limit: int = 200) -> dict[str, Any]:
    """Ежедневный добор метрик для уже известных реп — через GraphQL-батчи.

    Именно это наполняет repository_snapshots, из которых считается рост.
    """
    if not settings.GITHUB_TOKEN:
        return {"status": "skipped", "reason": "no_token"}

    repos = session.execute(
        select(Repository)
        .where(Repository.archived.is_(False), Repository.disabled.is_(False))
        .order_by(Repository.stars.desc())
        .limit(limit)
    ).scalars().all()
    if not repos:
        return {"status": "ok", "updated": 0}

    updated = 0
    with GitHubClient() as client:
        for i in range(0, len(repos), 50):
            batch = repos[i : i + 50]
            try:
                metrics = client.graphql_repo_metrics([r.full_name for r in batch])
            except Exception as exc:  # noqa: BLE001
                log.warning("github_graphql_batch_failed", error=str(exc)[:200])
                continue

            for repo in batch:
                data = metrics.get(repo.full_name)
                if not data:
                    continue
                for key, value in data.items():
                    if value is not None and hasattr(repo, key):
                        setattr(repo, key, value)
                repo.last_checked_at = datetime.now(timezone.utc)
                snapshot_repository(session, repo)
                updated += 1
            session.flush()

    log.info("github_refresh_done", updated=updated)
    return {"status": "ok", "updated": updated}


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


__all__ = [
    "SearchStrategy",
    "build_strategies",
    "run_github_radar",
    "refresh_tracked_repositories",
    "upsert_repository",
    "snapshot_repository",
    "compute_growth",
    "compute_growth_bulk",
    "enrich_repository",
    "get_or_create_source",
]
