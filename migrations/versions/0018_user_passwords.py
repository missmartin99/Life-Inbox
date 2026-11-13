"""Local accounts: a salted password hash per user.

Revision ID: 0018_user_passwords
Revises: 0017_google_calendar
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_user_passwords"
down_revision: Optional[str] = "0017_google_calendar"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "password_hash" not in columns:
        with op.batch_alter_table("users") as batch:
            batch.add_column(sa.Column("password_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("password_hash")
