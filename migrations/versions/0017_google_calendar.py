"""Google Calendar connections and the events made for tasks.

Revision ID: 0017_google_calendar
Revises: 0016_user_settings
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_google_calendar"
down_revision: Optional[str] = "0016_user_settings"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    # New tables only; nothing existing is touched.
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "calendar_connections" not in tables:
        op.create_table(
            "calendar_connections",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("google_email", sa.Text(), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("refresh_token_enc", sa.Text(), nullable=False),
            sa.Column("access_token_enc", sa.Text(), nullable=True),
            sa.Column("access_token_expires_at", sa.Text(), nullable=True),
            sa.Column("calendar_id", sa.Text(), nullable=True),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("last_synced_at", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("connected_at", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.Text(), nullable=False),
        )
        op.create_index(
            "ix_calendar_connections_user_id",
            "calendar_connections",
            ["user_id"],
            unique=True,
        )

    if "calendar_task_events" not in tables:
        op.create_table(
            "calendar_task_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.Text(), nullable=False),
            sa.Column("fingerprint", sa.Text(), nullable=False),
            sa.Column("synced_at", sa.Text(), nullable=False),
            sa.UniqueConstraint(
                "user_id", "task_id", name="uq_calendar_task_events_task"
            ),
        )


def downgrade() -> None:
    # Same policy as earlier revisions: data-bearing revisions are not rolled
    # back. Dropping these tables would leave events on people's calendars that
    # Life Inbox could no longer tidy up. Restore from backup instead.
    raise RuntimeError(
        "Downgrade is disabled to keep calendar connections; restore from backup instead"
    )
