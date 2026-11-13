"""Local sign-in: email + password accounts, one private inbox per account.

Without LIFE_INBOX_LOCAL_ANONYMOUS the app no longer drops signed-out
visitors into the shared Local Owner account; they must sign in with a
password, and every account only ever sees its own notes, tasks and dashboard.
"""

import importlib
import sys
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
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dev_login.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "dev")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    monkeypatch.delenv("LIFE_INBOX_LOCAL_ANONYMOUS", raising=False)
    monkeypatch.delenv("LIFE_INBOX_ALLOW_DEV_LOGIN", raising=False)
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    yield SimpleNamespace(main=main, database=database)
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


PASSWORD = "correct horse battery"


def sign_in(main, email, password=PASSWORD):
    client = TestClient(main.app)
    response = client.post(
        "/api/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return client


def test_signed_out_visitors_are_not_given_a_shared_inbox(ctx):
    client = TestClient(ctx.main.app)
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/dashboard").status_code == 401
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 302
    assert home.headers["location"] == "/login"
    assert client.get("/api/auth/options").json()["local_anonymous"] is False


def test_login_page_offers_local_sign_in(ctx):
    page = TestClient(ctx.main.app).get("/login").text
    assert 'id="passwordSignIn"' in page
    assert 'type="password"' in page
    assert "Google sign-in is not configured" not in page


def test_each_account_sees_only_its_own_data(ctx):
    alice = sign_in(ctx.main, "alice@example.com")
    bob = sign_in(ctx.main, "bob@example.com")

    note = alice.post("/api/items", json={"raw_text": "Alice's secret plan"}).json()
    assert [item["id"] for item in alice.get("/api/items").json()] == [note["id"]]

    # Bob starts empty and cannot read, edit or delete Alice's note.
    assert bob.get("/api/items").json() == []
    assert bob.get(f"/api/items/{note['id']}").status_code == 404
    assert bob.put(f"/api/items/{note['id']}", json={"raw_text": "hijack"}).status_code == 404
    assert bob.delete(f"/api/items/{note['id']}").status_code == 404
    assert bob.get("/api/dashboard").json()["item_count"] == 0
    assert bob.get("/api/items", params={"q": "secret"}).json() == []

    # Alice's note is untouched.
    assert alice.get(f"/api/items/{note['id']}").json()["raw_text"] == "Alice's secret plan"


def test_new_accounts_start_with_no_notes(ctx):
    client = sign_in(ctx.main, "new@example.com")
    assert client.get("/api/items").json() == []
    dashboard = client.get("/api/dashboard").json()
    assert dashboard["item_count"] == 0
    assert dashboard["recently_captured"] == []
    assert dashboard["open_tasks"] == []


def test_passwordless_dev_login_is_off(ctx):
    client = TestClient(ctx.main.app)
    response = client.post("/api/auth/dev-login", json={"email": "x@example.com"})
    assert response.status_code == 403


def test_sign_in_requires_the_right_password(ctx):
    sign_in(ctx.main, "carol@example.com")
    client = TestClient(ctx.main.app)
    wrong = client.post(
        "/api/auth/login", json={"email": "carol@example.com", "password": "nope-nope"}
    )
    assert wrong.status_code == 401
    assert client.get("/api/items").status_code == 401
    unknown = client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]
    right = client.post(
        "/api/auth/login", json={"email": "Carol@Example.com", "password": PASSWORD}
    )
    assert right.status_code == 200
    assert client.get("/api/items").status_code == 200


def test_passwords_are_stored_hashed(ctx):
    sign_in(ctx.main, "dave@example.com")
    account = ctx.database.get_password_account("dave@example.com")
    assert account["password_hash"].startswith("scrypt$")
    assert PASSWORD not in account["password_hash"]


def test_an_email_can_only_be_registered_once(ctx):
    sign_in(ctx.main, "erin@example.com")
    again = TestClient(ctx.main.app).post(
        "/api/auth/register", json={"email": "erin@example.com", "password": "another-pass"}
    )
    assert again.status_code == 409


def test_short_passwords_are_rejected(ctx):
    response = TestClient(ctx.main.app).post(
        "/api/auth/register", json={"email": "frank@example.com", "password": "short"}
    )
    assert response.status_code == 400


def test_repeated_wrong_passwords_lock_the_account_briefly(ctx):
    sign_in(ctx.main, "gina@example.com")
    client = TestClient(ctx.main.app)
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "gina@example.com", "password": "bad-guess"})
    locked = client.post(
        "/api/auth/login", json={"email": "gina@example.com", "password": PASSWORD}
    )
    assert locked.status_code == 429


def test_google_and_password_sign_in_can_be_used_together(ctx, monkeypatch):
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    page = TestClient(ctx.main.app).get("/login").text
    assert "/api/auth/google/start" in page
    assert 'id="passwordSignIn"' in page
    alice = sign_in(ctx.main, "alice@example.com")
    assert alice.get("/api/items").status_code == 200
