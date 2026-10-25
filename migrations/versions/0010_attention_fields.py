"""Add attention / reminder fields and calendar action receipts.

Revision ID: 0010_attention_fields
Revises: 0009_image_voice_fields
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_attention_fields"
down_revision: Optional[str] = "0009_image_voice_fields"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("tasks")
    }
    with op.batch_alter_table("tasks") as batch:
        if "due_on" not in columns:
            batch.add_column(sa.Column("due_on", sa.Text(), nullable=True))
        if "due_confidence" not in columns:
            batch.add_column(sa.Column("due_confidence", sa.Text(), nullable=True))
        if "snoozed_until" not in columns:
            batch.add_column(sa.Column("snoozed_until", sa.Text(), nullable=True))
        if "reminder_at" not in columns:
            batch.add_column(sa.Column("reminder_at", sa.Text(), nullable=True))

    tables = set(inspector.get_table_names())
    if "calendar_action_receipts" not in tables:
        op.create_table(
            "calendar_action_receipts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("draft_id", sa.Text(), nullable=False),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid dropping attention metadata"
    )
