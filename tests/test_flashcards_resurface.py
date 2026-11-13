"""Tests for flashcards (Leitner boxes) and resurfacing old notes."""

import importlib
import json
import sys
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.attention import dates as attention_dates
from app.flashcards import clean_cards, fallback_cards, next_review
from app.resurface import forgotten, on_this_day

APP_MODULES = ("app.main", "app.database", "app.auth", "app.config", "app.retrieval.ask")
TODAY = date(2026, 11, 12)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    set_today(monkeypatch, TODAY)
    for name in APP_MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    for name in APP_MODULES:
        sys.modules.pop(name, None)


def set_today(monkeypatch, day):
    """Pretend today is `day` (lets a test move time forward)."""
    monkeypatch.setattr(attention_dates, "today_in_configured_tz", lambda: day)


# --- Leitner scheduling ------------------------------------------------------------


def test_remembered_moves_up_one_box():
    assert next_review(0, True, TODAY) == (1, date(2026, 11, 15))  # +3 days
    assert next_review(1, True, TODAY) == (2, date(2026, 11, 19))  # +7 days


def test_top_box_stays_at_top():
    assert next_review(4, True, TODAY) == (4, date(2026, 12, 12))  # +30 days


def test_forgotten_goes_back_to_box_zero():
    assert next_review(3, False, TODAY) == (0, date(2026, 11, 13))  # +1 day


# --- making cards ------------------------------------------------------------------


def test_fallback_hides_the_longest_word():
    cards = fallback_cards("Pay rent of 15000 to the landlord by the 5th.")
    assert cards == [{"question": "Pay rent of 15000 to the ____ by the 5th.", "answer": "landlord"}]


def test_fallback_makes_at_most_three_cards():
    text = "Alpha beta gamma. Delta epsilon. Kappa lambda. Sigma omega."
    assert len(fallback_cards(text)) == 3


def test_clean_cards_drops_empty_ones():
    cards = clean_cards({"cards": [{"question": "Q?", "answer": ""}, {"question": "Who?", "answer": "Anu"}]})
    assert cards == [{"question": "Who?", "answer": "Anu"}]
    assert clean_cards({"cards": []}) is None


# --- flashcard endpoints: a full review cycle ------------------------------------------


def test_review_cycle(client, monkeypatch):
    item = client.post(
        "/api/items", json={"raw_text": "The capital of Australia is Canberra."}
    ).json()
    cards = client.post(f"/api/items/{item['id']}/flashcards").json()
    card_id = cards[0]["id"]
    assert cards[0]["answer"] == "Australia"  # longest word was hidden
    assert [c["id"] for c in client.get("/api/flashcards/due").json()] == [card_id]

    # Remembered -> box 1, due in 3 days, so nothing is due today any more.
    reviewed = client.post(f"/api/flashcards/{card_id}/review", json={"remembered": True}).json()
    assert reviewed["box"] == 1
    assert reviewed["due_date"] == "2026-11-15"
    assert client.get("/api/flashcards/due").json() == []

    # Three days later it is due again; this time we forget it.
    set_today(monkeypatch, date(2026, 11, 15))
    assert len(client.get("/api/flashcards/due").json()) == 1
    reviewed = client.post(f"/api/flashcards/{card_id}/review", json={"remembered": False}).json()
    assert reviewed["box"] == 0
    assert reviewed["due_date"] == "2026-11-16"


def test_ai_cards_are_used_when_configured(client, monkeypatch):
    import openai

    reply = json.dumps({"cards": [{"question": "Capital of Australia?", "answer": "Canberra"}]})
    message = SimpleNamespace(content=reply)
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(choices=[SimpleNamespace(message=message)])
                )
            )
        ),
    )
    item = client.post("/api/items", json={"raw_text": "Canberra is the capital of Australia"}).json()

    cards = client.post(f"/api/items/{item['id']}/flashcards").json()

    assert cards[0]["question"] == "Capital of Australia?"


def test_deleting_item_deletes_its_cards(client):
    item = client.post("/api/items", json={"raw_text": "Remember the passport office address."}).json()
    client.post(f"/api/items/{item['id']}/flashcards")

    client.delete(f"/api/items/{item['id']}")

    assert client.get("/api/flashcards/due").json() == []


def test_flashcard_errors(client):
    assert client.post("/api/items/999/flashcards").status_code == 404
    assert client.post("/api/flashcards/999/review", json={"remembered": True}).status_code == 404
    short = client.post("/api/items", json={"raw_text": "ok"}).json()
    assert client.post(f"/api/items/{short['id']}/flashcards").status_code == 400


# --- resurfacing ---------------------------------------------------------------------


def note(item_id, saved):
    return {"id": item_id, "created_at": f"{saved}T09:00:00+00:00"}


def test_on_this_day():
    items = [
        note(1, "2026-11-05"),  # 1 week ago
        note(2, "2026-10-13"),  # 30 days ago
        note(3, "2025-11-12"),  # 1 year ago
        note(4, "2026-11-04"),  # 8 days ago -> not a milestone
    ]
    found = [(row["label"], row["item"]["id"]) for row in on_this_day(items, TODAY)]
    assert found == [("1 week ago", 1), ("1 month ago", 2), ("1 year ago", 3)]


def test_forgotten_only_old_notes_and_same_pick_all_day():
    items = [note(i, "2026-09-01") for i in range(1, 11)] + [note(99, "2026-11-10")]

    first = forgotten(items, TODAY)
    again = forgotten(items, TODAY)

    assert len(first) == 3
    assert 99 not in [item["id"] for item in first]  # only 2 days old
    assert first == again  # stable for the whole day


def test_resurface_endpoint(client):
    client.post("/api/items", json={"raw_text": "Saved today"})

    data = client.get("/api/resurface").json()

    assert data["date"] == "2026-11-12"
    assert data["on_this_day"] == []
    assert data["forgotten"] == []  # nothing is two weeks old yet
