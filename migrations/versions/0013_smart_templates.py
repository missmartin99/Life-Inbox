"""Smart templates: item kind (recipe, book, ...) and its structured fields.

Revision ID: 0013_smart_templates
Revises: 0012_privacy_layer
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_smart_templates"
down_revision: Optional[str] = "0012_privacy_layer"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("inbox_items")}

    with op.batch_alter_table("inbox_items") as batch:
        if "kind" not in columns:
            batch.add_column(sa.Column("kind", sa.Text(), nullable=True))
        if "kind_fields" not in columns:
            # JSON text, e.g. {"name": "Paneer tikka", "ingredients": [...]}
            batch.add_column(sa.Column("kind_fields", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("inbox_items") as batch:
        batch.drop_column("kind_fields")
        batch.drop_column("kind")
