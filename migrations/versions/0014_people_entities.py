"""People & entities: an entities table and an item <-> entity link table.

Revision ID: 0014_people_entities
Revises: 0013_smart_templates
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_people_entities"
down_revision: Optional[str] = "0013_smart_templates"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "entities" not in tables:
        op.create_table(
            "entities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
            ),
            sa.Column("name", sa.Text(), nullable=False),
            # lower-case copy of name, so "priya" and "Priya" are one entity
            sa.Column("name_key", sa.Text(), nullable=False),
            sa.Column("entity_type", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint(
                "owner_user_id", "entity_type", "name_key", name="uq_entities_owner_type_name"
            ),
        )

    if "item_entities" not in tables:
        op.create_table(
            "item_entities",
            sa.Column(
                "item_id",
                sa.Integer(),
                sa.ForeignKey("inbox_items.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "entity_id",
                sa.Integer(),
                sa.ForeignKey("entities.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )


def downgrade() -> None:
    op.drop_table("item_entities")
    op.drop_table("entities")
