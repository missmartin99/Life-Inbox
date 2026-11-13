"""Semantic search: cache of text embeddings, keyed by a hash of the text.

Revision ID: 0015_embedding_cache
Revises: 0014_people_entities
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_embedding_cache"
down_revision: Optional[str] = "0014_people_entities"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    if "embedding_cache" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "embedding_cache",
            sa.Column("text_hash", sa.Text(), primary_key=True),
            sa.Column("model", sa.Text(), primary_key=True),
            sa.Column("vector", sa.Text(), nullable=False),  # JSON list of floats
            sa.Column("created_at", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("embedding_cache")
