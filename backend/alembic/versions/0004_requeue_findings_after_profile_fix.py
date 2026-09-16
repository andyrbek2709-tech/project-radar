"""Вернуть на переоценку находки, разобранные по прежнему профилю.

Миграция 0003 починила профили всех семи проектов, но уже разобранные находки
остались с прежним вердиктом: `reanalyse_pending` выбирает `status='analyzed'`,
а решённая находка лежит в `decided`, и вернуть её оттуда умел только
reassessment-триггер — он следит за репозиторием находки, не за профилем
проекта. Поэтому выгрузка от 16.09.2026 по-прежнему описывала EngHub как
«Python, FastAPI, PostgreSQL» и советовала pydantic, стоящий в трёх его
сервисах: разбор был сделан до 0003, а пересматривать его было некому.

Дальше это лечится само — `app.services.profile_reanalysis` возвращает находки
при каждой правке профиля (сид, миграция, PATCH из интерфейса). Здесь разовая
уборка того, что уже принято.

Критерий точный, а не «вернуть всё подряд»: находка возвращается, только если
хотя бы у одного сопоставленного проекта её разбор `project_match` старше
`projects.updated_at`, то есть сделан до правки профиля. Кэш `finding_analyses`
пересчёту не мешает — `input_digest` считается от текста промпта, куда входит
профиль, так что после его правки кэш промахивается сам.

Решения человека (`decisions.decided_by='user'`) не трогаются.

Revision ID: 0004_requeue_after_profile_fix
Revises: 0003_seed_project_profiles
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_requeue_after_profile_fix"
down_revision = "0003_seed_project_profiles"
branch_labels = None
depends_on = None

# Тот же предохранитель, что и в app/services/profile_reanalysis.py: каждая
# возвращённая находка — платный вызов модели на ближайших прогонах pipeline.
MAX_REQUEUED = 500

_REQUEUE = sa.text(
    """
    WITH stale AS (
        SELECT f.id
        FROM findings f
        WHERE f.status = 'decided'
          AND EXISTS (
              SELECT 1
              FROM finding_project_matches m
              JOIN projects p ON p.id = m.project_id
              WHERE m.finding_id = f.id
                AND NOT EXISTS (
                    SELECT 1
                    FROM finding_analyses a
                    WHERE a.finding_id = f.id
                      AND a.project_id = m.project_id
                      AND a.analysis_type = 'project_match'
                      AND a.status = 'ok'
                      AND a.created_at >= p.updated_at
                )
          )
          AND NOT EXISTS (
              SELECT 1
              FROM decisions d
              WHERE d.finding_id = f.id
                AND d.is_current
                AND d.decided_by = 'user'
          )
        ORDER BY f.updated_at DESC
        LIMIT :cap
    )
    UPDATE findings
    SET status = 'analyzed',
        pipeline_stage = 'profile_changed:0003'
    WHERE id IN (SELECT id FROM stale)
    """
)


def upgrade() -> None:
    op.get_bind().execute(_REQUEUE, {"cap": MAX_REQUEUED})


def downgrade() -> None:
    """Откат не возвращает прежние вердикты.

    Возвращать нечего: статус `decided` сам по себе вердикта не хранит — он
    лежит в `decisions`, и там ничего не удалялось. Находка, попавшая на
    переоценку, получит новое решение поверх прежнего, а история останется.
    """
    pass
