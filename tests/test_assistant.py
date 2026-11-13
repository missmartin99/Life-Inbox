"""Voice assistant: intent routing, speech text, TTS adapter, and API."""

import importlib
import sys

import pytest

from app import assistant

# --- intent routing (pure) --------------------------------------------------


@pytest.mark.parametrize(
    "spoken, intent, saved",
    [
        ("Remember to order Atomic Habits this weekend", "capture", "order Atomic Habits this weekend"),
        ("remind me to call mom tomorrow", "capture", "remind me to call mom tomorrow"),
        ("Note that Rohan recommended Gurgaon restaurant", "capture", "Rohan recommended Gurgaon restaurant"),
        ("hey, add buy milk", "capture", "buy milk"),
        ("save this: renew car insurance in March", "capture", "renew car insurance in March"),
        ("नोट करो कल दूध लाना है", "capture", "कल दूध लाना है"),
        ("What did Rohan recommend?", "ask", "What did Rohan recommend?"),
        ("what did I plan about car insurance", "ask", "what did I plan about car insurance"),
        ("Remember what Rohan said about the book", "ask", "Remember what Rohan said about the book"),
        ("show me my open tasks", "ask", "show me my open tasks"),
        ("notebook recommendations?", "ask", "notebook recommendations?"),
        ("address of the restaurant", "ask", "address of the restaurant"),
    ],
)
def test_classify_utterance(spoken, intent, saved):
    assert assistant.classify_utterance(spoken) == (intent, saved)


def test_classify_blank_is_ask_with_nothing():
    assert assistant.classify_utterance("   ") == ("ask", "")


def test_prefix_must_be_a_whole_word():
    # "notebook" and "address" must not be mistaken for "note" / "add".
    assert assistant.classify_utterance("notebook sale on friday")[0] == "ask"
    assert assistant.classify_utterance("address of the shop")[0] == "ask"


# --- speech text ------------------------------------------------------------


def test_to_speech_strips_markers_and_links():
    text = "You planned to renew it [1]. See **notes** at https://x.example/a"
    assert assistant.to_speech(text) == "You planned to renew it . See notes at a link"


def test_to_speech_shortens_at_a_sentence():
    text = ("First sentence here. " * 40).strip()
    spoken = assistant.to_speech(text, limit=100)
    assert len(spoken) <= 100 and spoken.endswith(".")


def test_has_devanagari():
    assert assistant.has_devanagari("नोट करो") and not assistant.has_devanagari("note")


# --- Gnani TTS adapter (HTTP mocked) -----------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, content=b"ID3audio"):
        self.status_code = status_code
        self.content = content


@pytest.fixture()
def tts_env(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    for name in ("GNANI_TTS_VOICE", "GNANI_TTS_MODEL", "GNANI_TTS_LANGUAGE", "GNANI_TTS_SPEED"):
        monkeypatch.delenv(name, raising=False)


def test_tts_request_shape(tts_env, monkeypatch):
    seen = {}

    def fake_post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return FakeResponse()

    monkeypatch.setattr(assistant.requests, "post", fake_post)
    audio, content_type = assistant.synthesize_speech("Hello [1] there")
    assert (audio, content_type) == (b"ID3audio", "audio/mpeg")
    assert seen["url"] == assistant.GNANI_TTS_URL
    assert seen["headers"]["X-API-Key-ID"] == "test-key"
    body = seen["body"]
    assert body["text"] == "Hello there"
    assert body["model"] == "timbre-v2.5" and body["language"] == "auto"
    assert body["audio_config"]["container"] == "mp3"


def test_tts_speed_is_clamped(tts_env, monkeypatch):
    monkeypatch.setenv("GNANI_TTS_SPEED", "3")
    seen = {}
    monkeypatch.setattr(
        assistant.requests, "post",
        lambda url, headers, json, timeout: seen.update(b=json) or FakeResponse(),
    )
    assistant.synthesize_speech("hi")
    assert seen["b"]["speed"] == 1.15


@pytest.mark.parametrize(
    "status, fragment",
    [(401, "rejected"), (402, "credits"), (429, "busy"), (500, "failed")],
)
def test_tts_errors_are_friendly(tts_env, monkeypatch, status, fragment):
    monkeypatch.setattr(
        assistant.requests, "post", lambda *a, **k: FakeResponse(status, b"")
    )
    with pytest.raises(assistant.SpeechError) as excinfo:
        assistant.synthesize_speech("hello")
    assert fragment in str(excinfo.value)
    assert "test-key" not in str(excinfo.value)


def test_tts_not_configured(monkeypatch):
    monkeypatch.delenv("GNANI_API_KEY", raising=False)
    with pytest.raises(assistant.SpeechError) as excinfo:
        assistant.synthesize_speech("hello")
    assert excinfo.value.status == 501


# --- API ---------------------------------------------------------------------

MODULES = (
    "app.main", "app.database", "app.auth", "app.config",
    "app.assistant", "app.assistant_routes", "app.integrations.calendar",
)


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    for name in ("OPENAI_API_KEY", "GNANI_API_KEY", "LIFE_INBOX_AUTH_MODE", "LIFE_INBOX_ENV"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    routes = importlib.import_module("app.assistant_routes")
    with TestClient(main.app) as client:
        yield client, routes
    importlib.import_module("app.database").engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def test_page_loads_and_tells_ui_if_voice_replies_exist(api):
    client, _ = api
    page = client.get("/assistant")
    assert page.status_code == 200
    assert "const VOICE_REPLIES = false;" in page.text


def test_spoken_note_is_saved_to_the_inbox(api):
    client, _ = api
    body = client.post(
        "/api/assistant/voice",
        data={"text": "Remember to order Atomic Habits this weekend"},
    ).json()
    assert body["intent"] == "capture" and body["saved_item_id"]
    items = client.get("/api/items").json()
    assert any("Atomic Habits" in i["raw_text"] for i in items)


def test_question_is_answered_from_saved_items_with_sources(api):
    client, _ = api
    saved = client.post(
        "/api/items", json={"raw_text": "Rohan recommended the book Atomic Habits."}
    ).json()
    body = client.post(
        "/api/assistant/voice", data={"text": "What did Rohan recommend?"}
    ).json()
    assert body["intent"] == "ask"
    assert body["reply"] and body["speech"]
    assert saved["id"] in [c["item_id"] for c in body["citations"]]


def test_audio_is_transcribed_then_handled(api, monkeypatch):
    client, routes = api
    monkeypatch.setattr(
        routes, "transcribe_audio",
        lambda data, original_filename, mime_type: {"text": "note buy milk", "available": True},
    )
    wav = b"RIFF" + b"\x00" * 60 + b"WAVE" + b"\x00" * 200
    body = client.post(
        "/api/assistant/voice", files={"file": ("voice.wav", wav, "audio/wav")}
    ).json()
    assert body["transcript"] == "note buy milk"
    assert body["intent"] == "capture"
    assert any(i["raw_text"] == "buy milk" for i in client.get("/api/items").json())


def test_silent_recording_returns_a_helpful_error(api, monkeypatch):
    client, routes = api
    monkeypatch.setattr(
        routes, "transcribe_audio",
        lambda data, original_filename, mime_type: {"text": "", "note": "No speech detected."},
    )
    wav = b"RIFF" + b"\x00" * 60 + b"WAVE" + b"\x00" * 200
    response = client.post(
        "/api/assistant/voice", files={"file": ("voice.wav", wav, "audio/wav")}
    )
    assert response.status_code == 422
    assert "No speech" in response.json()["detail"]


def test_empty_request_is_rejected(api):
    client, _ = api
    assert client.post("/api/assistant/voice").status_code == 400


def test_speak_returns_audio_or_501(api, monkeypatch):
    client, _ = api
    assert client.post("/api/assistant/speak", json={"text": "hello"}).status_code == 501
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.setattr(
        assistant.requests, "post", lambda *a, **k: FakeResponse(200, b"ID3xyz")
    )
    response = client.post("/api/assistant/speak", json={"text": "hello"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"ID3xyz"


# --- reminders and Jin's manner ----------------------------------------------


@pytest.mark.parametrize(
    "spoken",
    [
        "Jin, remind me about washing my car at 10pm",
        "Hey Jin can you remind me about washing my car at 10 PM?",
    ],
)
def test_jin_name_and_politeness_do_not_turn_a_reminder_into_a_question(spoken):
    intent, cleaned = assistant.classify_utterance(spoken)
    assert intent == "capture"
    assert cleaned.lower().startswith("remind me about washing my car")


def test_parse_reminder_understands_time_and_task():
    from datetime import datetime

    now = datetime(2026, 11, 13, 13, 0)
    r = assistant.parse_reminder("remind me about washing my car at 10pm", now=now)
    assert r["task"] == "Washing my car"
    assert r["iso"] == "2026-11-13T22:00"
    assert r["spoken"] == "about washing your car tonight at 10 PM"
    # A time already gone today means tomorrow.
    r = assistant.parse_reminder("remind me to call mom at 9am", now=now)
    assert r["iso"] == "2026-11-14T09:00"
    # No am/pm: the next occurrence of that hour.
    assert assistant.parse_reminder("remind me to eat at 6", now=now)["iso"] == "2026-11-13T18:00"
    assert assistant.parse_reminder("remind me to buy milk", now=now)["when"] is None
    assert assistant.parse_reminder("buy milk") is None


def test_voice_reminder_creates_a_timed_task(api):
    client, _ = api
    body = client.post(
        "/api/assistant/voice",
        data={"text": "Jin, remind me to wash my car at 10pm tomorrow"},
    ).json()
    assert body["intent"] == "reminder" and body["task_id"]
    assert "wash your car" in body["reply"]
    tasks = client.get("/api/tasks").json()
    task = next(t for t in tasks if t["id"] == body["task_id"])
    assert task["text"] == "Wash my car"
    assert "10 pm" in task["timing_text"]
    assert task["status"] == "open"


def test_no_match_sounds_human(api):
    client, _ = api
    body = client.post(
        "/api/assistant/voice", data={"text": "what did the zebra say about quantum socks?"}
    ).json()
    assert "inbox to answer that" not in body["reply"]
    assert body["reply"] == body["speech"]


def test_extractive_answer_drops_the_robotic_lead_in():
    reply, speech = assistant.humanize_answer({
        "answer": "Based on your inbox item “Item 4” (original text): Rohan likes tea. "
                  "Also related: “Item 5” — more",
        "confidence": "medium",
    })
    assert "Based on your inbox item" not in reply + speech
    assert "Rohan likes tea." in speech and "Also related" not in reply
