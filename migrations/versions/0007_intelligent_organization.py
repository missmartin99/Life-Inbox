"""Topics, insight overrides, relations, multi-task sources.

Revision ID: 0007_intelligent_organization
Revises: 0006_deduplicate_tasks_by_source
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_intelligent_organization"
down_revision: Optional[str] = "0006_deduplicate_tasks_by_source"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "topics" not in tables:
        op.create_table(
            "topics",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
            sa.Column("archived", sa.Integer(), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
            sqlite_autoincrement=True,
        )

    insight_columns = {
        column["name"] for column in inspector.get_columns("item_insights")
    }
    with op.batch_alter_table("item_insights") as batch:
        if "topic_id" not in insight_columns:
            batch.add_column(sa.Column("topic_id", sa.Integer(), nullable=True))
        if "tags_json" not in insight_columns:
            batch.add_column(sa.Column("tags_json", sa.Text(), nullable=True))
        if "rationale" not in insight_columns:
            batch.add_column(sa.Column("rationale", sa.Text(), nullable=True))
        if "user_title" not in insight_columns:
            batch.add_column(sa.Column("user_title", sa.Text(), nullable=True))
        if "user_topic_id" not in insight_columns:
            batch.add_column(sa.Column("user_topic_id", sa.Integer(), nullable=True))
        if "user_priority" not in insight_columns:
            batch.add_column(sa.Column("user_priority", sa.Text(), nullable=True))
        if "user_tags_json" not in insight_columns:
            batch.add_column(sa.Column("user_tags_json", sa.Text(), nullable=True))
        if "needs_review" not in insight_columns:
            batch.add_column(
                sa.Column(
                    "needs_review",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )

    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "item_relations" not in tables:
        op.create_table(
            "item_relations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("item_id_a", sa.Integer(), nullable=False),
            sa.Column("item_id_b", sa.Integer(), nullable=False),
            sa.Column("relation_type", sa.Text(), nullable=False),
            sa.Column("score", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("dismissed", sa.Integer(), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(
                ["item_id_a"], ["inbox_items.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["item_id_b"], ["inbox_items.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "item_id_a",
                "item_id_b",
                "relation_type",
                name="uq_item_relations_pair_type",
            ),
            sqlite_autoincrement=True,
        )

    task_columns = {column["name"] for column in inspector.get_columns("tasks")}
    with op.batch_alter_table("tasks") as batch:
        if "ai_timing_text" not in task_columns:
            batch.add_column(sa.Column("ai_timing_text", sa.Text(), nullable=True))
        if "timing_confirmed" not in task_columns:
            batch.add_column(
                sa.Column(
                    "timing_confirmed",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                )
            )

    indexes = {
        index["name"]
        for index in sa.inspect(bind).get_indexes("task_source_items")
    }
    if "uq_task_source_items_item_id" in indexes:
        op.drop_index(
            "uq_task_source_items_item_id",
            table_name="task_source_items",
        )

    # Seed default topics from common organizer buckets if empty.
    existing = bind.execute(sa.text("SELECT COUNT(*) FROM topics")).scalar()
    if not existing:
        now = "2026-01-01T00:00:00+00:00"
        for name in (
            "Work",
            "Money & Admin",
            "Learning",
            "Shopping & Errands",
            "Health",
            "Travel",
            "Ideas",
            "People",
            "Personal / Reference",
        ):
            bind.execute(
                sa.text(
                    """
                    INSERT INTO topics (name, description, created_at, updated_at, archived)
                    VALUES (:name, NULL, :now, :now, 0)
                    """
                ),
                {"name": name, "now": now},
            )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid dropping organization data"
    )
