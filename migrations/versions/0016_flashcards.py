"""Flashcards for spaced repetition (Leitner boxes).

Revision ID: 0016_flashcards
Revises: 0015_embedding_cache
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_flashcards"
down_revision: Optional[str] = "0015_embedding_cache"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    if "flashcards" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "flashcards",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
            ),
            sa.Column(
                "item_id",
                sa.Integer(),
                sa.ForeignKey("inbox_items.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("box", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("due_date", sa.Text(), nullable=False),  # YYYY-MM-DD
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("last_reviewed_at", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("flashcards")
