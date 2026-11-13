"""Per-person settings (notifications and theme), stored as JSON.

Revision ID: 0016_user_settings
Revises: 0015_profile_details
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_user_settings"
down_revision: Optional[str] = "0015_profile_details"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild), so SQLite foreign keys from other
    # tables to users stay intact. NULL means "all defaults".
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "settings_json" not in columns:
        op.add_column("users", sa.Column("settings_json", sa.Text(), nullable=True))


def downgrade() -> None:
    # Same policy as earlier revisions: data-bearing revisions are not rolled
    # back. Restore from backup.
    raise RuntimeError(
        "Downgrade is disabled to keep people's settings; restore from backup instead"
    )
