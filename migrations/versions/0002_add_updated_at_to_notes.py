"""Add an optional updated timestamp to notes.

Revision ID: 0002_add_updated_at_to_notes
Revises: 0001_create_notes_table
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_add_updated_at_to_notes"
down_revision: Optional[str] = "0001_create_notes_table"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "notes" not in inspector.get_table_names():
        raise RuntimeError("Cannot add updated_at because notes table is missing")

    column_names = {
        column["name"] for column in inspector.get_columns("notes")
    }
    if "updated_at" in column_names:
        return

    with op.batch_alter_table("notes") as batch_op:
        batch_op.add_column(sa.Column("updated_at", sa.Text(), nullable=True))


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting note update timestamps"
    )
