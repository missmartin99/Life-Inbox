"""Create persistent tasks and their inbox-item sources.

Revision ID: 0005_create_tasks
Revises: 0004_persist_analysis_results
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_create_tasks"
down_revision: Optional[str] = "0004_persist_analysis_results"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


TASK_COLUMNS = {
    "id",
    "text",
    "priority",
    "timing_text",
    "status",
    "dedup_key",
    "created_at",
    "updated_at",
}
TASK_SOURCE_COLUMNS = {"task_id", "item_id"}


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

    if "tasks" not in tables:
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("priority", sa.Text(), nullable=False),
            sa.Column("timing_text", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("dedup_key", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "priority IN ('High', 'Medium', 'Low')",
                name="ck_tasks_priority",
            ),
            sa.CheckConstraint(
                "status IN ('open', 'completed', 'dismissed')",
                name="ck_tasks_status",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dedup_key"),
            sqlite_autoincrement=True,
        )
    else:
        _validate_columns(inspector, "tasks", TASK_COLUMNS)

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "task_source_items" not in tables:
        op.create_table(
            "task_source_items",
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("item_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["item_id"],
                ["inbox_items.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["task_id"],
                ["tasks.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("task_id", "item_id"),
        )
    else:
        _validate_columns(
            inspector,
            "task_source_items",
            TASK_SOURCE_COLUMNS,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting persistent tasks"
    )
