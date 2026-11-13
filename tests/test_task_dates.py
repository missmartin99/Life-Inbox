"""Notes with a date become tasks (even without OpenAI) and reach the bell."""

import importlib
import sys
from datetime import date, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.attention.dates import find_timing_phrase, parse_timing_phrase

REF = date(2026, 11, 12)  # a Thursday


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("in a week", date(2026, 11, 19)),
        ("in 2 weeks", date(2026, 11, 26)),
        ("within two weeks", date(2026, 11, 26)),
        ("in a fortnight", date(2026, 11, 26)),
        ("in a month", date(2026, 12, 12)),
        ("in 3 months", date(2027, 2, 12)),
        ("in a couple of days", date(2026, 11, 14)),
        ("in one day", date(2026, 11, 13)),
        ("in 5 days", date(2026, 11, 17)),
    ],
)
def test_spans_of_time_are_understood(phrase, expected):
    parsed = parse_timing_phrase(phrase, reference=REF)
    assert parsed.due_date == expected
    assert parsed.confidence == "high"


def test_a_month_from_the_31st_lands_on_the_last_day():
    assert parse_timing_phrase("in a month", reference=date(2026, 1, 31)).due_date == date(2026, 2, 28)


def test_a_few_days_is_a_guess():
    parsed = parse_timing_phrase("in a few days", reference=REF)
    assert parsed.due_date == date(2026, 11, 15)
    assert parsed.confidence == "medium" and parsed.needs_confirmation


@pytest.mark.parametrize(
    "note, phrase",
    [
        ("submission 1 tomorrow", "tomorrow"),
        ("submission 2 in a week", "in a week"),
        ("submission 3 in a month", "in a month"),
        ("Meet Tom on 20 Nov", "on 20 Nov"),
        ("meeting tomorrow at 3pm", "tomorrow at 3pm"),
        ("pay rent by friday", "by friday"),
        ("exam on the 3rd", "on the 3rd"),
        ("Life Inbox sub", None),
        ("Call Tom about the car", None),  # "Tom" is a name, not "tomorrow"
        ("my 1st draft", None),
        ("", None),
    ],
)
def test_the_date_words_are_found_inside_a_note(note, phrase):
    assert find_timing_phrase(note, reference=REF) == phrase


def test_found_phrase_keeps_the_time_of_day():
    phrase = find_timing_phrase("dentist 21/11/2026 at 4:30 pm", reference=REF)
    parsed = parse_timing_phrase(phrase, reference=REF)
    assert parsed.due_date == date(2026, 11, 21) and parsed.due_time == time(16, 30)


def test_built_in_rules_turn_dated_notes_into_tasks():
    from app.organizer import fallback_analyze

    result = fallback_analyze(
        [
            {"id": 1, "text": "submission 1 tomorrow"},
            {"id": 2, "text": "submission 2 in a week"},
            {"id": 3, "text": "submission 3 in a month"},
            {"id": 4, "text": "Life Inbox sub"},
            {"id": 5, "text": "Met Rohan yesterday, nice chat"},
            {"id": 6, "text": "Ideas for the app: dark mode, export"},
            {"id": 7, "text": "Dentist on 20 Nov at 4pm"},
        ]
    )
    todos = {t["source_note_ids"][0]: t for t in result["todos"]}
    assert set(todos) == {1, 2, 3, 7}
    assert (todos[1]["when"], todos[1]["priority"]) == ("tomorrow", "High")
    assert (todos[2]["when"], todos[2]["priority"]) == ("in a week", "Medium")
    assert (todos[3]["when"], todos[3]["priority"]) == ("in a month", "Low")
    assert todos[7]["when"] == "on 20 Nov at 4pm"


# --- end to end: save a note, see it on the bell --------------------------------------

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.integrations.calendar",
    "app.integrations.google_calendar",
)


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dates.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY", "LIFE_INBOX_AUTO_ANALYZE", "LIFE_INBOX_TIMEZONE"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    from app.attention.dates import today_in_configured_tz

    with TestClient(main.app) as client:
        yield SimpleNamespace(client=client, main=main, database=database, today=today_in_configured_tz())
    main.auto_analyzer.wait_for_idle()
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def test_saved_notes_with_dates_show_up_on_the_bell(ctx):
    user = ctx.database.ensure_user(email="a@example.com", display_name="A", auth_provider="google")
    ctx.client.cookies.set("life_inbox_session", ctx.database.create_auth_session(user["id"]))
    for text in ("submission 1 tomorrow", "submission 2 in a week", "submission 3 in a month", "Life Inbox sub"):
        assert ctx.client.post("/api/items", json={"raw_text": text}).status_code == 200
    ctx.main.auto_analyzer.wait_for_idle()

    bell = ctx.client.get("/api/notifications").json()
    assert [(t["text"], t["priority"], t["due_on"]) for t in bell["tasks"]] == [
        ("submission 1 tomorrow", "High", (ctx.today + timedelta(days=1)).isoformat()),
        ("submission 2 in a week", "Medium", (ctx.today + timedelta(days=7)).isoformat()),
        ("submission 3 in a month", "Low", parse_timing_phrase("in a month").normalized_iso),
    ]
    assert bell["count"] == 3


def test_india_time_is_the_default(monkeypatch):
    from app.attention.dates import configured_timezone, format_deadline, format_timestamp

    monkeypatch.delenv("LIFE_INBOX_TIMEZONE", raising=False)
    assert str(configured_timezone()) == "Asia/Kolkata"
    assert format_deadline(date(2026, 11, 13)) == "Fri, 13 Nov 2026, 11:59 PM IST"
    assert format_deadline(date(2026, 11, 13), time(15, 0)) == "Fri, 13 Nov 2026, 3:00 PM IST"
    assert format_deadline(date(2026, 11, 13), time(0, 5)) == "Fri, 13 Nov 2026, 12:05 AM IST"
    # Stored timestamps are UTC; they are shown in India time.
    assert format_timestamp("2026-11-12T20:15:00+00:00") == "13 Nov 2026, 1:45 AM IST"


def test_notes_list_shows_priority_and_a_clear_deadline(ctx):
    user = ctx.database.ensure_user(email="a@example.com", display_name="A", auth_provider="google")
    ctx.client.cookies.set("life_inbox_session", ctx.database.create_auth_session(user["id"]))
    for text in (
        "submission 1 tomorrow",
        "Dentist tomorrow at 4:30 pm",
        "submission 2 in a week",
        "Life Inbox sub",
    ):
        assert ctx.client.post("/api/items", json={"raw_text": text}).status_code == 200
    ctx.main.auto_analyzer.wait_for_idle()

    notes = {n["raw_text"]: n for n in ctx.client.get("/api/items").json()}
    tomorrow = ctx.today + timedelta(days=1)
    day = f"{tomorrow.strftime('%a')}, {tomorrow.day} {tomorrow.strftime('%b %Y')}"

    first = notes["submission 1 tomorrow"]
    assert first["priority"] == "High" and first["task_status"] == "open"
    assert first["deadline"]["date"] == tomorrow.isoformat()
    assert first["deadline"]["time"] == "23:59" and first["deadline"]["all_day"] is True
    assert first["deadline"]["label"] == f"{day}, 11:59 PM IST"
    assert first["deadline"]["status"] == "upcoming"

    dentist = notes["Dentist tomorrow at 4:30 pm"]
    assert dentist["deadline"]["label"] == f"{day}, 4:30 PM IST"
    assert dentist["deadline"]["all_day"] is False

    assert notes["submission 2 in a week"]["priority"] == "Medium"
    plain = notes["Life Inbox sub"]
    assert plain["deadline"] is None and plain["task_status"] is None and plain["priority"]
    assert all(n["saved_label"].endswith(" IST") for n in notes.values())

    # Finishing the task marks the note done; the bell labels are in IST too.
    bell = ctx.client.get("/api/notifications").json()
    entry = next(t for t in bell["tasks"] if t["text"] == "Dentist tomorrow at 4:30 pm")
    assert entry["deadline_label"] == f"{day}, 4:30 PM IST" and entry["time_label"] == "4:30 PM IST"
    assert ctx.client.post(f"/api/tasks/{entry['id']}/complete").status_code == 200
    done = {n["raw_text"]: n for n in ctx.client.get("/api/items").json()}["Dentist tomorrow at 4:30 pm"]
    assert done["task_status"] == "done" and done["deadline"] is None
