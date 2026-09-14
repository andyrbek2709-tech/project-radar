"""Ключ идемпотентности покрывает только удавшиеся анализы

Revision ID: 0002_idempotency_ok
Revises: 0001_initial
Create Date: 2026-09-14

Оба частичных уникальных индекса на finding_analyses не учитывали status.
Кэш при этом искал строго status='ok'. Получалась ловушка: отказ провайдера
(api_error по rate limit, schema_error) записывался строкой с тем же
input_digest, кэш её не видел, а повторная попытка падала на вставке:

    duplicate key value violates unique constraint
    "uq_analysis_idempotency_global"

Находка выбывала из обработки навсегда — даже после того, как лимит
провайдера отпускал. На проде так встали 435 записей разом.

Идемпотентность здесь про то, чтобы не платить дважды за удавшийся ответ.
Отказ ответом не является и ключ занимать не должен.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_idempotency_ok"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("uq_analysis_idempotency_project", table_name="finding_analyses")
    op.drop_index("uq_analysis_idempotency_global", table_name="finding_analyses")

    op.create_index(
        "uq_analysis_idempotency_project",
        "finding_analyses",
        ["finding_id", "project_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where="project_id IS NOT NULL AND status = 'ok'",
    )
    op.create_index(
        "uq_analysis_idempotency_global",
        "finding_analyses",
        ["finding_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where="project_id IS NULL AND status = 'ok'",
    )

    # Сырьё, убитое этим же багом, вернуть в очередь.
    #
    # Повторная попытка падала на вставке, обработчик помечал запись error —
    # а выборка берёт только pending, так что сама она бы уже не вернулась.
    # Условие по тексту ошибки нарочно узкое: трогаем ровно те строки, что
    # споткнулись об этот индекс, и не будим то, что сломалось по своей причине.
    op.execute(
        """
        UPDATE raw_items
           SET processing_status = 'pending', error = NULL
         WHERE processing_status = 'error'
           AND error LIKE '%uq_analysis_idempotency%'
        """
    )


def downgrade() -> None:
    # Обратно ключ сужается до строк без учёта status, поэтому сначала убираем
    # неудачные попытки — иначе старый индекс не построится на дублях.
    op.execute("DELETE FROM finding_analyses WHERE status <> 'ok'")

    op.drop_index("uq_analysis_idempotency_project", table_name="finding_analyses")
    op.drop_index("uq_analysis_idempotency_global", table_name="finding_analyses")

    op.create_index(
        "uq_analysis_idempotency_project",
        "finding_analyses",
        ["finding_id", "project_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where="project_id IS NOT NULL",
    )
    op.create_index(
        "uq_analysis_idempotency_global",
        "finding_analyses",
        ["finding_id", "analysis_type", "prompt_version", "input_digest"],
        unique=True,
        postgresql_where="project_id IS NULL",
    )
