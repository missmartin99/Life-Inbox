"""Add users and ownership scoping for private-beta multi-user support.

Revision ID: 0011_multi_user_ownership
Revises: 0010_attention_fields
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_multi_user_ownership"
down_revision: Optional[str] = "0010_attention_fields"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("email", sa.Text(), nullable=False),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("auth_provider", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )

    # Seed default local owner and backfill.
    connection = op.get_bind()
    existing = connection.execute(sa.text("SELECT COUNT(*) FROM users")).scalar()
    if not existing:
        connection.execute(
            sa.text(
                "INSERT INTO users (email, display_name, auth_provider, created_at) "
                "VALUES ('local@life-inbox.local', 'Local Owner', 'local', "
                "CURRENT_TIMESTAMP)"
            )
        )
    owner_id = connection.execute(
        sa.text("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    ).scalar()

    for table_name in ("inbox_items", "topics", "tasks", "analysis_runs"):
        columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        if "owner_user_id" not in columns:
            with op.batch_alter_table(table_name) as batch:
                batch.add_column(
                    sa.Column("owner_user_id", sa.Integer(), nullable=True)
                )
            connection.execute(
                sa.text(
                    f"UPDATE {table_name} SET owner_user_id = :owner "
                    "WHERE owner_user_id IS NULL"
                ),
                {"owner": owner_id},
            )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled to avoid dropping ownership metadata"
    )
