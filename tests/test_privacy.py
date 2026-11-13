"""Tests for the privacy layer (app/privacy.py)."""

import importlib
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.privacy import redact, restore

APP_MODULES = ("app.main", "app.database", "app.auth", "app.config", "app.retrieval.ask")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in APP_MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    for name in APP_MODULES:
        sys.modules.pop(name, None)


def fake_ai(monkeypatch, reply="not valid JSON"):
    """Replace the OpenAI client with a fake that remembers every prompt."""
    import openai

    prompts = []

    def create(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        message = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    return prompts


# --- redact() and restore() ------------------------------------------------


def test_redact_hides_personal_details():
    text = (
        "Call Priya on +91 98765 43210 or mail priya@example.com. "
        "Card 4111 1111 1111 1111, Aadhaar 1234 5678 9012, PAN ABCDE1234F."
    )
    redacted, mapping = redact(text)

    for secret in ("98765", "priya@example.com", "4111", "1234 5678 9012", "ABCDE1234F"):
        assert secret not in redacted
    assert "[PHONE_1]" in redacted
    assert "[EMAIL_1]" in redacted
    assert "[CARD_1]" in redacted
    assert "[AADHAAR_1]" in redacted
    assert "[PAN_1]" in redacted
    assert len(mapping) == 5


def test_redact_leaves_normal_text_alone():
    text = "Buy 2 kg rice and pay rent of 15000 before March 5"
    assert redact(text) == (text, {})


def test_same_value_gets_same_placeholder_and_restores():
    text = "Ping 9876543210 today; if busy, 9876543210 again tomorrow"
    redacted, mapping = redact(text)

    assert redacted.count("[PHONE_1]") == 2
    assert restore(redacted, mapping) == text


# --- sensitive items ---------------------------------------------------------


def test_mark_item_sensitive_and_back(client):
    item = client.post("/api/items", json={"raw_text": "Therapy notes"}).json()
    assert item["is_sensitive"] is False

    marked = client.post(f"/api/items/{item['id']}/sensitive", json={"sensitive": True})
    assert marked.status_code == 200
    assert marked.json()["is_sensitive"] is True

    cleared = client.post(f"/api/items/{item['id']}/sensitive", json={"sensitive": False})
    assert cleared.json()["is_sensitive"] is False


def test_mark_missing_item_sensitive_returns_404(client):
    response = client.post("/api/items/999/sensitive", json={"sensitive": True})
    assert response.status_code == 404


def test_analyze_never_sends_sensitive_text_or_phone_numbers(client, monkeypatch):
    client.post("/api/items", json={"raw_text": "Call the plumber at 9876543210"})
    secret = client.post(
        "/api/items", json={"raw_text": "Blood test results came back positive"}
    ).json()
    client.post(f"/api/items/{secret['id']}/sensitive", json={"sensitive": True})
    prompts = fake_ai(monkeypatch)

    response = client.post("/api/analyze")

    assert response.status_code == 200
    assert len(prompts) == 1
    assert "Blood test" not in prompts[0]
    assert "9876543210" not in prompts[0]
    assert "[PHONE_1]" in prompts[0]


def test_ask_never_sends_sensitive_items(client, monkeypatch):
    client.post("/api/items", json={"raw_text": "Passport renewal appointment on Friday"})
    secret = client.post(
        "/api/items", json={"raw_text": "Passport number is stored in the locker"}
    ).json()
    client.post(f"/api/items/{secret['id']}/sensitive", json={"sensitive": True})
    prompts = fake_ai(monkeypatch)

    client.post("/api/ask", json={"question": "When is the passport appointment?"})

    assert prompts, "the AI should still be asked about the non-sensitive item"
    assert "locker" not in prompts[0]


# --- audit log and preview ----------------------------------------------------


def test_ai_calls_are_logged_without_prompt_text(client, monkeypatch):
    client.post("/api/items", json={"raw_text": "Email boss@work.com the report"})
    fake_ai(monkeypatch)
    client.post("/api/analyze")

    log = client.get("/api/privacy/ai-log").json()

    assert len(log) == 1
    assert log[0]["feature"] == "analyze"
    assert log[0]["status"] == "ok"
    assert log[0]["redactions"] == 1
    assert "boss@work.com" not in str(log)


def test_no_log_rows_when_ai_is_not_configured(client):
    client.post("/api/items", json={"raw_text": "Buy milk"})
    client.post("/api/analyze")  # uses the local fallback, no AI call

    assert client.get("/api/privacy/ai-log").json() == []


def test_redact_preview_endpoint(client):
    response = client.post(
        "/api/privacy/redact-preview", json={"text": "My number is 9876543210"}
    )

    assert response.json() == {
        "redacted_text": "My number is [PHONE_1]",
        "hidden_values": 1,
    }
