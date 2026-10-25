"""Persist analysis runs and latest per-item insights.

Revision ID: 0004_persist_analysis_results
Revises: 0003_create_inbox_items
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_persist_analysis_results"
down_revision: Optional[str] = "0003_create_inbox_items"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


ANALYSIS_RUN_COLUMNS = {
    "id",
    "started_at",
    "completed_at",
    "status",
    "mode",
    "result_json",
}
ITEM_INSIGHT_COLUMNS = {
    "item_id",
    "analysis_run_id",
    "category",
    "concise_title",
    "priority",
    "updated_at",
}


def _validate_columns(inspector, table_name: str, required: set) -> None:
    columns = {
        column["name"]
        for column in inspector.get_columns(table_name)
    }
    missing = required - columns
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(
            f"Existing {table_name} table is incompatible; missing: {names}"
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "analysis_runs" not in tables:
        op.create_table(
            "analysis_runs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("started_at", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("mode", sa.Text(), nullable=False),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sqlite_autoincrement=True,
        )
    else:
        _validate_columns(
            inspector,
            "analysis_runs",
            ANALYSIS_RUN_COLUMNS,
        )

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "item_insights" not in tables:
        op.create_table(
            "item_insights",
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.Column("analysis_run_id", sa.Integer(), nullable=False),
            sa.Column("category", sa.Text(), nullable=False),
            sa.Column("concise_title", sa.Text(), nullable=False),
            sa.Column("priority", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["analysis_run_id"],
                ["analysis_runs.id"],
            ),
            sa.ForeignKeyConstraint(
                ["item_id"],
                ["inbox_items.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("item_id"),
        )
    else:
        _validate_columns(
            inspector,
            "item_insights",
            ITEM_INSIGHT_COLUMNS,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persisted analyses"
    )
