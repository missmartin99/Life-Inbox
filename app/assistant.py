"""Voice assistant logic: intent routing, speech-friendly text, Gnani TTS.

Kept free of web/database imports so it is easy to test on its own.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GNANI_TTS_URL = "https://api.vachana.ai/api/v1/tts/inference"
TTS_TIMEOUT_SECONDS = 30
MAX_SPEECH_CHARS = 450

# --- intent routing ---------------------------------------------------------

# Longest first, so "note that" wins over "note".
_CAPTURE_PREFIXES = sorted(
    [
        "set a reminder to", "set a reminder for", "set a reminder", "set reminder",
        "reminder to", "reminder for", "reminder", "remind me to", "remind me",
        "remind", "note that", "note down", "note",
        "remember that", "remember to", "remember", "add a note",
        "add to my inbox", "add to inbox", "add", "save this", "save that",
        "save", "write down", "jot down", "capture",
        # Hinglish / Hindi
        "yaad dilana", "yaad dila", "yaad rakhna", "note kar",
        "याद दिलाना", "याद दिला", "याद रखना", "नोट करो", "नोट",
    ],
    key=len,
    reverse=True,
)
# Reminders keep their wording ("remind me to call mom" is the note itself).
_KEEP_WORDING = ("remind", "set a reminder", "set reminder", "yaad dila", "याद दिला")
_FILLER = {"hey", "hi", "ok", "okay", "please", "assistant", "hello"}
_QUESTION_WORDS = (
    "what", "when", "where", "who", "which", "how", "why", "do i", "did i",
    "is there", "are there", "can you", "could you", "show", "list", "tell me",
    "kya", "kab", "kahan", "kaun", "kitna", "kaise", "क्या", "कब", "कहाँ", "कौन",
)


def _strip_filler(text: str) -> str:
    words = text.strip().split()
    while words and words[0].lower().strip(",.!") in _FILLER:
        words.pop(0)
    return " ".join(words)


def _starts_with_word(text: str, prefix: str) -> bool:
    lowered = text.lower()
    if not lowered.startswith(prefix):
        return False
    rest = lowered[len(prefix):]
    return rest == "" or not rest[0].isalnum()


_POLITE_LEAD = re.compile(
    r"^(?:(?:jin|hey jin|ok jin)\b[\s,]*)?"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need|would like)\s+(?:you\s+)?to\s+"
    r"|i['’]d\s+like\s+(?:you\s+)?to\s+|simply\s+|just\s+)*",
    re.I,
)
_REMINDER_REQUEST = re.compile(
    r"^(?:(?:set|add|create|make)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?reminder\b"
    r"|remind\s+me\b|reminder\b)",
    re.I,
)


def _reminder_request(text: str) -> Optional[str]:
    """The reminder wording if the text is a request to set one, else None."""
    stripped = _POLITE_LEAD.sub("", text.strip()).lstrip(" ,:-")
    match = _REMINDER_REQUEST.match(stripped)
    if match:
        rest = stripped[match.end():].lstrip(" ,:-").lower()
        if rest.startswith(_QUESTION_WORDS):
            return None  # "remind me what Rohan said?" is a question
        return stripped.rstrip("?？ ").strip()
    return None


_GENERIC = {
    "remind", "reminder", "reminders", "set", "add", "note", "notes", "save",
    "about", "have", "what", "when", "which", "tell", "show", "list", "inbox",
    "tomorrow", "today", "tonight", "morning", "evening", "night", "next",
    "week", "the", "and", "for", "you", "can", "did", "does", "are", "was",
    "with", "that", "this", "from", "your", "any", "all", "get", "got",
}


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _GENERIC}


def is_on_topic(question: str, citations: list[dict]) -> bool:
    """True if some cited note shares a real topic word with the question.

    Matching only on words like "reminder", "10" or "pm" is not an answer.
    """
    wanted = content_words(question)
    if not wanted:
        return True
    for cite in citations:
        text = f"{cite.get('title', '')} {cite.get('excerpt', '')}"
        if wanted & content_words(text):
            return True
    return False


def classify_utterance(text: str) -> tuple[str, str]:
    """Return ("capture" | "ask", cleaned_text)."""
    reminder_text = _reminder_request(_strip_filler(text))
    if reminder_text:
        return "capture", reminder_text
    cleaned = _strip_filler(text)
    if not cleaned:
        return "ask", ""
    # Speech-to-text often adds a "?"; an explicit reminder is still a reminder.
    reminder = cleaned.lower().startswith(_KEEP_WORDING)
    if cleaned.endswith(("?", "？")) and not reminder:
        return "ask", cleaned
    for prefix in _CAPTURE_PREFIXES:
        if not _starts_with_word(cleaned, prefix):
            continue
        remainder = cleaned[len(prefix):].lstrip(" :,-.").strip()
        if remainder.lower().startswith(_QUESTION_WORDS):
            return "ask", cleaned  # "remember what Rohan said" is a question
        if prefix.startswith(_KEEP_WORDING):
            return "capture", cleaned.rstrip("?？ ").strip()
        return "capture", remainder
    return "ask", cleaned


def has_devanagari(text: str) -> bool:
    return any("\u0900" <= ch <= "\u097f" for ch in text)


# --- speech-friendly text ---------------------------------------------------


def to_speech(text: str, limit: int = MAX_SPEECH_CHARS) -> str:
    """Strip markup and citation markers; shorten at a sentence boundary."""
    cleaned = re.sub(r"\[[^\]]{1,12}\]", "", text or "")
    cleaned = re.sub(r"https?://\S+", "a link", cleaned)
    cleaned = re.sub(r"[*_`#>]+", "", cleaned)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) <= limit:
        return cleaned
    window = cleaned[:limit]
    best = max(window.rfind(sep) for sep in (". ", "! ", "? ", "। "))
    if best >= limit // 3:
        return window[: best + 1].strip()
    return window.rsplit(" ", 1)[0].strip() + "."


# --- Gnani text-to-speech ---------------------------------------------------


class SpeechError(Exception):
    """Text-to-speech could not be produced. `status` is an HTTP status."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def speech_configured() -> bool:
    return bool(os.getenv("GNANI_API_KEY", "").strip())


def _float_env(name: str, default: float) -> float:
    try:
        return min(1.15, max(0.85, float(os.getenv(name, "") or default)))
    except ValueError:
        return default


def synthesize_speech(text: str) -> tuple[bytes, str]:
    """Return (mp3_bytes, content_type) for the text using Gnani Timbre."""
    api_key = os.getenv("GNANI_API_KEY", "").strip()
    if not api_key:
        raise SpeechError("Voice replies are not configured.", status=501)
    spoken = to_speech(text)
    if not spoken:
        raise SpeechError("Nothing to say.", status=400)

    payload = {
        "text": spoken,
        "voice": os.getenv("GNANI_TTS_VOICE", "").strip() or "Kaveri",
        "model": os.getenv("GNANI_TTS_MODEL", "").strip() or "timbre-v2.5",
        "language": os.getenv("GNANI_TTS_LANGUAGE", "").strip() or "auto",
        "speed": _float_env("GNANI_TTS_SPEED", 1.0),
        "audio_config": {
            "sample_rate": 24000,
            "num_channels": 1,
            "sample_width": 2,
            "container": "mp3",
            "bitrate": "64k",
        },
    }
    try:
        response = requests.post(
            GNANI_TTS_URL,
            headers={"X-API-Key-ID": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=TTS_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Gnani TTS request error: %s", type(exc).__name__)
        raise SpeechError("Could not reach the voice service.") from exc

    if response.status_code == 200 and response.content:
        return response.content, "audio/mpeg"
    if response.status_code in (401, 403):
        raise SpeechError(
            "Gnani rejected the key for text-to-speech (check its scopes)."
        )
    if response.status_code == 402:
        raise SpeechError("Gnani credits are exhausted.")
    if response.status_code == 429:
        raise SpeechError("The voice service is busy. Try again in a moment.")
    raise SpeechError(f"Voice synthesis failed (HTTP {response.status_code}).")
