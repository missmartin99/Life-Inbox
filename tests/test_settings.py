"""Settings: notifications, theme and connected apps."""

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

DEFAULTS = {
    "theme": "light",
    "bell_badge": True,
    "bell_overdue_alert": True,
    "bell_scope": "all",
}


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY", "LIFE_INBOX_CALENDAR_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    from app.attention.dates import today_in_configured_tz

    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, database=database, today=today_in_configured_tz())
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def make_user(database, email, provider="google"):
    user = database.ensure_user(
        email=email, display_name=email.split("@")[0], auth_provider=provider
    )
    if provider == "google":
        with database.SessionLocal() as session:
            session.get(database.UserRecord, user["id"]).google_sub = f"sub-{email}"
            session.commit()
    token = database.create_auth_session(user["id"])
    return user, {"Cookie": f"life_inbox_session={token}"}


def save(ctx, headers=None, **changes):
    return ctx.client.patch("/api/me/settings", json=changes, headers=headers or {})


def me(ctx, headers):
    return ctx.client.get("/api/auth/me", headers=headers).json()


def add_task(ctx, owner, text, priority, days=None):
    db = ctx.database
    with db.SessionLocal() as session:
        session.add(
            db.TaskRecord(
                text=text,
                priority=priority,
                status="open",
                dedup_key=f"{owner['id']}:{text}",
                owner_user_id=owner["id"],
                due_on=(ctx.today + timedelta(days=days)).isoformat() if days is not None else None,
                due_confidence="high" if days is not None else "none",
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()


# --- the page and menu --------------------------------------------------------


def test_settings_page_needs_sign_in(ctx):
    response = ctx.client.get("/settings", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/login"


def test_settings_page_has_every_section(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    page = ctx.client.get("/settings", headers=a).text
    for text in ("Notification settings", "Theme preferences", "Connected apps", "/static/settings.js?v="):
        assert text in page, text


def test_menu_links_to_settings(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert "window.location.assign('/settings')" in ctx.client.get("/", headers=a).text


@pytest.mark.parametrize("path", ["/", "/profile", "/settings"])
def test_theme_is_applied_before_the_page_paints(ctx, path):
    _, a = make_user(ctx.database, "alice@example.com")
    page = ctx.client.get(path, headers=a).text
    assert "/static/theme.js?v=" in page
    assert page.index("/static/theme.js") < page.index("/static/styles.css")


# --- saving settings ------------------------------------------------------------


def test_new_accounts_get_the_defaults(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert me(ctx, a)["settings"] == DEFAULTS


def test_settings_save_one_at_a_time(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert save(ctx, a, theme="light").json() == {**DEFAULTS, "theme": "light"}
    assert save(ctx, a, bell_scope="high", bell_badge=False).json() == {
        **DEFAULTS, "theme": "light", "bell_scope": "high", "bell_badge": False,
    }
    assert me(ctx, a)["settings"]["theme"] == "light"


@pytest.mark.parametrize(
    "changes",
    [{"theme": "purple"}, {"bell_scope": "sometimes"}, {"bell_badge": "maybe"}, {"font": "big"}],
)
def test_bad_settings_are_refused(ctx, changes):
    _, a = make_user(ctx.database, "alice@example.com")
    assert save(ctx, a, **changes).status_code == 422
    assert me(ctx, a)["settings"] == DEFAULTS


def test_unreadable_saved_settings_fall_back_to_defaults(ctx):
    alice, a = make_user(ctx.database, "alice@example.com")
    db = ctx.database
    with db.SessionLocal() as session:
        session.get(db.UserRecord, alice["id"]).settings_json = '{"theme": "neon", "bell_badge": false'
        session.commit()
    assert me(ctx, a)["settings"] == DEFAULTS
    with db.SessionLocal() as session:
        session.get(db.UserRecord, alice["id"]).settings_json = '{"theme": "neon", "bell_badge": false}'
        session.commit()
    assert me(ctx, a)["settings"] == {**DEFAULTS, "bell_badge": False}


def test_settings_are_per_person_and_private(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    _, b = make_user(ctx.database, "bob@example.com")
    save(ctx, a, theme="dark")
    assert me(ctx, b)["settings"]["theme"] == "light"
    assert save(ctx, theme="light").status_code == 401
    blocked = ctx.client.patch(
        "/api/me/settings",
        json={"theme": "dark"},
        headers={**a, "Origin": "https://evil.example"},
    )
    assert blocked.status_code == 403
    assert me(ctx, a)["settings"]["theme"] == "dark"


# --- the bell follows the notification settings ---------------------------------


def test_bell_scope_and_badge_settings(ctx):
    alice, a = make_user(ctx.database, "alice@example.com")
    add_task(ctx, alice, "high later", "High", days=5)
    add_task(ctx, alice, "low overdue", "Low", days=-1)
    add_task(ctx, alice, "medium today", "Medium", days=0)
    add_task(ctx, alice, "medium undated", "Medium")

    def bell():
        return ctx.client.get("/api/notifications", headers=a).json()

    everything = bell()
    assert [t["text"] for t in everything["tasks"]] == [
        "high later", "medium today", "medium undated", "low overdue",
    ]
    assert everything["scope"] == "all"
    assert everything["show_badge"] is True and everything["overdue_alert"] is True

    save(ctx, a, bell_scope="due_soon")
    soon = bell()
    assert [t["text"] for t in soon["tasks"]] == ["medium today", "low overdue"]
    assert soon["count"] == 2 and soon["overdue_count"] == 1

    save(ctx, a, bell_scope="high", bell_badge=False, bell_overdue_alert=False)
    high = bell()
    assert [t["text"] for t in high["tasks"]] == ["high later"]
    assert high["overdue_count"] == 0
    assert high["show_badge"] is False and high["overdue_alert"] is False


# --- connected apps ---------------------------------------------------------------


def apps(ctx, headers):
    response = ctx.client.get("/api/me/connections", headers=headers)
    assert response.status_code == 200
    return {app["id"]: app for app in response.json()["apps"]}


def test_connected_apps_report_honestly(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    found = apps(ctx, a)
    assert list(found) == ["google", "google_calendar", "openai"]
    assert found["google"]["status"] == "connected"
    assert "alice@example.com" in found["google"]["detail"]
    assert found["google"]["manage_url"] == "https://myaccount.google.com/connections"
    # Ready to connect; see tests/test_google_calendar.py for the rest.
    assert found["google_calendar"]["status"] == "not_connected"
    assert found["google_calendar"]["calendar"]["available"] is True
    assert found["openai"]["status"] == "off"
    assert "nothing is sent to OpenAI" in found["openai"]["detail"]


def test_ai_shows_as_on_when_the_server_has_a_key(ctx, monkeypatch):
    _, a = make_user(ctx.database, "alice@example.com")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    openai = apps(ctx, a)["openai"]
    assert openai["status"] == "active"
    assert "sent to OpenAI" in openai["detail"]
    assert "sk-test" not in str(openai)


def test_accounts_without_google_say_so(ctx):
    _, d = make_user(ctx.database, "dev@example.com", provider="dev")
    google = apps(ctx, d)["google"]
    assert google["status"] == "not_connected" and google["manage_url"] is None


def test_connected_apps_need_sign_in(ctx):
    assert ctx.client.get("/api/me/connections").status_code == 401
