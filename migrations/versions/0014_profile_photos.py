"""Profile photos: the Google account picture and an optional uploaded photo.

Revision ID: 0014_profile_photos
Revises: 0013_topics_unique_per_owner
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_profile_photos"
down_revision: Optional[str] = "0013_topics_unique_per_owner"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    # Plain ADD COLUMN (no table rebuild), so SQLite foreign keys from other
    # tables to users stay intact.
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")
    }
    if "google_picture_url" not in columns:
        op.add_column(
            "users", sa.Column("google_picture_url", sa.Text(), nullable=True)
        )
    if "avatar_path" not in columns:
        op.add_column("users", sa.Column("avatar_path", sa.Text(), nullable=True))


def downgrade() -> None:
    # Same policy as 0011/0012: data-bearing revisions are not rolled back
    # (dropping avatar_path would orphan uploaded photos). Restore from backup.
    raise RuntimeError(
        "Downgrade is disabled to keep uploaded profile photos linked; "
        "restore from backup instead"
    )
