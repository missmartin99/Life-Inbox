import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db_path = tmp_path / "life_inbox_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    for module_name in (
        "app.main",
        "app.database",
        "app.auth",
        "app.config",
        "app.retrieval.ask",
    ):
        sys.modules.pop(module_name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    for module_name in (
        "app.main",
        "app.database",
        "app.auth",
        "app.config",
        "app.retrieval.ask",
    ):
        sys.modules.pop(module_name, None)


def _seed_atomic_habits(client):
    return client.post(
        "/api/items",
        json={
            "raw_text": (
                "Order Atomic Habits this weekend after Rohan's recommendation"
            )
        },
    ).json()


def _mock_ask_openai(monkeypatch, output_text=None, *, raise_on_create=None):
    import openai

    def create(**_kwargs):
        if raise_on_create is not None:
            raise raise_on_create
        return SimpleNamespace(output_text=output_text)

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(responses=SimpleNamespace(create=create)),
    )


def test_search_finds_keyword_matches(client):
    client.post("/api/items", json={"raw_text": "Renew car insurance before March 15"})
    client.post(
        "/api/items",
        json={"raw_text": "Try the Gurgaon restaurant Rohan recommended"},
    )

    hits = client.get("/api/search", params={"q": "car insurance"}).json()
    assert hits
    assert any("insurance" in hit["excerpt"].lower() for hit in hits)
    assert hits[0]["item_id"] > 0


def test_ask_returns_citations_for_known_question(client):
    created = _seed_atomic_habits(client)

    response = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["confidence"] != "insufficient"
    assert data["citations"]
    assert any(cite["item_id"] == created["id"] for cite in data["citations"])
    assert "Atomic Habits" in data["answer"]
    assert data["mode"] == "extractive_fallback"


def test_ask_admits_insufficient_evidence(client):
    client.post("/api/items", json={"raw_text": "Buy milk tomorrow"})

    data = client.post(
        "/api/ask",
        json={"question": "What is the capital of France?"},
    ).json()
    assert data["confidence"] == "insufficient"
    assert data["citations"] == []
    assert "enough" in data["answer"].lower()
    assert data["mode"] == "extractive_fallback"


def test_ask_llm_grounded_answer_with_citations(client, monkeypatch):
    created = _seed_atomic_habits(client)
    question = "What did Rohan recommend about Atomic Habits?"

    # Probe extractive path once (no key) to learn a real citation id.
    probe = client.post("/api/ask", json={"question": question}).json()
    assert probe["citations"]
    cite_id = probe["citations"][0]["id"]
    assert probe["citations"][0]["item_id"] == created["id"]

    _mock_ask_openai(
        monkeypatch,
        json.dumps(
            {
                "answer": (
                    "Rohan recommended ordering Atomic Habits this weekend."
                ),
                "confidence": "high",
                "citation_ids": [cite_id],
            }
        ),
    )

    data = client.post("/api/ask", json={"question": question}).json()
    assert data["mode"] == "llm_grounded"
    assert data["answer_kind"] == "model_generated"
    assert data["confidence"] == "high"
    assert "Rohan" in data["answer"] and "Atomic Habits" in data["answer"]
    assert data["citations"]
    assert all(c["item_id"] == created["id"] for c in data["citations"])
    assert data["citations"][0]["id"] == cite_id
    assert "OpenAI" not in data["answer"]
    assert "api" not in data["answer"].lower()


def test_ask_falls_back_without_openai_key(client):
    created = _seed_atomic_habits(client)
    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["mode"] == "extractive_fallback"
    assert data["answer_kind"] == "grounded_summary"
    assert any(c["item_id"] == created["id"] for c in data["citations"])
    assert "Atomic Habits" in data["answer"]


def test_ask_falls_back_on_openai_api_error(client, monkeypatch):
    created = _seed_atomic_habits(client)
    _mock_ask_openai(
        monkeypatch,
        raise_on_create=RuntimeError("OPENAI_API_KEY=super-secret"),
    )

    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["mode"] == "extractive_fallback"
    assert data["confidence"] != "insufficient"
    assert any(c["item_id"] == created["id"] for c in data["citations"])
    assert "super-secret" not in data["answer"]
    assert "OPENAI" not in data["answer"]
    assert "exception" not in data["answer"].lower()


def test_ask_falls_back_on_malformed_model_response(client, monkeypatch):
    created = _seed_atomic_habits(client)
    _mock_ask_openai(monkeypatch, "not valid JSON {{{")

    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["mode"] == "extractive_fallback"
    assert any(c["item_id"] == created["id"] for c in data["citations"])


def test_ask_falls_back_on_unvalidated_citation_ids(client, monkeypatch):
    created = _seed_atomic_habits(client)
    _mock_ask_openai(
        monkeypatch,
        json.dumps(
            {
                "answer": "Invented answer with a fake citation.",
                "confidence": "high",
                "citation_ids": ["item-999999-1"],
            }
        ),
    )

    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["mode"] == "extractive_fallback"
    assert all(c["item_id"] == created["id"] for c in data["citations"])
    assert not any(c["id"] == "item-999999-1" for c in data["citations"])


def test_ask_llm_insufficient_when_model_says_so(client, monkeypatch):
    _seed_atomic_habits(client)
    probe = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    cite_id = probe["citations"][0]["id"]

    _mock_ask_openai(
        monkeypatch,
        json.dumps(
            {
                "answer": (
                    "I do not have enough matching information in your "
                    "inbox to answer that."
                ),
                "confidence": "insufficient",
                "citation_ids": [cite_id],
            }
        ),
    )

    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["mode"] == "llm_grounded"
    assert data["confidence"] == "insufficient"
    assert data["citations"] == []


def test_ask_user_isolation_prevents_cross_account_citations(client, monkeypatch):
    alice_login = client.post(
        "/api/auth/dev-login",
        json={"email": "alice-ask@example.com", "display_name": "Alice"},
    )
    assert alice_login.status_code == 200
    alice = alice_login.json()

    alice_create = client.post(
        "/api/items",
        json={
            "raw_text": (
                "Alice secret: renew the purple yacht insurance before March"
            )
        },
    )
    assert alice_create.status_code == 200
    alice_item = alice_create.json()
    assert alice_item["owner_user_id"] == alice["id"]

    bob_login = client.post(
        "/api/auth/dev-login",
        json={"email": "bob-ask@example.com", "display_name": "Bob"},
    )
    assert bob_login.status_code == 200
    bob = bob_login.json()
    assert bob["id"] != alice["id"]

    bob_create = client.post(
        "/api/items",
        json={"raw_text": "Bob note about groceries and milk"},
    )
    assert bob_create.status_code == 200

    # Even with a mocked LLM, Bob must not retrieve or cite Alice's item.
    _mock_ask_openai(
        monkeypatch,
        json.dumps(
            {
                "answer": "Trying to cite Alice's yacht insurance.",
                "confidence": "high",
                "citation_ids": [f"item-{alice_item['id']}-1"],
            }
        ),
    )

    ask = client.post(
        "/api/ask",
        json={"question": "What about purple yacht insurance?"},
    )
    assert ask.status_code == 200
    data = ask.json()
    assert all(c["item_id"] != alice_item["id"] for c in data["citations"])
    assert "yacht" not in data["answer"].lower()
