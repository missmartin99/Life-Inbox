"""Add rich-capture error and storage fields.

Revision ID: 0008_rich_capture_fields
Revises: 0007_intelligent_organization
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_rich_capture_fields"
down_revision: Optional[str] = "0007_intelligent_organization"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {
        column["name"] for column in inspector.get_columns("inbox_items")
    }
    with op.batch_alter_table("inbox_items") as batch:
        if "error_message" not in columns:
            batch.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        if "storage_path" not in columns:
            batch.add_column(sa.Column("storage_path", sa.Text(), nullable=True))
        if "extracted_text" not in columns:
            batch.add_column(sa.Column("extracted_text", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid dropping capture metadata"
    )
