"""Copy notes from an old Life Inbox SQLite snapshot into the current database.

The 1,000+ seeded "power notes" used to live in data/life_inbox.db, which was
committed to git (commit 2fd4fc4). That file is no longer tracked, so this
script copies the notes you choose back in, under one account.

It copies each chosen item together with everything hanging off it:
tasks, people/entities, flashcards, AI insights (category, topic, tags) and
"related item" links.

Get the snapshot out of git first:
    git show 2fd4fc4:data/life_inbox.db > /tmp/seeded.db

Then preview (nothing is written without --apply):
    python scripts/restore_seed_notes.py /tmp/seeded.db --email you@example.com
    python scripts/restore_seed_notes.py /tmp/seeded.db --email you@example.com \
        --kinds receipt recipe note --since 2026-06-01 --skip-sensitive

And write it:
    python scripts/restore_seed_notes.py /tmp/seeded.db --email you@example.com --apply

The target database is whatever DATABASE_URL points at (local SQLite by
default, Postgres on the server).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.database import (  # noqa: E402
    AnalysisRunRecord,
    EntityRecord,
    FlashcardRecord,
    InboxItemRecord,
    ItemEntityRecord,
    ItemInsightRecord,
    ItemRelationRecord,
    SessionLocal,
    TaskRecord,
    TaskSourceItemRecord,
    TopicRecord,
    UserRecord,
    ensure_default_topics,
    init_db,
)

# Columns we copy as-is for each table. Ids and owner ids are handled
# separately, because they change when the row moves to a new database.
ITEM_COLUMNS = [
    "item_type", "title", "raw_text", "source_url", "original_filename",
    "mime_type", "processing_status", "error_message", "storage_path",
    "extracted_text", "ai_description", "created_at", "updated_at",
    "is_sensitive", "kind", "kind_fields",
]
TASK_COLUMNS = [
    "text", "priority", "timing_text", "ai_timing_text", "timing_confirmed",
    "due_on", "due_confidence", "snoozed_until", "reminder_at", "status",
    "created_at", "updated_at",
]
INSIGHT_COLUMNS = [
    "category", "concise_title", "priority", "tags_json", "rationale",
    "user_title", "user_priority", "user_tags_json", "needs_review", "updated_at",
]


# The seed script used longer category names than the expense tracker knows
# (app/expenses.py CATEGORIES), so those receipts all landed in "Other".
CATEGORY_MAP = {
    "Food & Dining": "Food",
    "Healthcare & Pharmacy": "Health",
    "Travel & Fuel": "Transport",
    "Shopping & Electronics": "Shopping",
    "Utilities & Bills": "Bills",
}


def fix_category(kind_fields: str | None) -> str | None:
    """Rename a receipt's category to one the expense tracker understands."""
    if not kind_fields:
        return kind_fields
    fields = json.loads(kind_fields)
    if fields.get("category") in CATEGORY_MAP:
        fields["category"] = CATEGORY_MAP[fields["category"]]
        return json.dumps(fields)
    return kind_fields


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pick_items(old, args) -> list[sqlite3.Row]:
    """Return the snapshot items that match the command-line filters."""
    rows = old.execute("SELECT * FROM inbox_items ORDER BY created_at").fetchall()
    chosen = []
    for row in rows:
        kind = row["kind"] or "note"  # plain notes have no kind
        if args.kinds and kind not in args.kinds:
            continue
        if args.since and row["created_at"][:10] < args.since:
            continue
        if args.until and row["created_at"][:10] > args.until:
            continue
        if args.skip_sensitive and row["is_sensitive"]:
            continue
        if args.keyword:
            text = f"{row['title'] or ''} {row['raw_text'] or ''}".lower()
            if not any(word.lower() in text for word in args.keyword):
                continue
        chosen.append(row)
    if args.limit:
        chosen = chosen[: args.limit]
    return chosen


def summarize(old, items) -> dict:
    """Count what would be copied for the chosen items."""
    ids = [row["id"] for row in items]
    marks = ",".join("?" * len(ids)) or "NULL"

    def count(sql: str) -> int:
        return old.execute(sql.format(marks=marks), ids).fetchone()[0]

    kinds: dict[str, int] = {}
    for row in items:
        kinds[row["kind"] or "note"] = kinds.get(row["kind"] or "note", 0) + 1
    return {
        "items": len(items),
        "by kind": kinds,
        "sensitive": sum(1 for row in items if row["is_sensitive"]),
        "tasks": count(
            "SELECT COUNT(DISTINCT task_id) FROM task_source_items WHERE item_id IN ({marks})"
        ),
        "entity links": count("SELECT COUNT(*) FROM item_entities WHERE item_id IN ({marks})"),
        "flashcards": count("SELECT COUNT(*) FROM flashcards WHERE item_id IN ({marks})"),
        "insights": count("SELECT COUNT(*) FROM item_insights WHERE item_id IN ({marks})"),
        "related links (both ends chosen)": old.execute(
            f"SELECT COUNT(*) FROM item_relations WHERE item_id_a IN ({marks}) "
            f"AND item_id_b IN ({marks})",
            ids + ids,
        ).fetchone()[0],
    }


def copy_into(session, old, items, owner_id: int) -> None:
    """Copy the chosen items and their related rows into the current DB."""
    # The same note may be restored twice by accident; skip ones already there.
    existing = set(
        session.execute(
            select(InboxItemRecord.created_at, InboxItemRecord.raw_text).where(
                InboxItemRecord.owner_user_id == owner_id
            )
        ).all()
    )

    # Old id -> new id, so links between rows still point at the right rows.
    item_map: dict[int, int] = {}
    for row in items:
        if (row["created_at"], row["raw_text"]) in existing:
            continue
        new = InboxItemRecord(owner_user_id=owner_id, **{c: row[c] for c in ITEM_COLUMNS})
        new.is_sensitive = bool(row["is_sensitive"])
        new.kind_fields = fix_category(row["kind_fields"])
        session.add(new)
        session.flush()  # gives `new` its id
        item_map[row["id"]] = new.id
    if not item_map:
        print("Every chosen item is already in this account. Nothing to do.")
        return
    old_ids = list(item_map)
    marks = ",".join("?" * len(old_ids))

    # Tasks: a task can come from several items; copy it once.
    task_map: dict[int, int] = {}
    links = old.execute(
        f"SELECT task_id, item_id FROM task_source_items WHERE item_id IN ({marks})", old_ids
    ).fetchall()
    for link in links:
        old_task_id = link["task_id"]
        if old_task_id not in task_map:
            task = old.execute("SELECT * FROM tasks WHERE id = ?", (old_task_id,)).fetchone()
            new_task = TaskRecord(
                owner_user_id=owner_id,
                # dedup_key must be unique across the whole table.
                dedup_key=f"{task['dedup_key']}:restored:{owner_id}",
                **{c: task[c] for c in TASK_COLUMNS},
            )
            session.add(new_task)
            session.flush()
            task_map[old_task_id] = new_task.id
        session.add(TaskSourceItemRecord(task_id=task_map[old_task_id], item_id=item_map[link["item_id"]]))

    # Entities: reuse the account's entity if it already has one with that name.
    entity_map: dict[int, int] = {}
    links = old.execute(
        f"SELECT item_id, entity_id FROM item_entities WHERE item_id IN ({marks})", old_ids
    ).fetchall()
    for link in links:
        old_entity_id = link["entity_id"]
        if old_entity_id not in entity_map:
            ent = old.execute("SELECT * FROM entities WHERE id = ?", (old_entity_id,)).fetchone()
            found = session.execute(
                select(EntityRecord).where(
                    EntityRecord.owner_user_id == owner_id,
                    EntityRecord.entity_type == ent["entity_type"],
                    EntityRecord.name_key == ent["name_key"],
                )
            ).scalar_one_or_none()
            if found is None:
                found = EntityRecord(
                    owner_user_id=owner_id, name=ent["name"], name_key=ent["name_key"],
                    entity_type=ent["entity_type"], created_at=ent["created_at"],
                )
                session.add(found)
                session.flush()
            entity_map[old_entity_id] = found.id
        session.add(ItemEntityRecord(item_id=item_map[link["item_id"]], entity_id=entity_map[old_entity_id]))

    # Flashcards.
    for card in old.execute(f"SELECT * FROM flashcards WHERE item_id IN ({marks})", old_ids):
        session.add(FlashcardRecord(
            owner_user_id=owner_id, item_id=item_map[card["item_id"]],
            question=card["question"], answer=card["answer"], box=card["box"],
            due_date=card["due_date"], created_at=card["created_at"],
            last_reviewed_at=card["last_reviewed_at"],
        ))

    # AI insights. Each insight must point at an analysis run, so we record
    # one "restore" run, and match topics by name in the target account.
    ensure_default_topics(session, owner_user_id=owner_id)
    run = AnalysisRunRecord(
        started_at=now_iso(), completed_at=now_iso(), status="completed",
        mode="restore", owner_user_id=owner_id,
    )
    session.add(run)
    session.flush()
    old_topic_names = {r["id"]: r["name"] for r in old.execute("SELECT id, name FROM topics")}
    topic_ids: dict[str, int] = {}

    def new_topic_id(old_topic_id):
        name = old_topic_names.get(old_topic_id)
        if name is None:
            return None
        if name not in topic_ids:
            topic = session.execute(
                select(TopicRecord).where(
                    TopicRecord.owner_user_id == owner_id, TopicRecord.name == name
                )
            ).scalar_one_or_none()
            if topic is None:
                topic = TopicRecord(
                    name=name, owner_user_id=owner_id,
                    created_at=now_iso(), updated_at=now_iso(), archived=0,
                )
                session.add(topic)
                session.flush()
            topic_ids[name] = topic.id
        return topic_ids[name]

    for ins in old.execute(f"SELECT * FROM item_insights WHERE item_id IN ({marks})", old_ids):
        session.add(ItemInsightRecord(
            item_id=item_map[ins["item_id"]], analysis_run_id=run.id,
            topic_id=new_topic_id(ins["topic_id"]),
            user_topic_id=new_topic_id(ins["user_topic_id"]),
            **{c: ins[c] for c in INSIGHT_COLUMNS},
        ))

    # "Related item" links, only when both items were restored.
    for rel in old.execute(
        f"SELECT * FROM item_relations WHERE item_id_a IN ({marks}) AND item_id_b IN ({marks})",
        old_ids + old_ids,
    ):
        session.add(ItemRelationRecord(
            item_id_a=item_map[rel["item_id_a"]], item_id_b=item_map[rel["item_id_b"]],
            relation_type=rel["relation_type"], score=rel["score"],
            created_at=rel["created_at"], dismissed=rel["dismissed"],
        ))

    print(f"Restored {len(item_map)} items and {len(task_map)} tasks "
          f"({len(items) - len(item_map)} skipped as already present).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("snapshot", help="path to the old life_inbox.db")
    parser.add_argument("--email", required=True, help="account that should own the notes")
    parser.add_argument("--kinds", nargs="*", help="note receipt recipe book contact event other")
    parser.add_argument("--since", help="only items created on/after YYYY-MM-DD")
    parser.add_argument("--until", help="only items created on/before YYYY-MM-DD")
    parser.add_argument("--keyword", nargs="*", help="only items mentioning any of these words")
    parser.add_argument("--skip-sensitive", action="store_true", help="leave out sensitive items")
    parser.add_argument("--limit", type=int, help="copy at most this many items")
    parser.add_argument("--apply", action="store_true", help="actually write (default: preview)")
    args = parser.parse_args()

    old = sqlite3.connect(f"file:{args.snapshot}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    items = pick_items(old, args)

    print("Would restore:" if not args.apply else "Restoring:")
    for key, value in summarize(old, items).items():
        print(f"  {key}: {value}")
    for row in items[:5]:
        print(f"  e.g. [{row['kind'] or 'note'}] {(row['title'] or row['raw_text'] or '')[:70]}")

    init_db()
    with SessionLocal() as session:
        user = session.execute(
            select(UserRecord).where(UserRecord.email == args.email.strip().lower())
        ).scalar_one_or_none()
        if user is None:
            sys.exit(f"No account with email {args.email}. Sign up in the app first.")
        print(f"Target account: {user.display_name} (id {user.id})")
        if not args.apply:
            print("Preview only. Add --apply to write.")
            return
        copy_into(session, old, items, user.id)
        session.commit()


if __name__ == "__main__":
    main()
