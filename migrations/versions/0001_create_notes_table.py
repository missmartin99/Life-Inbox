"""Create or adopt the existing notes table.

Revision ID: 0001_create_notes_table
Revises:
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_create_notes_table"
down_revision: Optional[str] = None
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def _validate_existing_notes_table(inspector: sa.Inspector) -> None:
    columns = {
        column["name"]: column
        for column in inspector.get_columns("notes")
    }
    required_columns = {"id", "text", "created_at"}
    missing_columns = required_columns - columns.keys()

    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            f"Existing notes table is incompatible; missing columns: {missing}"
        )
    if not columns["id"].get("primary_key"):
        raise RuntimeError(
            "Existing notes table is incompatible; id must be the primary key"
        )
    if columns["text"].get("nullable", True):
        raise RuntimeError(
            "Existing notes table is incompatible; text must be NOT NULL"
        )
    if columns["created_at"].get("nullable", True):
        raise RuntimeError(
            "Existing notes table is incompatible; created_at must be NOT NULL"
        )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "notes" in inspector.get_table_names():
        _validate_existing_notes_table(inspector)
        return

    op.create_table(
        "notes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sqlite_autoincrement=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled because this revision may have adopted an "
        "existing notes table containing user data"
    )
