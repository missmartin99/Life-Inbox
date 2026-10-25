"""Add AI description field for image/voice rich capture.

Revision ID: 0009_image_voice_fields
Revises: 0008_rich_capture_fields
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_image_voice_fields"
down_revision: Optional[str] = "0008_rich_capture_fields"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("inbox_items")
    }
    with op.batch_alter_table("inbox_items") as batch:
        if "ai_description" not in columns:
            batch.add_column(sa.Column("ai_description", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid dropping capture metadata"
    )
