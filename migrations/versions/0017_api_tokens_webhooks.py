"""Public API: API tokens (stored hashed) and webhooks.

Revision ID: 0017_api_tokens_webhooks
Revises: 0016_flashcards
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_api_tokens_webhooks"
down_revision: Optional[str] = "0016_flashcards"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "api_tokens" not in tables:
        op.create_table(
            "api_tokens",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("last_used_at", sa.Text(), nullable=True),
        )

    if "webhooks" not in tables:
        op.create_table(
            "webhooks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("url", sa.Text(), nullable=False),
            sa.Column("event", sa.Text(), nullable=False),
            sa.Column("secret", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("webhooks")
    op.drop_table("api_tokens")
