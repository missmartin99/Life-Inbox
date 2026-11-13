"""Google Calendar: connect, keep dated tasks on a "Life Inbox" calendar, disconnect.

Google is replaced by an in-memory fake, so no test talks to the network.
"""

import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi.testclient import TestClient

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.integrations.calendar",
    "app.integrations.google_calendar",
)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/calendar/v3"


class FakeResponse:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeGoogle:
    """Just enough of Google's OAuth and Calendar APIs."""

    def __init__(self):
        self.calls = []
        self.calendars = {}  # id -> {"summary", "timeZone", "events": {id: body}}
        self.access_tokens = set()
        self.refresh_tokens = set()
        self.scope = f"openid https://www.googleapis.com/auth/userinfo.email {CALENDAR_SCOPE}"
        self.email = "alice@gmail.com"
        self.api_disabled = False
        self.revoked = []
        self._counter = 0

    def _next(self, prefix):
        self._counter += 1
        return f"{prefix}{self._counter}"

    def api_calls(self):
        return [(m, u) for m, u in self.calls if u.startswith(API_BASE)]

    def events(self, calendar_id=None):
        if calendar_id is None:
            (calendar_id,) = self.calendars
        return self.calendars[calendar_id]["events"]

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        if url == TOKEN_URL:
            return self._token(kwargs["data"])
        if url == REVOKE_URL:
            self.revoked.append(kwargs["data"]["token"])
            self.refresh_tokens.discard(kwargs["data"]["token"])
            return FakeResponse(200, {})
        denied = self._check_auth(kwargs)
        if denied:
            return denied
        if url == USERINFO_URL:
            return FakeResponse(200, {"sub": "g-1", "email": self.email})
        assert url.startswith(API_BASE), url
        if self.api_disabled:
            return FakeResponse(
                403,
                {"error": {"code": 403, "status": "PERMISSION_DENIED",
                           "errors": [{"reason": "accessNotConfigured"}]}},
            )
        return self._calendar_api(method, [unquote(p) for p in url[len(API_BASE):].split("/")[1:]], kwargs.get("json"))

    def _token(self, data):
        if data["grant_type"] == "authorization_code":
            access, refresh = self._next("access-"), self._next("refresh-")
            self.access_tokens.add(access)
            self.refresh_tokens.add(refresh)
            return FakeResponse(200, {
                "access_token": access, "refresh_token": refresh, "expires_in": 3599,
                "scope": self.scope, "token_type": "Bearer",
            })
        if data["refresh_token"] not in self.refresh_tokens:
            return FakeResponse(400, {"error": "invalid_grant"})
        access = self._next("access-")
        self.access_tokens.add(access)
        return FakeResponse(200, {"access_token": access, "expires_in": 3599, "scope": self.scope})

    def _check_auth(self, kwargs):
        header = (kwargs.get("headers") or {}).get("Authorization", "")
        if header.removeprefix("Bearer ") not in self.access_tokens:
            return FakeResponse(401, {"error": {"code": 401, "status": "UNAUTHENTICATED"}})
        return None

    def _calendar_api(self, method, parts, body):
        if parts == ["calendars"] and method == "POST":
            calendar_id = self._next("cal-") + "@group.calendar.google.com"
            self.calendars[calendar_id] = {**body, "events": {}}
            return FakeResponse(200, {"id": calendar_id, **body})
        calendar = self.calendars.get(parts[1])
        if calendar is None:
            return FakeResponse(404, {"error": {"code": 404, "status": "NOT_FOUND"}})
        if len(parts) == 2 and method == "DELETE":
            del self.calendars[parts[1]]
            return FakeResponse(204)
        events = calendar["events"]
        if len(parts) == 3 and method == "POST":
            event_id = self._next("evt-")
            events[event_id] = body
            return FakeResponse(200, {"id": event_id, **body})
        event_id = parts[3]
        if event_id not in events:
            return FakeResponse(410 if method == "DELETE" else 404, {"error": {"code": 404}})
        if method == "PUT":
            events[event_id] = body
            return FakeResponse(200, {"id": event_id, **body})
        if method == "DELETE":
            del events[event_id]
            return FakeResponse(204)
        raise AssertionError((method, parts))


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'calendar.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("LIFE_INBOX_AUTO_ANALYZE", "0")
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY", "LIFE_INBOX_GOOGLE_CALENDAR", "LIFE_INBOX_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    calendar = importlib.import_module("app.integrations.google_calendar")
    from app.attention.dates import today_in_configured_tz

    google = FakeGoogle()
    monkeypatch.setattr(calendar, "_http", google)

    def no_network(*args, **kwargs):
        raise AssertionError("a test tried to reach the real Google")

    import requests

    monkeypatch.setattr(requests, "request", no_network)
    with TestClient(main.app) as client:
        yield SimpleNamespace(
            client=client, main=main, database=database, calendar=calendar,
            google=google, today=today_in_configured_tz(),
        )
    main.calendar_syncer.wait_for_idle()
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def sign_in(ctx, email):
    """Make a Google user and use their session for the client's next requests."""
    database = ctx.database
    user = database.ensure_user(email=email, display_name=email.split("@")[0], auth_provider="google")
    with database.SessionLocal() as session:
        session.get(database.UserRecord, user["id"]).google_sub = f"sub-{email}"
        session.commit()
    ctx.client.cookies.clear()
    ctx.client.cookies.set("life_inbox_session", database.create_auth_session(user["id"]))
    return user


def add_task(ctx, owner, text, priority="High", *, days=None, timing=None, status="open"):
    db = ctx.database
    with db.SessionLocal() as session:
        task = db.TaskRecord(
            text=text,
            priority=priority,
            status=status,
            timing_text=timing,
            dedup_key=f"{owner['id']}:{text}",
            owner_user_id=owner["id"],
            due_on=(ctx.today + timedelta(days=days)).isoformat() if days is not None else None,
            due_confidence="high" if days is not None else "none",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        session.add(task)
        session.commit()
        return task.id


def start_connect(ctx):
    response = ctx.client.get("/api/auth/google/calendar/start", follow_redirects=False)
    assert response.status_code == 302, response.text
    return response, parse_qs(urlparse(response.headers["location"]).query)


def finish(ctx, query, **params):
    return ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "the-code", "state": query["state"][0], **params},
        follow_redirects=False,
    )


def connect(ctx):
    _, query = start_connect(ctx)
    done = finish(ctx, query)
    assert done.headers["location"] == "/settings?calendar=connected"
    ctx.main.calendar_syncer.wait_for_idle()


def status(ctx):
    response = ctx.client.get("/api/calendar/google")
    assert response.status_code == 200
    return response.json()


def sync(ctx):
    ctx.main.calendar_syncer.wait_for_idle()
    assert ctx.client.post("/api/calendar/google/sync").status_code == 200
    ctx.main.calendar_syncer.wait_for_idle()


# --- connecting -----------------------------------------------------------------------


def test_connect_asks_google_only_for_its_own_calendar(ctx):
    sign_in(ctx, "alice@example.com")
    response, query = start_connect(ctx)
    assert response.headers["location"].startswith("https://accounts.google.com/")
    assert query["scope"] == [f"openid email {CALENDAR_SCOPE}"]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["login_hint"] == ["alice@example.com"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and query["state"][0]
    assert "life_inbox_oauth" in response.headers["set-cookie"]


def test_connect_needs_sign_in(ctx):
    response = ctx.client.get("/api/auth/google/calendar/start", follow_redirects=False)
    assert response.status_code == 401


def test_connecting_stores_tokens_encrypted_and_reports_connected(ctx):
    alice = sign_in(ctx, "alice@example.com")
    assert status(ctx)["status"] == "not_connected"
    connect(ctx)

    found = status(ctx)
    assert found["connected"] is True and found["status"] == "connected"
    assert found["email"] == "alice@gmail.com"
    assert found["calendar_name"] == "Life Inbox"

    with ctx.database.SessionLocal() as session:
        record = session.query(ctx.database.CalendarConnectionRecord).one()
        assert record.user_id == alice["id"]
        assert "refresh-" not in record.refresh_token_enc
        assert "access-" not in (record.access_token_enc or "")
        assert CALENDAR_SCOPE in record.scopes
    # The token response never reaches the browser.
    assert "refresh-" not in str(found)


def test_unticking_the_calendar_permission_connects_nothing(ctx):
    sign_in(ctx, "alice@example.com")
    ctx.google.scope = "openid https://www.googleapis.com/auth/userinfo.email"
    _, query = start_connect(ctx)
    assert finish(ctx, query).headers["location"] == "/settings?calendar=missing_scope"
    assert status(ctx)["connected"] is False


def test_saying_no_on_google_connects_nothing(ctx):
    sign_in(ctx, "alice@example.com")
    _, query = start_connect(ctx)
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"error": "access_denied", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/settings?calendar=denied"
    assert status(ctx)["connected"] is False
    assert not ctx.google.calls


def test_a_calendar_is_never_attached_to_someone_else(ctx):
    sign_in(ctx, "alice@example.com")
    _, query = start_connect(ctx)
    oauth_cookie = ctx.client.cookies.get("life_inbox_oauth")
    # Bob finishes the flow Alice started.
    sign_in(ctx, "bob@example.com")
    ctx.client.cookies.set("life_inbox_oauth", oauth_cookie, path="/api/auth/google")
    assert finish(ctx, query).headers["location"] == "/settings?calendar=wrong_user"
    assert status(ctx)["connected"] is False
    with ctx.database.SessionLocal() as session:
        assert session.query(ctx.database.CalendarConnectionRecord).count() == 0


def test_forged_state_is_refused(ctx):
    sign_in(ctx, "alice@example.com")
    start_connect(ctx)
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "x", "state": "forged"},
        follow_redirects=False,
    )
    # A bad state is treated as a failed sign-in, and nothing is connected.
    assert done.headers["location"] == "/login?error=state"
    assert status(ctx)["connected"] is False


def test_sign_in_still_works_alongside_calendar(ctx, monkeypatch):
    """The sign-in flow shares the callback URL; it must be unaffected."""
    google_oauth = importlib.import_module("app.google_oauth")
    response = ctx.client.get("/api/auth/google/start", follow_redirects=False)
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["scope"] == ["openid email profile"]
    assert "access_type" not in query
    monkeypatch.setattr(google_oauth.requests, "post", lambda *a, **k: FakeResponse(200, {"access_token": "t"}))
    monkeypatch.setattr(
        google_oauth.requests, "get",
        lambda *a, **k: FakeResponse(200, {"sub": "g-9", "email": "new@example.com", "email_verified": True}),
    )
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/"
    assert ctx.client.get("/api/auth/me").json()["email"] == "new@example.com"


# --- what goes on the calendar ---------------------------------------------------------


def test_dated_open_tasks_go_on_a_life_inbox_calendar(ctx):
    alice = sign_in(ctx, "alice@example.com")
    pay = add_task(ctx, alice, "Pay rent", "High", days=1)
    add_task(ctx, alice, "Read a book", "Low")  # no date: stays off the calendar
    add_task(ctx, alice, "Old thing", "Medium", days=2, status="completed")
    connect(ctx)

    (calendar_id,) = ctx.google.calendars
    calendar = ctx.google.calendars[calendar_id]
    assert calendar["summary"] == "Life Inbox"
    (event,) = calendar["events"].values()
    due = (ctx.today + timedelta(days=1)).isoformat()
    assert event["summary"] == "Pay rent"
    assert event["start"] == {"date": due}
    assert event["end"] == {"date": (ctx.today + timedelta(days=2)).isoformat()}
    assert event["transparency"] == "transparent"  # never shows you as busy
    assert event["colorId"] == "11"
    assert event["extendedProperties"]["private"]["lifeInboxTaskId"] == str(pay)
    assert "http://testserver/" in event["description"]

    found = status(ctx)
    assert found["event_count"] == 1 and found["last_synced_at"] and found["last_error"] is None


def test_a_time_of_day_makes_a_half_hour_event(ctx, monkeypatch):
    monkeypatch.setenv("LIFE_INBOX_TIMEZONE", "Asia/Kolkata")
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Call the bank", "Medium", timing="tomorrow at 3pm")
    connect(ctx)
    (event,) = ctx.google.events().values()
    assert event["start"]["timeZone"] == "Asia/Kolkata"
    start = datetime.fromisoformat(event["start"]["dateTime"])
    end = datetime.fromisoformat(event["end"]["dateTime"])
    assert (start.hour, start.minute) == (15, 0)
    assert end - start == timedelta(minutes=30)
    assert event["colorId"] == "5"


def test_no_calendar_is_made_until_there_is_a_dated_task(ctx):
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Someday", "Low")
    connect(ctx)
    assert ctx.google.calendars == {}
    add_task(ctx, alice, "Dentist", "High", days=3)
    sync(ctx)
    assert len(ctx.google.calendars) == 1


def test_changes_to_tasks_follow_onto_the_calendar(ctx):
    alice = sign_in(ctx, "alice@example.com")
    rent = add_task(ctx, alice, "Pay rent", "High", days=1)
    gym = add_task(ctx, alice, "Gym", "Low", days=2)
    connect(ctx)
    assert len(ctx.google.events()) == 2

    # Finishing a task takes it off; this goes through the normal API, which
    # queues a sync by itself.
    assert ctx.client.post(f"/api/tasks/{gym}/complete").status_code == 200
    ctx.main.calendar_syncer.wait_for_idle()
    assert [e["summary"] for e in ctx.google.events().values()] == ["Pay rent"]

    # A new date moves the event (same event, updated in place).
    (event_id,) = ctx.google.events()
    assert ctx.client.post(
        f"/api/tasks/{rent}/confirm-timing", json={"timing_text": "in 5 days"}
    ).status_code == 200
    ctx.main.calendar_syncer.wait_for_idle()
    assert list(ctx.google.events()) == [event_id]
    assert ctx.google.events()[event_id]["start"] == {"date": (ctx.today + timedelta(days=5)).isoformat()}

    # Reopening puts it back.
    assert ctx.client.post(f"/api/tasks/{gym}/reopen").status_code == 200
    ctx.main.calendar_syncer.wait_for_idle()
    assert len(ctx.google.events()) == 2


def test_nothing_is_sent_when_nothing_changed(ctx):
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)
    before = len(ctx.google.api_calls())
    sync(ctx)
    sync(ctx)
    assert len(ctx.google.api_calls()) == before


def test_deleted_task_comes_off_the_calendar(ctx):
    alice = sign_in(ctx, "alice@example.com")
    task_id = add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)
    with ctx.database.SessionLocal() as session:
        session.delete(session.get(ctx.database.TaskRecord, task_id))
        session.commit()
    sync(ctx)
    assert ctx.google.events() == {}
    assert status(ctx)["event_count"] == 0


def test_an_event_deleted_in_google_is_put_back_when_the_task_changes(ctx):
    alice = sign_in(ctx, "alice@example.com")
    task_id = add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)
    ctx.google.events().clear()
    ctx.client.post(f"/api/tasks/{task_id}/confirm-timing", json={"timing_text": "in 3 days"})
    ctx.main.calendar_syncer.wait_for_idle()
    (event,) = ctx.google.events().values()
    assert event["summary"] == "Pay rent"


def test_a_deleted_life_inbox_calendar_is_made_again(ctx):
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)
    ctx.google.calendars.clear()  # deleted in Google Calendar
    add_task(ctx, alice, "Dentist", "Medium", days=2)
    sync(ctx)
    (calendar,) = ctx.google.calendars.values()
    assert sorted(e["summary"] for e in calendar["events"].values()) == ["Dentist", "Pay rent"]
    assert status(ctx)["last_error"] is None


# --- tokens and errors --------------------------------------------------------------------


def test_an_expired_access_token_is_refreshed(ctx):
    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    with ctx.database.SessionLocal() as session:
        record = session.query(ctx.database.CalendarConnectionRecord).one()
        record.access_token_expires_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        session.commit()
    add_task(ctx, alice, "Pay rent", "High", days=1)
    sync(ctx)
    refreshes = [c for c in ctx.google.calls if c[1] == TOKEN_URL]
    assert len(refreshes) == 2  # the first connect, then one refresh
    assert len(ctx.google.events()) == 1


def test_a_token_google_stopped_accepting_is_refreshed_once(ctx):
    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    ctx.google.access_tokens.clear()  # Google ended the access token early
    add_task(ctx, alice, "Pay rent", "High", days=1)
    sync(ctx)
    assert len(ctx.google.events()) == 1


def test_access_removed_in_google_asks_to_reconnect(ctx):
    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    ctx.google.access_tokens.clear()
    ctx.google.refresh_tokens.clear()  # removed at myaccount.google.com
    add_task(ctx, alice, "Pay rent", "High", days=1)
    sync(ctx)
    found = status(ctx)
    assert found["status"] == "needs_reconnect" and found["connected"] is False
    assert "Reconnect" in found["last_error"]
    card = {a["id"]: a for a in ctx.client.get("/api/me/connections").json()["apps"]}["google_calendar"]
    assert card["status"] == "needs_reconnect"

    # Reconnecting the same Google account keeps the calendar.
    connect(ctx)
    assert status(ctx)["status"] == "connected"
    assert len(ctx.google.calendars) == 1 and len(ctx.google.events()) == 1


def test_a_changed_server_secret_asks_to_reconnect(ctx, monkeypatch):
    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    monkeypatch.setenv("LIFE_INBOX_SESSION_SECRET", "a-completely-different-secret-value-123")
    add_task(ctx, alice, "Pay rent", "High", days=1)
    sync(ctx)
    assert status(ctx)["status"] == "needs_reconnect"


def test_calendar_api_turned_off_is_explained(ctx):
    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    ctx.google.api_disabled = True
    add_task(ctx, alice, "Pay rent", "High", days=1)
    sync(ctx)
    found = status(ctx)
    assert found["status"] == "connected"  # nothing for the person to redo
    assert "Google Calendar API is turned off" in found["last_error"]
    ctx.google.api_disabled = False
    sync(ctx)
    assert status(ctx)["last_error"] is None and len(ctx.google.events()) == 1


def test_google_unreachable_is_retried_later(ctx, monkeypatch):
    import requests

    alice = sign_in(ctx, "alice@example.com")
    connect(ctx)
    add_task(ctx, alice, "Pay rent", "High", days=1)

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(ctx.calendar, "_http", offline)
    sync(ctx)
    assert "Couldn't reach Google Calendar" in status(ctx)["last_error"]
    monkeypatch.setattr(ctx.calendar, "_http", ctx.google)
    assert ctx.client.get("/").status_code == 200  # opening the inbox retries
    ctx.main.calendar_syncer.wait_for_idle()
    assert status(ctx)["last_error"] is None and len(ctx.google.events()) == 1


# --- disconnecting ---------------------------------------------------------------------------


def test_disconnect_removes_the_calendar_and_gives_access_back(ctx):
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)
    refresh_token = next(iter(ctx.google.refresh_tokens))

    response = ctx.client.delete("/api/calendar/google")
    assert response.status_code == 200
    body = response.json()
    assert body["calendar_removed"] is True and body["access_removed"] is True
    assert body["calendar"]["status"] == "not_connected"
    assert ctx.google.calendars == {}
    assert ctx.google.revoked == [refresh_token]
    with ctx.database.SessionLocal() as session:
        assert session.query(ctx.database.CalendarConnectionRecord).count() == 0
        assert session.query(ctx.database.CalendarTaskEventRecord).count() == 0

    # Later task changes no longer reach Google.
    calls = len(ctx.google.calls)
    add_task(ctx, alice, "Dentist", "High", days=2)
    ctx.client.get("/")
    ctx.main.calendar_syncer.wait_for_idle()
    assert len(ctx.google.calls) == calls
    assert ctx.client.delete("/api/calendar/google").status_code == 404


def test_disconnect_works_even_when_google_cannot_be_reached(ctx, monkeypatch):
    import requests

    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Pay rent", "High", days=1)
    connect(ctx)

    def offline(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(ctx.calendar, "_http", offline)
    body = ctx.client.delete("/api/calendar/google").json()
    assert body["disconnected"] is True and body["calendar_removed"] is False
    assert status(ctx)["status"] == "not_connected"


def test_disconnect_and_sync_are_blocked_cross_origin(ctx):
    sign_in(ctx, "alice@example.com")
    connect(ctx)
    evil = {"Origin": "https://evil.example"}
    assert ctx.client.delete("/api/calendar/google", headers=evil).status_code == 403
    assert ctx.client.post("/api/calendar/google/sync", headers=evil).status_code == 403
    assert status(ctx)["connected"] is True


# --- privacy between people ---------------------------------------------------------------------


def test_each_person_has_their_own_calendar(ctx):
    alice = sign_in(ctx, "alice@example.com")
    add_task(ctx, alice, "Alice's dentist", "High", days=1)
    connect(ctx)

    bob = sign_in(ctx, "bob@example.com")
    add_task(ctx, bob, "Bob's flight", "High", days=2)
    assert status(ctx)["status"] == "not_connected"
    assert ctx.client.post("/api/calendar/google/sync").status_code == 409
    assert ctx.client.delete("/api/calendar/google").status_code == 404
    ctx.client.get("/")
    ctx.main.calendar_syncer.wait_for_idle()
    # Bob is not connected, so his task goes nowhere; Alice's calendar has only hers.
    (calendar,) = ctx.google.calendars.values()
    assert [e["summary"] for e in calendar["events"].values()] == ["Alice's dentist"]

    ctx.google.email = "bob@gmail.com"
    connect(ctx)
    assert len(ctx.google.calendars) == 2
    summaries = sorted(
        e["summary"] for c in ctx.google.calendars.values() for e in c["events"].values()
    )
    assert summaries == ["Alice's dentist", "Bob's flight"]


def test_status_needs_sign_in(ctx):
    assert ctx.client.get("/api/calendar/google").status_code == 401
    assert ctx.client.post("/api/calendar/google/sync").status_code == 401
    assert ctx.client.delete("/api/calendar/google").status_code == 401


# --- the Settings card ---------------------------------------------------------------------------


def test_connected_apps_card_follows_the_connection(ctx):
    sign_in(ctx, "alice@example.com")

    def card():
        apps = ctx.client.get("/api/me/connections").json()["apps"]
        return {a["id"]: a for a in apps}["google_calendar"]

    before = card()
    assert before["status"] == "not_connected"
    assert "Life Inbox" in before["detail"] and "never your other events" in before["detail"]
    connect(ctx)
    after = card()
    assert after["status"] == "connected"
    assert "alice@gmail.com" in after["detail"]
    assert after["calendar"]["connected"] is True


def test_calendar_can_be_switched_off_for_the_server(ctx, monkeypatch):
    sign_in(ctx, "alice@example.com")
    monkeypatch.setenv("LIFE_INBOX_GOOGLE_CALENDAR", "0")
    assert status(ctx)["status"] == "unavailable"
    assert ctx.client.get("/api/auth/google/calendar/start", follow_redirects=False).status_code == 404
