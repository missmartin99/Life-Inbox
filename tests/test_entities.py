"""Tests for people & entities (app/entities.py)."""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.entities import clean_entities, fallback_entities

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


def fake_ai(monkeypatch, entities):
    """Make the AI answer with these entities; remember the prompts sent."""
    import openai

    prompts = []

    def create(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        message = SimpleNamespace(content=json.dumps({"entities": entities}))
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


def add(client, text):
    return client.post("/api/items", json={"raw_text": text}).json()


# --- without AI ---------------------------------------------------------------------


def test_fallback_finds_people_from_titles_and_verbs():
    found = fallback_entities("Call Priya, then meet Dr. Mehta with Rahul on Friday")

    assert [e["name"] for e in found] == ["Dr. Mehta", "Priya", "Rahul"]
    assert all(e["type"] == "person" for e in found)


def test_fallback_skips_days_and_lowercase_words():
    assert fallback_entities("call tomorrow, meet Monday, email the landlord") == []


# --- checking the AI's answer -------------------------------------------------------


def test_clean_entities_filters_bad_rows_and_duplicates():
    cleaned = clean_entities(
        {
            "entities": [
                {"name": "Goa", "type": "place"},
                {"name": "  goa ", "type": "place"},  # duplicate
                {"name": "Priya", "type": "friend"},  # unknown type
                {"name": "X", "type": "person"},  # too short
                "not a dict",
            ]
        }
    )
    assert cleaned == [{"name": "Goa", "type": "place"}]


def test_clean_entities_rejects_wrong_shape():
    assert clean_entities({"people": []}) is None


# --- endpoints and the "everything about X" page -------------------------------------


def test_entity_page_collects_every_item_that_mentions_it(client):
    first = add(client, "Call Dr. Mehta about the report")
    second = add(client, "Ask Dr. Mehta for the prescription")
    add(client, "Buy groceries")
    client.post(f"/api/items/{first['id']}/entities")
    client.post(f"/api/items/{second['id']}/entities")

    entities = client.get("/api/entities").json()
    assert entities == [{"id": 1, "name": "Dr. Mehta", "type": "person", "item_count": 2}]

    page = client.get("/api/entities/1").json()
    assert page["name"] == "Dr. Mehta"
    assert [item["id"] for item in page["items"]] == [second["id"], first["id"]]


def test_same_name_different_case_is_one_entity(client, monkeypatch):
    a = add(client, "Trip to Goa")
    b = add(client, "goa packing list")
    fake_ai(monkeypatch, [{"name": "Goa", "type": "place"}])
    client.post(f"/api/items/{a['id']}/entities")
    fake_ai(monkeypatch, [{"name": "goa", "type": "place"}])
    client.post(f"/api/items/{b['id']}/entities")

    places = client.get("/api/entities?type=place").json()
    assert len(places) == 1
    assert places[0]["item_count"] == 2


def test_rerunning_replaces_old_links(client, monkeypatch):
    item = add(client, "Lunch with Anu at Cafe Delhi Heights")
    fake_ai(monkeypatch, [{"name": "Anu", "type": "person"}])
    client.post(f"/api/items/{item['id']}/entities")
    fake_ai(
        monkeypatch,
        [{"name": "Anu", "type": "person"}, {"name": "Cafe Delhi Heights", "type": "place"}],
    )
    client.post(f"/api/items/{item['id']}/entities")

    names = sorted(e["name"] for e in client.get("/api/entities").json())
    assert names == ["Anu", "Cafe Delhi Heights"]
    assert all(e["item_count"] == 1 for e in client.get("/api/entities").json())


def test_deleting_an_item_removes_its_links(client):
    item = add(client, "Call Priya tonight")
    client.post(f"/api/items/{item['id']}/entities")

    client.delete(f"/api/items/{item['id']}")

    assert client.get("/api/entities").json() == []  # no items mention Priya now


def test_sensitive_item_never_goes_to_ai(client, monkeypatch):
    item = add(client, "Therapy session with Dr. Rao")
    client.post(f"/api/items/{item['id']}/sensitive", json={"sensitive": True})
    prompts = fake_ai(monkeypatch, [{"name": "Dr. Rao", "type": "person"}])

    saved = client.post(f"/api/items/{item['id']}/entities").json()

    assert prompts == []
    assert saved[0]["name"] == "Dr. Rao"  # found by the local fallback instead


def test_errors(client):
    assert client.post("/api/items/999/entities").status_code == 404
    assert client.get("/api/entities/999").status_code == 404
    assert client.get("/api/entities?type=planet").status_code == 400
    assert client.get("/api/entities/999/summary").status_code == 404


def test_entity_summary_and_time_filter(client):
    item1 = add(client, "Visited Dr. Mehta for annual general checkup")
    item2 = add(client, "Follow up call with Dr. Mehta regarding blood test")
    client.post(f"/api/items/{item1['id']}/entities")
    client.post(f"/api/items/{item2['id']}/entities")

    # Time filter test
    entities_30d = client.get("/api/entities?days=30").json()
    assert len(entities_30d) == 1
    assert entities_30d[0]["name"] == "Dr. Mehta"
    assert entities_30d[0]["item_count"] == 2

    # Summary test
    entity_id = entities_30d[0]["id"]
    res = client.get(f"/api/entities/{entity_id}/summary")
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Dr. Mehta"
    assert "Dr. Mehta" in data["summary"]
    assert "person" in data["summary"]

