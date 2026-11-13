"""Notification bell: every pending task, highest priority first."""

import importlib
import sys
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.integrations.calendar",
)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bell.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    from app.attention.dates import today_in_configured_tz

    with TestClient(main.app) as client:
        yield SimpleNamespace(
            client=client, database=database, today=today_in_configured_tz()
        )
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def make_user(database, email):
    user = database.ensure_user(
        email=email, display_name=email.split("@")[0], auth_provider="google"
    )
    token = database.create_auth_session(user["id"])
    return user, {"Cookie": f"life_inbox_session={token}"}


def add_task(ctx, owner, text, priority, *, days=None, status="open", snoozed=None):
    """days: due date relative to today (None = no due date)."""
    db = ctx.database
    with db.SessionLocal() as session:
        task = db.TaskRecord(
            text=text,
            priority=priority,
            status=status,
            dedup_key=f"{owner['id']}:{text}",
            owner_user_id=owner["id"],
            due_on=(ctx.today + timedelta(days=days)).isoformat() if days is not None else None,
            due_confidence="high" if days is not None else "none",
            snoozed_until=(ctx.today + timedelta(days=snoozed)).isoformat() if snoozed else None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(task)
        session.commit()


def bell(ctx, headers):
    response = ctx.client.get("/api/notifications", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_tasks_are_ordered_high_to_low_then_by_urgency(ctx):
    alice, a = make_user(ctx.database, "alice@example.com")
    add_task(ctx, alice, "low, no date", "Low")
    add_task(ctx, alice, "high, in 5 days", "High", days=5)
    add_task(ctx, alice, "medium, overdue", "Medium", days=-2)
    add_task(ctx, alice, "high, no date", "High")
    add_task(ctx, alice, "high, overdue", "High", days=-1)
    add_task(ctx, alice, "high, today", "High", days=0)
    add_task(ctx, alice, "high, in 2 days", "High", days=2)
    add_task(ctx, alice, "medium, today", "Medium", days=0)
    add_task(ctx, alice, "high, snoozed", "High", snoozed=3)

    data = bell(ctx, a)
    assert [task["text"] for task in data["tasks"]] == [
        "high, overdue",
        "high, today",
        "high, in 2 days",
        "high, in 5 days",
        "high, no date",
        "high, snoozed",
        "medium, overdue",
        "medium, today",
        "low, no date",
    ]
    assert data["count"] == 9
    assert data["overdue_count"] == 2
    assert data["reference_date"] == ctx.today.isoformat()
    first = data["tasks"][0]
    assert first["urgency"] == "overdue"
    assert first["due_on"] == (ctx.today - timedelta(days=1)).isoformat()


def test_finished_tasks_and_other_peoples_tasks_are_left_out(ctx):
    alice, a = make_user(ctx.database, "alice@example.com")
    bob, b = make_user(ctx.database, "bob@example.com")
    add_task(ctx, alice, "alice open", "High")
    add_task(ctx, alice, "alice done", "High", status="completed")
    add_task(ctx, alice, "alice dismissed", "High", status="dismissed")
    add_task(ctx, bob, "bob open", "High")

    assert [task["text"] for task in bell(ctx, a)["tasks"]] == ["alice open"]
    assert [task["text"] for task in bell(ctx, b)["tasks"]] == ["bob open"]


def test_nothing_pending(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert bell(ctx, a) == {
        "reference_date": ctx.today.isoformat(),
        "scope": "all",
        "count": 0,
        "overdue_count": 0,
        "tasks": [],
        "show_badge": True,
        "overdue_alert": True,
    }


def test_bell_needs_sign_in(ctx):
    assert ctx.client.get("/api/notifications").status_code == 401


def test_bell_sits_between_volume_and_profile(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    page = ctx.client.get("/", headers=a).text
    assert (
        page.index('id="soundToggle"')
        < page.index('id="bellToggle"')
        < page.index('id="accountToggle"')
    )
    assert 'aria-controls="bellPanel"' in page
