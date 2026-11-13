"""Tests for meaning-based search (app/retrieval/embeddings.py).

The real model is ~90 MB, so these tests use a tiny fake "embedder" that
knows a few topics. That is enough to test OUR code: caching, scoring,
falling back. (scripts/eval_search.py measures the real model.)
"""

import importlib
import sys

import pytest
from fastapi.testclient import TestClient

from app.retrieval import embeddings

APP_MODULES = ("app.main", "app.database", "app.auth", "app.config", "app.retrieval.ask")

# Each topic is one dimension of the fake vector.
TOPICS = [
    {"rent", "landlord", "owe", "money", "flat", "pay"},
    {"dentist", "teeth", "checkup", "clinic"},
    {"cake", "birthday", "party"},
]


def fake_embed(texts):
    fake_embed.calls.append(list(texts))
    vectors = []
    for text in texts:
        words = set(text.lower().replace(",", " ").split())
        vectors.append([float(len(words & topic)) for topic in TOPICS] + [0.01])
    return vectors


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_EMBEDDINGS", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake_embed.calls = []
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    for name in APP_MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    with TestClient(main.app) as test_client:
        test_client.post("/api/items", json={"raw_text": "Pay rent to the landlord by the 5th"})
        test_client.post("/api/items", json={"raw_text": "Dentist appointment at Smile clinic"})
        test_client.post("/api/items", json={"raw_text": "Order a birthday cake"})
        yield test_client

    database.engine.dispose()
    for name in APP_MODULES:
        sys.modules.pop(name, None)


def search(client, q):
    return client.get("/api/search", params={"q": q}).json()


# --- small helpers ---------------------------------------------------------------


def test_cosine():
    assert embeddings.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert embeddings.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert embeddings.cosine([0, 0], [1, 1]) == 0.0


@pytest.mark.parametrize(("raw", "scaled"), [(0.05, 0.0), (0.10, 0.0), (0.30, 0.5), (0.50, 1.0), (0.9, 1.0)])
def test_rescale(raw, scaled):
    assert embeddings.rescale(raw) == pytest.approx(scaled)


# --- search behaviour ----------------------------------------------------------------


def test_finds_note_with_no_shared_words(client):
    results = search(client, "money owed, flat")

    assert results[0]["item_id"] == 1  # the rent note
    assert results[0]["keyword_score"] == 0  # no word overlap...
    assert results[0]["meaning_score"] > 0  # ...but the meaning matched


def test_turned_off_means_keyword_only(client, monkeypatch):
    monkeypatch.setenv("LIFE_INBOX_EMBEDDINGS", "0")

    assert search(client, "money owed, flat") == []
    assert fake_embed.calls == []


def test_model_failure_falls_back_to_keywords(client, monkeypatch):
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: None)

    results = search(client, "dentist")

    assert results[0]["item_id"] == 2
    assert results[0]["meaning_score"] == 0


def test_passage_vectors_are_cached(client):
    search(client, "teeth checkup")
    search(client, "teeth checkup")

    # Call 1: the query; call 2: the 3 passages; call 3: the query again.
    # The passages were NOT embedded a second time.
    assert [len(call) for call in fake_embed.calls] == [1, 3, 1]


def test_edited_item_is_embedded_again(client):
    search(client, "teeth checkup")
    client.put("/api/items/3", json={"raw_text": "Order a birthday cake for the party"})
    fake_embed.calls.clear()

    search(client, "teeth checkup")

    # Only the changed note needed a new vector.
    assert [len(call) for call in fake_embed.calls] == [1, 1]
