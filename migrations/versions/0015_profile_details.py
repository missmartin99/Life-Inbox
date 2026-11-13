"""Profile details: date of birth.

Revision ID: 0015_profile_details
Revises: 0014_profile_photos
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_profile_details"
down_revision: Optional[str] = "0014_profile_photos"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild), so SQLite foreign keys from other
    # tables to users stay intact.
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "date_of_birth" not in columns:
        # ISO date text, e.g. 1999-08-21 (same text-date style as the rest).
        op.add_column("users", sa.Column("date_of_birth", sa.Text(), nullable=True))


def downgrade() -> None:
    # Same policy as earlier revisions: data-bearing revisions are not rolled
    # back (it would drop people's dates of birth). Restore from backup.
    raise RuntimeError(
        "Downgrade is disabled to keep profile details; restore from backup instead"
    )
