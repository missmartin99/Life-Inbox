"""Privacy layer: sensitive flag on items and an AI call log.

Revision ID: 0012_privacy_layer
Revises: 0011_multi_user_ownership
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_privacy_layer"
down_revision: Optional[str] = "0011_multi_user_ownership"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    item_columns = {c["name"] for c in inspector.get_columns("inbox_items")}
    if "is_sensitive" not in item_columns:
        with op.batch_alter_table("inbox_items") as batch:
            batch.add_column(
                sa.Column(
                    "is_sensitive",
                    sa.Boolean(),
                    nullable=False,
                    server_default="0",
                )
            )

    if "ai_call_log" not in inspector.get_table_names():
        op.create_table(
            "ai_call_log",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "owner_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("feature", sa.Text(), nullable=False),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("chars_sent", sa.Integer(), nullable=False),
            sa.Column("redactions", sa.Integer(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("ai_call_log")
    with op.batch_alter_table("inbox_items") as batch:
        batch.drop_column("is_sensitive")
