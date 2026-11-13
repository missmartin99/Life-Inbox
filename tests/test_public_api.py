"""Tests for API tokens, webhooks and the MCP server."""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import api_access

APP_MODULES = ("app.main", "app.database", "app.auth", "app.config", "app.retrieval.ask")


@pytest.fixture()
def main(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in APP_MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    yield main
    database.engine.dispose()
    for name in APP_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture()
def client(main):
    with TestClient(main.app) as test_client:
        yield test_client


# --- API tokens ------------------------------------------------------------------------


def test_token_is_shown_once_and_stored_hashed(client):
    created = client.post("/api/tokens", json={"name": "my script"}).json()

    assert created["token"].startswith("li_")
    listed = client.get("/api/tokens").json()
    assert listed == [
        {"id": created["id"], "name": "my script", "created_at": created["created_at"], "last_used_at": None}
    ]
    assert created["token"] not in json.dumps(listed)  # never shown again


def test_bearer_token_works_and_records_last_use(client):
    token = client.post("/api/tokens", json={"name": "phone"}).json()["token"]

    response = client.post(
        "/api/items",
        json={"raw_text": "Saved from a script"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert client.get("/api/tokens").json()[0]["last_used_at"] is not None


def test_wrong_or_revoked_token_is_rejected(client):
    created = client.post("/api/tokens", json={"name": "old"}).json()
    headers = {"Authorization": f"Bearer {created['token']}"}

    bad = client.get("/api/items", headers={"Authorization": "Bearer li_not_a_real_token"})
    assert bad.status_code == 401

    assert client.delete(f"/api/tokens/{created['id']}").status_code == 204
    assert client.get("/api/items", headers=headers).status_code == 401


def test_token_from_header():
    assert api_access.token_from_header("Bearer li_abc") == "li_abc"
    assert api_access.token_from_header("bearer li_abc") == "li_abc"
    assert api_access.token_from_header("Basic dXNlcg==") is None
    assert api_access.token_from_header("Bearer not-ours") is None
    assert api_access.token_from_header(None) is None


# --- webhooks ---------------------------------------------------------------------------


@pytest.fixture()
def sent(main, monkeypatch):
    """Capture outgoing webhook POSTs instead of really sending them."""
    calls = []
    monkeypatch.setattr(main, "validate_public_http_url", lambda url: url)
    monkeypatch.setattr(
        api_access.requests,
        "post",
        lambda url, data, headers, timeout: calls.append(
            SimpleNamespace(url=url, body=data, headers=headers)
        ),
    )
    return calls


def test_webhook_fires_on_new_item_with_valid_signature(client, sent):
    hook = client.post("/api/webhooks", json={"url": "https://example.com/hook"}).json()

    item = client.post("/api/items", json={"raw_text": "Hello webhook"}).json()

    assert len(sent) == 1
    assert sent[0].url == "https://example.com/hook"
    payload = json.loads(sent[0].body)
    assert payload["event"] == "item.created"
    assert payload["data"]["id"] == item["id"]
    # The receiver can check the signature with the secret it was given.
    expected = api_access.sign(hook["secret"], sent[0].body)
    assert sent[0].headers["X-LifeInbox-Signature"] == expected


def test_webhook_secret_is_not_listed_again(client, sent):
    client.post("/api/webhooks", json={"url": "https://example.com/hook"})

    listed = client.get("/api/webhooks").json()

    assert "secret" not in listed[0]


def test_broken_receiver_does_not_break_saving(client, main, monkeypatch):
    monkeypatch.setattr(main, "validate_public_http_url", lambda url: url)
    client.post("/api/webhooks", json={"url": "https://example.com/down"})

    def boom(*args, **kwargs):
        raise ConnectionError("receiver is down")

    monkeypatch.setattr(api_access.requests, "post", boom)

    assert client.post("/api/items", json={"raw_text": "Still saved"}).status_code == 200


def test_webhook_cannot_point_at_localhost(client):
    response = client.post("/api/webhooks", json={"url": "http://localhost:8000/admin"})
    assert response.status_code == 400


def test_unknown_webhook_event_rejected(client, sent):
    response = client.post(
        "/api/webhooks", json={"url": "https://example.com/hook", "event": "item.exploded"}
    )
    assert response.status_code == 400


def test_delete_webhook_stops_delivery(client, sent):
    hook = client.post("/api/webhooks", json={"url": "https://example.com/hook"}).json()
    assert client.delete(f"/api/webhooks/{hook['id']}").status_code == 204

    client.post("/api/items", json={"raw_text": "No one is listening"})

    assert sent == []


# --- MCP server ---------------------------------------------------------------------------


def test_mcp_tools_talk_to_the_api_with_a_token(main, monkeypatch):
    server = importlib.import_module("mcp_server.server")
    with TestClient(main.app) as setup:
        token = setup.post("/api/tokens", json={"name": "mcp"}).json()["token"]
    with TestClient(main.app, headers={"Authorization": f"Bearer {token}"}) as api:
        monkeypatch.setattr(server, "http", api)

        saved = server.add_note("Renew passport before December 20")
        hits = server.search_inbox("passport")
        answer = server.ask_inbox("When should I renew my passport?")
        attention = server.what_needs_attention()

    assert saved["saved"] is True
    assert hits[0]["item_id"] == saved["item_id"]
    assert answer["sources"][0]["item_id"] == saved["item_id"]
    assert isinstance(attention, dict)


def test_mcp_server_lists_four_tools():
    import anyio

    server = importlib.import_module("mcp_server.server")
    tools = anyio.run(server.mcp.list_tools)

    assert sorted(t.name for t in tools) == [
        "add_note",
        "ask_inbox",
        "search_inbox",
        "what_needs_attention",
    ]
