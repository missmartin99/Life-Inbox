"""Google sign-in, server-side sessions, and per-user data isolation.

The app runs in Google auth mode here (no local-owner fallback). Google's
servers are never contacted: HTTP calls are replaced with fakes.
"""

import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
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
    "app.integrations.calendar",
)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auth_test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    monkeypatch.delenv("LIFE_INBOX_ALLOWED_EMAILS", raising=False)
    monkeypatch.delenv("LIFE_INBOX_ALLOWED_EMAIL_DOMAINS", raising=False)
    monkeypatch.delenv("LIFE_INBOX_ENV", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    google_oauth = importlib.import_module("app.google_oauth")
    with TestClient(main.app) as client:
        yield SimpleNamespace(
            client=client, main=main, database=database, google_oauth=google_oauth
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


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


# --- anonymous access -------------------------------------------------------


def test_anonymous_requests_are_rejected(ctx):
    client = ctx.client
    for path in ("/api/items", "/api/dashboard", "/api/tasks", "/api/auth/me"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/items", json={"raw_text": "x"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_home_page_redirects_to_login(ctx):
    response = ctx.client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    login = ctx.client.get("/login")
    assert login.status_code == 200
    assert "Sign in with Google" in login.text


def test_data_layer_refuses_anonymous_access(ctx):
    with pytest.raises(ctx.database.AuthRequiredError):
        ctx.database.require_owner_id()


# --- isolation --------------------------------------------------------------


def test_users_cannot_read_or_change_each_others_data(ctx):
    client = ctx.client
    alice, a = make_user(ctx.database, "alice@example.com")
    bob, b = make_user(ctx.database, "bob@example.com")

    item = client.post(
        "/api/items", json={"raw_text": "Alice private note about taxes"}, headers=a
    ).json()
    item_id = item["id"]
    assert item["owner_user_id"] == alice["id"]

    assert item_id not in {i["id"] for i in client.get("/api/items", headers=b).json()}

    attempts = [
        ("get", f"/api/items/{item_id}", {}),
        ("put", f"/api/items/{item_id}", {"json": {"raw_text": "hijacked"}}),
        ("delete", f"/api/items/{item_id}", {}),
        ("get", f"/api/items/{item_id}/file", {}),
        ("get", f"/api/items/{item_id}/insight", {}),
        ("patch", f"/api/items/{item_id}/insight", {"json": {"user_title": "hijacked"}}),
        (
            "patch",
            f"/api/items/{item_id}/extracted-text",
            {"json": {"extracted_text": "hijacked"}},
        ),
        ("post", f"/api/items/{item_id}/retry", {}),
    ]
    for method, path, kwargs in attempts:
        response = getattr(client, method)(path, headers=b, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)

    still = client.get(f"/api/items/{item_id}", headers=a)
    assert still.status_code == 200
    assert still.json()["raw_text"].startswith("Alice private")


def test_dashboard_relations_and_ask_are_scoped(ctx):
    client = ctx.client
    alice, a = make_user(ctx.database, "alice@example.com")
    bob, b = make_user(ctx.database, "bob@example.com")
    item = client.post(
        "/api/items", json={"raw_text": "Alice yacht tax filing deadline"}, headers=a
    ).json()

    alice_dash = client.get("/api/dashboard", headers=a).json()
    bob_dash = client.get("/api/dashboard", headers=b).json()
    assert item["id"] in {i["id"] for i in alice_dash["recently_captured"]}
    assert item["id"] not in {i["id"] for i in bob_dash["recently_captured"]}
    assert client.get("/api/relations", headers=b).json() == []

    ask = client.post(
        "/api/ask", json={"question": "What about the yacht tax deadline?"}, headers=b
    ).json()
    assert all(c["item_id"] != item["id"] for c in ask["citations"])


def test_each_user_gets_their_own_default_topics(ctx):
    client = ctx.client
    _, a = make_user(ctx.database, "alice@example.com")
    _, b = make_user(ctx.database, "bob@example.com")
    topics_a = client.get("/api/topics", headers=a).json()
    topics_b = client.get("/api/topics", headers=b).json()
    assert topics_a and topics_b
    assert {t["id"] for t in topics_a}.isdisjoint({t["id"] for t in topics_b})


# --- sessions ---------------------------------------------------------------


def test_logout_revokes_the_session_on_the_server(ctx):
    client = ctx.client
    _, headers = make_user(ctx.database, "alice@example.com")
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).status_code == 200
    # The copied cookie no longer works, even though it has not expired.
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_logout_all_signs_out_every_device(ctx):
    client = ctx.client
    user, phone = make_user(ctx.database, "alice@example.com")
    laptop = {
        "Cookie": f"life_inbox_session={ctx.database.create_auth_session(user['id'])}"
    }
    assert client.post("/api/auth/logout-all", headers=phone).status_code == 200
    assert client.get("/api/auth/me", headers=phone).status_code == 401
    assert client.get("/api/auth/me", headers=laptop).status_code == 401


def test_expired_session_is_rejected(ctx):
    client = ctx.client
    user, headers = make_user(ctx.database, "alice@example.com")
    with ctx.database.SessionLocal() as session:
        for row in session.query(ctx.database.AuthSessionRecord).all():
            row.expires_at = "2000-01-01T00:00:00+00:00"
        session.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_session_tokens_are_stored_hashed(ctx):
    user, headers = make_user(ctx.database, "alice@example.com")
    raw = headers["Cookie"].split("=", 1)[1]
    with ctx.database.SessionLocal() as session:
        stored = [r.token_hash for r in session.query(ctx.database.AuthSessionRecord)]
    assert stored and raw not in stored


def test_spoofed_user_id_header_is_ignored(ctx):
    client = ctx.client
    alice, a = make_user(ctx.database, "alice@example.com")
    bob, _ = make_user(ctx.database, "bob@example.com")
    me = client.get("/api/auth/me", headers={**a, "X-User-Id": str(bob["id"])}).json()
    assert me["id"] == alice["id"]


def test_cross_origin_writes_are_blocked(ctx):
    client = ctx.client
    _, headers = make_user(ctx.database, "alice@example.com")
    evil = {**headers, "Origin": "https://evil.example"}
    assert client.post("/api/items", json={"raw_text": "x"}, headers=evil).status_code == 403
    same = {**headers, "Origin": "http://testserver"}
    assert client.post("/api/items", json={"raw_text": "ok"}, headers=same).status_code == 200


# --- Google flow ------------------------------------------------------------


def _start(ctx, next_path=None):
    params = {"next": next_path} if next_path else {}
    response = ctx.client.get(
        "/api/auth/google/start", params=params, follow_redirects=False
    )
    query = parse_qs(urlparse(response.headers["location"]).query)
    return response, query


def _fake_google(ctx, monkeypatch, *, sub="g-1", email="new@example.com", verified=True):
    monkeypatch.setattr(
        ctx.google_oauth.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"access_token": "tok"}),
    )
    monkeypatch.setattr(
        ctx.google_oauth.requests,
        "get",
        lambda *a, **k: FakeResponse(
            200,
            {"sub": sub, "email": email, "email_verified": verified, "name": "New User"},
        ),
    )


def test_google_start_uses_state_and_pkce(ctx):
    response, query = _start(ctx)
    assert response.status_code == 302
    assert urlparse(response.headers["location"]).netloc == "accounts.google.com"
    assert query["client_id"] == ["test-client-id"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0] and query["code_challenge"][0]
    assert "life_inbox_oauth" in response.headers.get("set-cookie", "")


def test_google_login_creates_user_session_and_topics(ctx, monkeypatch):
    _, query = _start(ctx)
    _fake_google(ctx, monkeypatch)
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.status_code == 302 and done.headers["location"] == "/"
    me = ctx.client.get("/api/auth/me").json()
    assert me["email"] == "new@example.com"
    assert me["auth_provider"] == "google"
    assert ctx.client.get("/api/topics").json()


def test_google_login_rejects_wrong_state(ctx, monkeypatch):
    _start(ctx)
    _fake_google(ctx, monkeypatch)
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": "forged"},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=state"
    assert ctx.client.get("/api/auth/me").status_code == 401


def test_google_login_requires_verified_email(ctx, monkeypatch):
    _, query = _start(ctx)
    _fake_google(ctx, monkeypatch, verified=False)
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=email_unverified"
    assert ctx.client.get("/api/auth/me").status_code == 401


def test_google_login_respects_invite_list(ctx, monkeypatch):
    monkeypatch.setenv("LIFE_INBOX_ALLOWED_EMAILS", "friend@example.com")
    _, query = _start(ctx)
    _fake_google(ctx, monkeypatch, email="stranger@example.com")
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=not_allowed"
    assert ctx.client.get("/api/auth/me").status_code == 401


def test_google_login_denied_by_user(ctx):
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert done.headers["location"] == "/login?error=denied"


def test_google_login_blocks_open_redirects(ctx, monkeypatch):
    for bad in ("https://evil.example", "//evil.example", "/\\evil"):
        _, query = _start(ctx, next_path=bad)
        _fake_google(ctx, monkeypatch, sub=f"g-{bad}", email=f"u{abs(hash(bad))}@example.com")
        done = ctx.client.get(
            "/api/auth/google/callback",
            params={"code": "abc", "state": query["state"][0]},
            follow_redirects=False,
        )
        assert done.headers["location"] == "/", bad


def test_google_login_links_existing_account_with_same_email(ctx, monkeypatch):
    existing = ctx.database.ensure_user(
        email="kelly@example.com", display_name="Kelly", auth_provider="dev"
    )
    _, query = _start(ctx)
    _fake_google(ctx, monkeypatch, sub="g-kelly", email="kelly@example.com")
    ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert ctx.client.get("/api/auth/me").json()["id"] == existing["id"]


def test_google_login_replaces_previous_session(ctx, monkeypatch):
    _, old_headers = make_user(ctx.database, "old@example.com")
    ctx.client.cookies.set("life_inbox_session", old_headers["Cookie"].split("=", 1)[1])
    _, query = _start(ctx)
    _fake_google(ctx, monkeypatch, sub="g-new", email="new@example.com")
    ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert ctx.client.get("/api/auth/me", headers=old_headers).status_code == 401
    assert ctx.client.get("/api/auth/me").json()["email"] == "new@example.com"


# --- concurrency ------------------------------------------------------------


def test_concurrent_users_stay_isolated(ctx):
    users = [make_user(ctx.database, f"user{i}@example.com") for i in range(4)]

    def work(index):
        user, headers = users[index % len(users)]
        with TestClient(ctx.main.app) as client:
            created = [
                client.post(
                    "/api/items",
                    json={"raw_text": f"note {index}-{n} for user {user['id']}"},
                    headers=headers,
                ).json()
                for n in range(3)
            ]
            listed = client.get("/api/items", headers=headers).json()
        return user["id"], created, listed

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(8)))

    for user_id, created, listed in results:
        assert len(created) == 3
        assert all(item["owner_user_id"] == user_id for item in created)
        assert listed and all(item["owner_user_id"] == user_id for item in listed)


def test_only_one_analysis_runs_per_user_at_a_time(ctx):
    alice, _ = make_user(ctx.database, "alice@example.com")
    bob, _ = make_user(ctx.database, "bob@example.com")
    database = ctx.database
    try:
        database.set_current_owner_id(alice["id"])
        first = database.start_analysis_run()
        with pytest.raises(database.AnalysisAlreadyRunningError):
            database.start_analysis_run()
        database.set_current_owner_id(bob["id"])
        assert database.start_analysis_run()  # another user is unaffected
        database.set_current_owner_id(alice["id"])
        database.fail_analysis_run(first)
        assert database.start_analysis_run()  # allowed again after it ends
    finally:
        database.set_current_owner_id(None)


def test_automatic_analysis_stays_inside_each_users_inbox(ctx, monkeypatch):
    """The background analyzer runs off-request, so it must carry the owner."""
    monkeypatch.setenv("LIFE_INBOX_AUTO_ANALYZE", "1")
    client, main = ctx.client, ctx.main
    _, a = make_user(ctx.database, "alice@example.com")
    _, b = make_user(ctx.database, "bob@example.com")
    seen = []
    original = main.analyze_inbox_with_openai

    def recording(notes):
        seen.append(sorted(note["id"] for note in notes))
        return original(notes)

    monkeypatch.setattr(main, "analyze_inbox_with_openai", recording)

    def add(text, headers):
        response = client.post("/api/items", json={"raw_text": text}, headers=headers)
        main.auto_analyzer.wait_for_idle()
        return response.json()["id"]

    alice_note = add("Alice: renew passport tomorrow", a)
    bob_note = add("Bob: book the dentist", b)

    assert seen == [[alice_note], [bob_note]]

    def view_ids(view):
        return {note["id"] for bucket in view["buckets"] for note in bucket["notes"]}

    alice_view = client.get("/api/analyze/latest", headers=a).json()
    bob_view = client.get("/api/analyze/latest", headers=b).json()
    assert alice_view["analyzed_item_ids"] == [alice_note]
    assert bob_view["analyzed_item_ids"] == [bob_note]
    assert view_ids(alice_view) == {alice_note}
    assert view_ids(bob_view) == {bob_note}
    for headers in (a, b):
        assert client.get("/api/analyze/pending", headers=headers).json()["pending_count"] == 0


# --- sign-in options, startup checks, claiming local data ------------------


def test_auth_options_are_public_and_hide_dev_login(ctx):
    response = ctx.client.get("/api/auth/options")
    assert response.status_code == 200
    assert response.json() == {
        "google_enabled": True,
        "dev_login_enabled": False,
        "local_anonymous": False,
    }


def test_beta_refuses_to_start_when_misconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'beta.db'}")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_ENV", "beta")
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "dev")
    monkeypatch.delenv("LIFE_INBOX_SESSION_SECRET", raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    try:
        with pytest.raises(Exception) as excinfo:
            importlib.import_module("app.main")
        message = str(excinfo.value)
        assert type(excinfo.value).__name__ == "ConfigError"
        assert "PostgreSQL" in message
        assert "SESSION_SECRET" in message
        assert "must be 'google'" in message
    finally:
        database = sys.modules.get("app.database")
        if database is not None:
            database.engine.dispose()
        for name in MODULES:
            sys.modules.pop(name, None)


def test_local_owner_data_can_be_claimed_by_google_account(ctx):
    database = ctx.database
    local_id = database.get_or_create_default_owner_id()
    try:
        database.set_current_owner_id(local_id)
        item = database.add_item_to_db("Captured before Google sign-in")
        custom = database.create_topic_in_db("Side project")
        run_id = database.start_analysis_run()
        with database.SessionLocal() as session:
            local_work = session.scalar(
                database.select(database.TopicRecord).where(
                    database.TopicRecord.owner_user_id == local_id,
                    database.TopicRecord.name == "Work",
                )
            )
            session.add(
                database.ItemInsightRecord(
                    item_id=item["id"],
                    analysis_run_id=run_id,
                    category="Work",
                    concise_title="Captured",
                    priority="Low",
                    topic_id=local_work.id,
                    user_topic_id=custom["id"],
                    needs_review=0,
                    updated_at="2026-01-01T00:00:00+00:00",
                )
            )
            session.commit()
        database.fail_analysis_run(run_id)
    finally:
        database.set_current_owner_id(None)

    me = database.upsert_google_user(sub="g-123", email="me@gmail.com", name="Me")
    before = database.count_owned_data(local_id)
    assert before["inbox_items"] == 1
    assert before["topics"] == len(database.DEFAULT_TOPIC_NAMES) + 1

    moved = database.transfer_owned_data(local_id, me["id"])
    assert moved["inbox_items"] == 1
    assert moved["analysis_runs"] == 1
    assert moved["topics_moved"] == 1  # "Side project"
    assert moved["topics_merged"] == len(database.DEFAULT_TOPIC_NAMES)
    assert not any(database.count_owned_data(local_id).values())

    headers = {"Cookie": f"life_inbox_session={database.create_auth_session(me['id'])}"}
    items = ctx.client.get("/api/items", headers=headers).json()
    assert [i["id"] for i in items] == [item["id"]]
    topics = ctx.client.get("/api/topics", headers=headers).json()
    names = [t["name"] for t in topics]
    assert len(names) == len(set(names))
    assert "Side project" in names

    with database.SessionLocal() as session:
        insight = session.get(database.ItemInsightRecord, item["id"])
        topic = session.get(database.TopicRecord, insight.topic_id)
        assert topic.owner_user_id == me["id"] and topic.name == "Work"
        assert insight.user_topic_id == custom["id"]
        assert session.get(database.TopicRecord, custom["id"]).owner_user_id == me["id"]

    with pytest.raises(ValueError):
        database.transfer_owned_data(me["id"], me["id"])
