"""Make topic names unique per owner instead of globally.

Migration 0007 created topics with UNIQUE(name). 0011 added owner_user_id but
kept that global constraint, so a second user could never get their own
default topics ("Work", "Health", ...). With Google sign-in every new account
creates them at first login, so that login failed on migrated databases.

Revision ID: 0013_topics_unique_per_owner
Revises: 0012_google_auth_sessions
"""

from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_topics_unique_per_owner"
down_revision: Optional[str] = "0012_google_auth_sessions"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None

PER_OWNER = "uq_topics_name_owner"


def upgrade() -> None:
    bind = op.get_bind()
    uniques = sa.inspect(bind).get_unique_constraints("topics")
    if any(
        sorted(unique["column_names"]) == ["name", "owner_user_id"]
        for unique in uniques
    ):
        return  # already per-owner (e.g. tables created from the models)
    name_only = [unique for unique in uniques if unique["column_names"] == ["name"]]

    if bind.dialect.name == "sqlite":
        # SQLite cannot drop a table constraint, so batch mode rebuilds the
        # table. item_insights references topics; with foreign keys enforced,
        # dropping the old table would fail. This must run before any data
        # change in this migration (the PRAGMA is ignored inside a transaction).
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
        with op.batch_alter_table(
            "topics",
            recreate="always",
            table_kwargs={"sqlite_autoincrement": True},  # keep ids unique forever
            # Gives the reflected, unnamed UNIQUE(name) a name we can drop.
            naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"},
        ) as batch:
            if name_only:
                batch.drop_constraint("uq_topics_name", type_="unique")
            batch.create_unique_constraint(PER_OWNER, ["name", "owner_user_id"])
        problems = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Foreign key check failed after rebuild: {problems}")
        return

    for unique in name_only:
        op.drop_constraint(unique["name"], "topics", type_="unique")
    op.create_unique_constraint(PER_OWNER, "topics", ["name", "owner_user_id"])


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled: per-owner topic names may already collide "
        "across users; restore from backup instead"
    )
