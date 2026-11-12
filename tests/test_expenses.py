"""Tests for receipts and the expense tracker (app/expenses.py)."""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.expenses import expense_summary, find_amount, find_date
from app.item_kinds import clean_card, fallback_card

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
    import openai

    def create(**_kwargs):
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


# --- finding amounts and dates ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("Total: ₹1,250.50", 1250.50),
        ("Paid Rs. 300 for auto", 300.0),
        ("Subtotal 400 GST 72 Total 472", 472.0),
        ("INR 99 only", 99.0),
        ("Buy 2 kg rice", None),
    ],
)
def test_find_amount(text, amount):
    assert find_amount(text) == amount


@pytest.mark.parametrize(
    ("text", "date"),
    [
        ("Date: 05/11/2026", "2026-11-05"),  # day/month/year, Indian style
        ("on 5-11-2026", "2026-11-05"),
        ("2026-11-05 14:32", "2026-11-05"),
        ("31/02/2026", None),  # not a real date
        ("no date here", None),
    ],
)
def test_find_date(text, date):
    assert find_date(text) == date


# --- receipt cards -------------------------------------------------------------------


def test_fallback_receipt_card():
    card = fallback_card("Cafe Coffee Day receipt 05/11/2026 Total ₹480")

    assert card["kind"] == "receipt"
    assert card["fields"] == {"amount": 480.0, "date": "2026-11-05"}


def test_ai_amount_as_text_becomes_a_number():
    card = clean_card({"kind": "receipt", "fields": {"vendor": "DMart", "amount": "₹1,250"}})
    assert card["fields"] == {"vendor": "DMart", "amount": 1250.0}


def test_ai_amount_that_is_not_a_number_is_dropped():
    card = clean_card({"kind": "receipt", "fields": {"amount": "a lot"}})
    assert card["fields"] == {}


# --- adding up -------------------------------------------------------------------


def receipt(item_id, amount, date=None, category=None, created_at="2026-11-01T10:00:00"):
    fields = {"amount": amount}
    if date:
        fields["date"] = date
    if category:
        fields["category"] = category
    return {"id": item_id, "kind_fields": fields, "created_at": created_at}


def test_expense_summary_totals_by_month_and_category():
    items = [
        receipt(1, 500, "2026-10-28", "Food"),
        receipt(2, 1200, "2026-11-03", "Groceries"),
        receipt(3, 300, "2026-11-04", "Food"),
        receipt(4, 50, None, "Made-up category"),  # month from created_at
        {"id": 5, "kind_fields": {"vendor": "No amount"}, "created_at": "2026-11-01"},
    ]

    summary = expense_summary(items)

    assert summary["count"] == 4
    assert summary["total"] == 2050
    assert summary["by_month"] == {"2026-10": 500, "2026-11": 1550}
    assert summary["by_category"] == {"Food": 800, "Groceries": 1200, "Other": 50}


def test_expense_summary_for_one_month():
    items = [receipt(1, 500, "2026-10-28"), receipt(2, 1200, "2026-11-03")]

    summary = expense_summary(items, month="2026-11")

    assert summary["total"] == 1200
    assert [r["item_id"] for r in summary["receipts"]] == [2]


# --- the endpoint -------------------------------------------------------------------


def test_expenses_endpoint_end_to_end(client, monkeypatch):
    bill = client.post(
        "/api/items", json={"raw_text": "DMart bill 03/11/2026, total ₹1,200"}
    ).json()
    client.post("/api/items", json={"raw_text": "Just a note, not a receipt"})
    fake_ai(
        monkeypatch,
        json.dumps(
            {
                "kind": "receipt",
                "fields": {
                    "vendor": "DMart",
                    "amount": 1200,
                    "date": "2026-11-03",
                    "category": "Groceries",
                },
            }
        ),
    )
    client.post(f"/api/items/{bill['id']}/card")

    summary = client.get("/api/expenses").json()

    assert summary["total"] == 1200
    assert summary["by_category"] == {"Groceries": 1200}
    assert summary["receipts"][0]["vendor"] == "DMart"
    assert client.get("/api/expenses?month=2026-10").json()["total"] == 0
    assert client.get("/api/expenses?month=November").status_code == 422
