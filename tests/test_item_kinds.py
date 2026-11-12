"""Tests for smart templates (app/item_kinds.py)."""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.item_kinds import clean_card, fallback_card, guess_kind

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


def fake_ai(monkeypatch, reply):
    """Make the AI return `reply` and remember the prompts it was sent."""
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


RECIPE_REPLY = json.dumps(
    {
        "kind": "recipe",
        "fields": {
            "name": "Paneer tikka",
            "ingredients": ["200g paneer", "yogurt", "spices"],
            "steps": ["Marinate 1 hr", "Grill 15 min"],
            "calories": "lots",  # not an allowed field -> should be dropped
        },
    }
)


# --- the no-AI fallback ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Recipe: dal tadka, ingredients lentils, ghee, cook 20 min", "recipe"),
        ("Book recommendation: Sapiens by Harari, start reading", "book"),
        ("Met Rahul at the conference, his email rahul@x.com", "contact"),
        ("Cousin's wedding at Leela Palace, venue opens 7pm", "event"),
        ("Buy milk", "other"),
    ],
)
def test_guess_kind(text, kind):
    assert guess_kind(text) == kind


def test_fallback_contact_finds_phone_and_email():
    card = fallback_card("New contact Dr. Mehta, phone 98765 43210, mehta@clinic.in")

    assert card["kind"] == "contact"
    assert card["fields"] == {"phone": "98765 43210", "email": "mehta@clinic.in"}


# --- checking the AI's answer -----------------------------------------------------


def test_clean_card_drops_unknown_fields_and_wrong_types():
    card = clean_card(
        {"kind": "book", "fields": {"title": "Sapiens", "author": 42, "isbn": "123"}}
    )
    assert card == {"kind": "book", "fields": {"title": "Sapiens"}, "source": "ai"}


@pytest.mark.parametrize(
    "bad", [None, [], {"kind": "spaceship"}, {"kind": "book", "fields": "oops"}]
)
def test_clean_card_rejects_unusable_answers(bad):
    assert clean_card(bad) is None


# --- endpoints ----------------------------------------------------------------


def test_card_endpoint_without_ai_uses_fallback(client):
    item = client.post(
        "/api/items", json={"raw_text": "Recipe for chai: ingredients tea, milk"}
    ).json()

    response = client.post(f"/api/items/{item['id']}/card")

    assert response.status_code == 200
    assert response.json()["kind"] == "recipe"
    assert response.json()["kind_fields"] == {}


def test_card_endpoint_with_ai_saves_clean_fields(client, monkeypatch):
    item = client.post(
        "/api/items",
        json={"raw_text": "Paneer tikka: 200g paneer, yogurt, spices. Marinate, grill"},
    ).json()
    fake_ai(monkeypatch, RECIPE_REPLY)

    saved = client.post(f"/api/items/{item['id']}/card").json()

    assert saved["kind"] == "recipe"
    assert saved["kind_fields"]["ingredients"] == ["200g paneer", "yogurt", "spices"]
    assert "calories" not in saved["kind_fields"]
    # The card is stored, so a normal GET returns it too.
    assert client.get(f"/api/items/{item['id']}").json()["kind"] == "recipe"


def test_bad_ai_answer_falls_back(client, monkeypatch):
    item = client.post("/api/items", json={"raw_text": "Book: Sapiens"}).json()
    fake_ai(monkeypatch, "sorry, I cannot help")

    saved = client.post(f"/api/items/{item['id']}/card").json()

    assert saved["kind"] == "book"  # from the keyword fallback


def test_sensitive_item_card_never_calls_ai(client, monkeypatch):
    item = client.post(
        "/api/items", json={"raw_text": "Recipe from my therapist's diet plan"}
    ).json()
    client.post(f"/api/items/{item['id']}/sensitive", json={"sensitive": True})
    prompts = fake_ai(monkeypatch, RECIPE_REPLY)

    client.post(f"/api/items/{item['id']}/card")

    assert prompts == []


def test_list_cards_by_kind(client):
    recipe = client.post("/api/items", json={"raw_text": "Recipe: poha, cook 10 min"}).json()
    book = client.post("/api/items", json={"raw_text": "Book by Ruskin Bond"}).json()
    client.post("/api/items", json={"raw_text": "No card for me"})
    client.post(f"/api/items/{recipe['id']}/card")
    client.post(f"/api/items/{book['id']}/card")

    all_cards = client.get("/api/cards").json()
    recipes = client.get("/api/cards?kind=recipe").json()

    assert {c["id"] for c in all_cards} == {recipe["id"], book["id"]}
    assert [c["id"] for c in recipes] == [recipe["id"]]
    assert client.get("/api/cards?kind=spaceship").status_code == 400


def test_card_for_missing_item_is_404(client):
    assert client.post("/api/items/999/card").status_code == 404
