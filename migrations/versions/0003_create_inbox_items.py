"""Create inbox items and copy existing text notes.

Revision ID: 0003_create_inbox_items
Revises: 0002_add_updated_at_to_notes
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_create_inbox_items"
down_revision: Optional[str] = "0002_add_updated_at_to_notes"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


REQUIRED_COLUMNS = {
    "id",
    "item_type",
    "title",
    "raw_text",
    "source_url",
    "original_filename",
    "mime_type",
    "processing_status",
    "created_at",
    "updated_at",
}


def _create_or_validate_inbox_items(bind) -> None:
    inspector = sa.inspect(bind)
    if "inbox_items" not in inspector.get_table_names():
        op.create_table(
            "inbox_items",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("item_type", sa.Text(), nullable=False),
            sa.Column("title", sa.Text(), nullable=True),
            sa.Column("raw_text", sa.Text(), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("original_filename", sa.Text(), nullable=True),
            sa.Column("mime_type", sa.Text(), nullable=True),
            sa.Column("processing_status", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sqlite_autoincrement=True,
        )
        return

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("inbox_items")
    }
    missing_columns = REQUIRED_COLUMNS - existing_columns
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise RuntimeError(
            f"Existing inbox_items table is incompatible; missing: {missing}"
        )


def _copy_legacy_notes(bind) -> None:
    inspector = sa.inspect(bind)
    if "notes" not in inspector.get_table_names():
        return

    conflicting_id = bind.execute(
        sa.text(
            """
            SELECT notes.id
            FROM notes
            JOIN inbox_items ON inbox_items.id = notes.id
            WHERE inbox_items.item_type != 'text'
               OR inbox_items.raw_text != notes.text
               OR inbox_items.created_at != notes.created_at
               OR inbox_items.updated_at IS NOT notes.updated_at
            LIMIT 1
            """
        )
    ).scalar()
    if conflicting_id is not None:
        raise RuntimeError(
            "Cannot migrate notes because inbox_items already contains "
            f"conflicting id {conflicting_id}"
        )

    bind.execute(
        sa.text(
            """
            INSERT INTO inbox_items (
                id,
                item_type,
                title,
                raw_text,
                source_url,
                original_filename,
                mime_type,
                processing_status,
                created_at,
                updated_at
            )
            SELECT
                notes.id,
                'text',
                NULL,
                notes.text,
                NULL,
                NULL,
                NULL,
                'ready',
                notes.created_at,
                notes.updated_at
            FROM notes
            WHERE NOT EXISTS (
                SELECT 1
                FROM inbox_items
                WHERE inbox_items.id = notes.id
            )
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_or_validate_inbox_items(bind)
    _copy_legacy_notes(bind)


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid deleting migrated inbox items"
    )
