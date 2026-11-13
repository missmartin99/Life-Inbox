"""Gnani transcription adapter tests (HTTP is mocked; ffmpeg is real)."""

from __future__ import annotations

import shutil
import struct
import wave
from io import BytesIO

import pytest

from app.capture import gnani_stt, voice_extract

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def _silent_wav(seconds: int, sample_rate: int = 8000) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(struct.pack("<h", 0) * sample_rate * seconds)
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GNANI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", raising=False)
    monkeypatch.delenv("GNANI_MODEL", raising=False)
    monkeypatch.delenv("GNANI_LANGUAGE", raising=False)


def test_long_audio_is_split_and_joined(monkeypatch):
    calls = []

    def fake_post(url, headers, files, data, timeout):
        calls.append({"url": url, "headers": headers, "data": dict(data)})
        return _FakeResponse(200, {"transcript": f"part{len(calls)}"})

    monkeypatch.setattr(gnani_stt.requests, "post", fake_post)
    text = gnani_stt.transcribe_with_gnani(
        _silent_wav(60), original_filename="note.wav"
    )

    assert len(calls) == 3  # 60 s at 25 s per segment
    assert text == "part1 part2 part3"
    assert calls[0]["url"] == gnani_stt.GNANI_STT_URL
    assert calls[0]["headers"] == {"X-API-Key-ID": "test-key"}
    assert calls[0]["data"]["model"] == "prisma-v3.0"
    assert calls[0]["data"]["language_code"] == "hi-IN,en-IN"
    assert "previous_text" not in calls[0]["data"]
    assert calls[1]["data"]["previous_text"] == "part1"


def test_v25_uses_single_language(monkeypatch):
    monkeypatch.setenv("GNANI_MODEL", "prisma-v2.5")
    seen = {}

    def fake_post(url, headers, files, data, timeout):
        seen.update(data)
        return _FakeResponse(200, {"transcript": "hello"})

    monkeypatch.setattr(gnani_stt.requests, "post", fake_post)
    gnani_stt.transcribe_with_gnani(_silent_wav(2), original_filename="a.wav")
    assert seen["language_code"] == "hi-IN"
    assert "previous_text" not in seen


def test_auth_error_is_friendly_and_hides_key(monkeypatch):
    monkeypatch.setattr(
        gnani_stt.requests,
        "post",
        lambda *a, **k: _FakeResponse(
            401, {"detail": {"error_code": "INVALID_API_KEY"}}
        ),
    )
    with pytest.raises(gnani_stt.GnaniError) as excinfo:
        gnani_stt.transcribe_with_gnani(_silent_wav(1), original_filename="a.wav")
    assert "test-key" not in str(excinfo.value)
    assert "API key" in str(excinfo.value)


def test_rate_limit_retries_once(monkeypatch):
    monkeypatch.setattr(gnani_stt.time, "sleep", lambda s: None)
    responses = [_FakeResponse(429, {}), _FakeResponse(200, {"transcript": "ok"})]
    monkeypatch.setattr(
        gnani_stt.requests, "post", lambda *a, **k: responses.pop(0)
    )
    assert (
        gnani_stt.transcribe_with_gnani(_silent_wav(1), original_filename="a.wav")
        == "ok"
    )


def test_transcribe_audio_reports_gnani_provider(monkeypatch):
    monkeypatch.setattr(
        gnani_stt.requests,
        "post",
        lambda *a, **k: _FakeResponse(200, {"transcript": "kal doodh lana hai"}),
    )
    result = voice_extract.transcribe_audio(
        _silent_wav(2), original_filename="n.wav", mime_type="audio/wav"
    )
    assert result["provider"] == "gnani"
    assert result["available"] is True
    assert result["text"] == "kal doodh lana hai"


def test_gnani_failure_without_openai_keeps_audio_with_note(monkeypatch):
    monkeypatch.setattr(
        gnani_stt.requests, "post", lambda *a, **k: _FakeResponse(402, {})
    )
    result = voice_extract.transcribe_audio(
        _silent_wav(2), original_filename="n.wav", mime_type="audio/wav"
    )
    assert result["text"] == ""
    assert result["available"] is False
    assert "credits" in result["note"]


def test_unreadable_audio_is_a_clean_error():
    with pytest.raises(gnani_stt.GnaniError):
        gnani_stt.transcribe_with_gnani(b"not audio", original_filename="x.webm")
