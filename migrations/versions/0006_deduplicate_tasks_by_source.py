"""Deduplicate generated tasks by source inbox item.

Revision ID: 0006_deduplicate_tasks_by_source
Revises: 0005_create_tasks
"""

import hashlib
import json
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_deduplicate_tasks_by_source"
down_revision: Optional[str] = "0005_create_tasks"
branch_labels: Optional[Union[str, Sequence[str]]] = None
depends_on: Optional[Union[str, Sequence[str]]] = None


def _source_key(item_ids) -> str:
    fingerprint = json.dumps(
        sorted(set(item_ids)),
        separators=(",", ":"),
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if not {"tasks", "task_source_items"} <= tables:
        raise RuntimeError("Task tables must exist before source deduplication")

    tasks = {
        row.id: row
        for row in connection.execute(
            sa.text(
                """
                SELECT id, text, priority, timing_text, status, dedup_key,
                       created_at, updated_at
                FROM tasks
                ORDER BY id
                """
            )
        ).mappings()
    }
    links = connection.execute(
        sa.text(
            """
            SELECT task_id, item_id
            FROM task_source_items
            ORDER BY task_id, item_id
            """
        )
    ).mappings().all()

    connection.execute(
        sa.text(
            """
            UPDATE tasks
            SET dedup_key = 'source-dedup-migration-' || id
            """
        )
    )

    parent = {task_id: task_id for task_id in tasks}

    def find(task_id):
        while parent[task_id] != task_id:
            parent[task_id] = parent[parent[task_id]]
            task_id = parent[task_id]
        return task_id

    def union(first, second):
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(
                first_root,
                second_root,
            )

    item_tasks = {}
    for link in links:
        item_tasks.setdefault(link["item_id"], []).append(link["task_id"])
    for task_ids in item_tasks.values():
        for task_id in task_ids[1:]:
            union(task_ids[0], task_id)

    components = {}
    for task_id in tasks:
        components.setdefault(find(task_id), []).append(task_id)

    task_items = {}
    for link in links:
        task_items.setdefault(link["task_id"], set()).add(link["item_id"])

    for task_ids in components.values():
        task_ids = sorted(task_ids)
        canonical_id = task_ids[0]
        latest_id = task_ids[-1]
        source_ids = sorted(
            {
                item_id
                for task_id in task_ids
                for item_id in task_items.get(task_id, set())
            }
        )

        if len(task_ids) > 1:
            connection.execute(
                sa.text(
                    """
                    DELETE FROM task_source_items
                    WHERE task_id IN :task_ids
                    """
                ).bindparams(
                    sa.bindparam("task_ids", expanding=True)
                ),
                {"task_ids": task_ids},
            )
            for item_id in source_ids:
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO task_source_items (task_id, item_id)
                        VALUES (:task_id, :item_id)
                        """
                    ),
                    {"task_id": canonical_id, "item_id": item_id},
                )
            duplicate_ids = task_ids[1:]
            connection.execute(
                sa.text("DELETE FROM tasks WHERE id IN :task_ids").bindparams(
                    sa.bindparam("task_ids", expanding=True)
                ),
                {"task_ids": duplicate_ids},
            )

        latest = tasks[latest_id]
        connection.execute(
            sa.text(
                """
                UPDATE tasks
                SET text = :text,
                    priority = :priority,
                    timing_text = :timing_text,
                    dedup_key = :dedup_key,
                    updated_at = :updated_at
                WHERE id = :task_id
                """
            ),
            {
                "text": latest["text"],
                "priority": latest["priority"],
                "timing_text": latest["timing_text"],
                "dedup_key": (
                    _source_key(source_ids)
                    if source_ids
                    else f"orphan-task-{canonical_id}"
                ),
                "updated_at": latest["updated_at"],
                "task_id": canonical_id,
            },
        )

    indexes = {
        index["name"]
        for index in sa.inspect(connection).get_indexes(
            "task_source_items"
        )
    }
    if "uq_task_source_items_item_id" not in indexes:
        op.create_index(
            "uq_task_source_items_item_id",
            "task_source_items",
            ["item_id"],
            unique=True,
        )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrade is disabled because removed duplicate tasks cannot "
        "be reconstructed"
    )
