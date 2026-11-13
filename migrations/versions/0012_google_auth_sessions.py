"""Add Google identity columns and server-side login sessions.

Revision ID: 0012_google_auth_sessions
Revises: 0011_multi_user_ownership
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_google_auth_sessions"
down_revision: Optional[str] = "0017_api_tokens_webhooks"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Plain ADD COLUMN (no table rebuild), so SQLite foreign keys from other
    # tables to users stay intact.
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "google_sub" not in user_columns:
        op.add_column("users", sa.Column("google_sub", sa.Text(), nullable=True))
    if "last_login_at" not in user_columns:
        op.add_column(
            "users", sa.Column("last_login_at", sa.Text(), nullable=True)
        )

    inspector = sa.inspect(bind)
    user_indexes = {index["name"] for index in inspector.get_indexes("users")}
    if "ix_users_google_sub" not in user_indexes:
        op.create_index(
            "ix_users_google_sub", "users", ["google_sub"], unique=True
        )

    if "auth_sessions" not in set(inspector.get_table_names()):
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("last_seen_at", sa.Text(), nullable=False),
            sa.Column("revoked_at", sa.Text(), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sqlite_autoincrement=True,
        )
        op.create_index(
            "ix_auth_sessions_token_hash",
            "auth_sessions",
            ["token_hash"],
            unique=True,
        )
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])


def downgrade() -> None:
    # Same policy as 0011: data-bearing revisions are not rolled back. Dropping
    # google_sub would unlink every Google account, and on SQLite the users
    # table rebuild conflicts with the foreign keys that point at it.
    # Restore from the backup taken before upgrading instead.
    raise RuntimeError(
        "Downgrade is disabled to avoid unlinking Google accounts; "
        "restore from backup instead"
    )
