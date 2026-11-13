"""Profile page: name, date of birth and Google status."""

import importlib
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.profile",
    "app.profile_photos",
    "app.integrations.calendar",
)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profile.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in (
        "LIFE_INBOX_ENV",
        "LIFE_INBOX_ALLOWED_EMAILS",
        "LIFE_INBOX_ALLOWED_EMAIL_DOMAINS",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    google_oauth = importlib.import_module("app.google_oauth")
    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, database=database, google_oauth=google_oauth)
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def make_user(database, email, provider="google"):
    user = database.ensure_user(
        email=email, display_name=email.split("@")[0], auth_provider=provider
    )
    token = database.create_auth_session(user["id"])
    return user, {"Cookie": f"life_inbox_session={token}"}


def google_sign_in(ctx, monkeypatch, name="Priya Sharma"):
    start = ctx.client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    info = {"sub": "g-1", "email": "priya@example.com", "email_verified": True, "name": name}
    monkeypatch.setattr(
        ctx.google_oauth.requests, "post",
        lambda *a, **k: FakeResponse(200, {"access_token": "tok"}),
    )
    monkeypatch.setattr(
        ctx.google_oauth.requests, "get", lambda *a, **k: FakeResponse(200, info)
    )
    ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    return ctx.client.get("/api/auth/me").json()


def edit(ctx, headers=None, **fields):
    return ctx.client.patch("/api/me/profile", json=fields, headers=headers or {})


# --- the page ---------------------------------------------------------------


def test_profile_page_needs_sign_in(ctx):
    response = ctx.client.get("/profile", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_profile_page_has_every_section(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    page = ctx.client.get("/profile", headers=a)
    assert page.status_code == 200
    for text in (
        "Profile picture and name",
        "Email and date of birth",
        "Google account",
        "Save changes",
        "/static/profile.js?v=",
    ):
        assert text in page.text, text
    assert "phone" not in page.text.lower()  # phone number was removed


def test_menu_links_to_the_profile_page(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert "window.location.assign('/profile')" in ctx.client.get("/", headers=a).text


# --- name -------------------------------------------------------------------


def test_name_can_be_changed_and_is_tidied(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    response = edit(ctx, a, display_name="  Alice   Mary  Smith ")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Alice Mary Smith"
    assert ctx.client.get("/api/auth/me", headers=a).json()["display_name"] == "Alice Mary Smith"


@pytest.mark.parametrize(
    "name, words",
    [("   ", "Enter your name"), ("x" * 81, "under 80"), ("Ali‮ce", "hidden")],
)
def test_bad_names_are_refused(ctx, name, words):
    _, a = make_user(ctx.database, "alice@example.com")
    response = edit(ctx, a, display_name=name)
    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert ctx.client.get("/api/auth/me", headers=a).json()["display_name"] == "alice"


def test_edited_name_survives_signing_in_with_google_again(ctx, monkeypatch):
    google_sign_in(ctx, monkeypatch)
    edit(ctx, display_name="Priya S.")
    assert google_sign_in(ctx, monkeypatch)["display_name"] == "Priya S."


# --- date of birth ------------------------------------------------------------


def test_date_of_birth_can_be_saved_and_removed(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    user = edit(ctx, a, date_of_birth="1999-08-21").json()
    assert user["date_of_birth"] == "1999-08-21"
    assert ctx.client.get("/api/auth/me", headers=a).json()["date_of_birth"] == "1999-08-21"
    assert edit(ctx, a, date_of_birth="").json()["date_of_birth"] is None


def test_leap_day_birthdays_are_fine(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert edit(ctx, a, date_of_birth="2000-02-29").json()["date_of_birth"] == "2000-02-29"


def test_today_is_allowed(ctx):
    from datetime import datetime, timezone

    _, a = make_user(ctx.database, "alice@example.com")
    today = datetime.now(timezone.utc).date().isoformat()
    assert edit(ctx, a, date_of_birth=today).status_code == 200


@pytest.mark.parametrize(
    "bad, words",
    [
        ("21/08/1999", "full date"),
        ("1999-8-21", "full date"),
        ("next tuesday", "full date"),
        ("2001-02-29", "doesn't exist"),
        ("1999-13-01", "doesn't exist"),
        ("2999-01-01", "future"),
        ("1899-12-31", "after 1900"),
    ],
)
def test_bad_dates_are_refused(ctx, bad, words):
    _, a = make_user(ctx.database, "alice@example.com")
    edit(ctx, a, date_of_birth="1999-08-21")
    response = edit(ctx, a, date_of_birth=bad)
    assert response.status_code == 400
    assert words in response.json()["detail"]
    assert ctx.client.get("/api/auth/me", headers=a).json()["date_of_birth"] == "1999-08-21"


def test_phone_number_is_gone(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    me = ctx.client.get("/api/auth/me", headers=a).json()
    assert not any("phone" in key for key in me)
    # An old client sending a phone number changes nothing.
    assert "phone_number" not in edit(ctx, a, phone_number="98765 43210").json()


def test_fields_left_out_are_not_changed(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    edit(ctx, a, display_name="Alice", date_of_birth="1999-08-21")
    user = edit(ctx, a, display_name="Alice B").json()
    assert user["date_of_birth"] == "1999-08-21"
    assert edit(ctx, a, date_of_birth="2000-01-01").json()["display_name"] == "Alice B"


# --- Google connection, privacy, safety --------------------------------------


def test_google_connection_is_reported(ctx, monkeypatch):
    me = google_sign_in(ctx, monkeypatch)
    assert me["google_connected"] is True
    assert me["last_login_at"]
    _, d = make_user(ctx.database, "dev@example.com", provider="dev")
    assert ctx.client.get("/api/auth/me", headers=d).json()["google_connected"] is False


def test_email_cannot_be_changed_here(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    response = edit(ctx, a, email="someone@else.com", display_name="Alice")
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


def test_people_only_change_their_own_profile(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    _, b = make_user(ctx.database, "bob@example.com")
    edit(ctx, b, display_name="Bobby", date_of_birth="1990-01-01")
    alice = ctx.client.get("/api/auth/me", headers=a).json()
    assert alice["display_name"] == "alice" and alice["date_of_birth"] is None
    assert edit(ctx, display_name="Nobody").status_code == 401


def test_other_sites_cannot_edit_your_profile(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    response = ctx.client.patch(
        "/api/me/profile",
        json={"display_name": "Hacked"},
        headers={**a, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert ctx.client.get("/api/auth/me", headers=a).json()["display_name"] == "alice"
