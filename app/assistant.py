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


# --- reminders ---------------------------------------------------------------

_REMIND_RE = re.compile(
    r"^(?:remind me|remind us|yaad dila(?:na)?(?: do)?)\b[\s,:-]*"
    r"(?P<link>to|about|that|of)?\b[\s,:-]*",
    re.IGNORECASE,
)
_CLOCK_RE = re.compile(
    r"\b(?:at\s+|by\s+)?(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?![a-z])",
    re.IGNORECASE,
)
_O_CLOCK_RE = re.compile(r"\b(?:at\s+)(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\b(?!\s*(?:am|pm|a\.m|p\.m))", re.IGNORECASE)
_RELATIVE_RE = re.compile(
    r"\bin\s+(?P<n>\d{1,3}|an?|half an?)\s*(?P<unit>minutes?|mins?|hours?|hrs?)\b",
    re.IGNORECASE,
)
_DAY_RE = re.compile(
    r"\b(?P<day>today|tonight|tomorrow|day after tomorrow|"
    r"(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
    re.IGNORECASE,
)
_PRONOUNS = {"my": "your", "me": "you", "mine": "yours", "myself": "yourself", "i": "you", "i'm": "you're"}


def _clock(h: int, m: int, ap: Optional[str]) -> Optional[tuple[int, int]]:
    ap = (ap or "").lower().replace(".", "")
    if m > 59 or h > 23:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    return h, m


def _say_time(h: int, m: int) -> str:
    suffix = "AM" if h < 12 else "PM"
    hour = h % 12 or 12
    return f"{hour} {suffix}" if m == 0 else f"{hour}:{m:02d} {suffix}"


def _tidy_task(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,.-:")
    text = re.sub(r"^(?:to|about|that|of)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(?:at|by|on|in|for)$", "", text, flags=re.IGNORECASE)
    return text[:1].upper() + text[1:] if text else text


def parse_reminder(text: str, now=None) -> Optional[dict]:
    """Understand "remind me to wash my car at 10pm".

    Returns None when `text` is not a reminder. Otherwise a dict with:
      task      tidy task wording ("Wash my car")
      when      phrase the date parser understands ("today at 10 pm") or None
      iso       "YYYY-MM-DDTHH:MM" when a clock time is known, else None
      spoken    how Jin says it back ("to wash your car tonight at 10 PM")
    """
    from datetime import datetime, timedelta

    from app.attention.dates import now_in_configured_tz, today_in_configured_tz

    match = _REMIND_RE.match(text.strip())
    if not match:
        return None
    link = (match.group("link") or "").lower()
    rest = text.strip()[match.end():]
    now = now or now_in_configured_tz()
    today = now.date() if hasattr(now, "date") else today_in_configured_tz()

    clock: Optional[tuple[int, int]] = None
    ambiguous = False
    day_word: Optional[str] = None
    target_date = None

    rel = _RELATIVE_RE.search(rest)
    if rel:
        raw = rel.group("n").lower()
        n = 0.5 if raw.startswith("half") else 1 if raw in ("a", "an") else int(raw)
        delta = timedelta(hours=n) if rel.group("unit").lower().startswith(("h")) else timedelta(minutes=n)
        due = (now + delta).replace(second=0, microsecond=0)
        target_date, clock = due.date(), (due.hour, due.minute)
        rest = rest[: rel.start()] + " " + rest[rel.end():]
    else:
        c = _CLOCK_RE.search(rest) or _O_CLOCK_RE.search(rest)
        if c:
            ap = c.groupdict().get("ap")
            parsed = _clock(int(c.group("h")), int(c.group("m") or 0), ap)
            if parsed:
                clock = parsed
                ambiguous = not ap and 1 <= int(c.group("h")) <= 11
                rest = rest[: c.start()] + " " + rest[c.end():]
        d = _DAY_RE.search(rest)
        if d:
            day_word = d.group("day").lower()
            rest = rest[: d.start()] + " " + rest[d.end():]

    task = _tidy_task(rest)
    if not task:
        return None

    if ambiguous and clock and day_word in (None, "today", "tonight"):
        # "at 6" with no am/pm: take the next 6 o'clock that is still ahead.
        h, m = clock
        later = [(hh, m) for hh in (h, h + 12) if (hh, m) > (now.hour, now.minute)]
        if later:
            clock = later[0]
        else:
            clock = (h, m)
            day_word = day_word or "tomorrow"

    if target_date is None:
        if day_word in ("tomorrow",):
            target_date = today + timedelta(days=1)
        elif day_word == "day after tomorrow":
            target_date = today + timedelta(days=2)
        elif day_word in ("today", "tonight"):
            target_date = today
        elif day_word:  # weekday name
            names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            wanted = names.index(day_word.replace("next ", ""))
            ahead = (wanted - today.weekday()) % 7
            if ahead == 0 or day_word.startswith("next "):
                ahead = ahead or 7
            target_date = today + timedelta(days=ahead)
        elif clock:
            # Bare time: the next time that clock reads.
            target_date = today
            if (clock[0], clock[1]) <= (now.hour, now.minute):
                target_date = today + timedelta(days=1)

    if target_date is None:
        return {"task": task, "when": None, "iso": None, "spoken": _spoken(link, task, None, None, today)}

    if clock:
        h, m = clock
        label = _say_time(h, m)
        when = f"{target_date.isoformat()} at {label.lower()}"
        iso = f"{target_date.isoformat()}T{h:02d}:{m:02d}"
    else:
        when = target_date.isoformat()
        iso = None
    return {
        "task": task,
        "when": when,
        "iso": iso,
        "spoken": _spoken(link, task, target_date, clock, today, tonight=day_word == "tonight"),
    }


def _spoken(link, task, target_date, clock, today, tonight=False) -> str:
    from datetime import timedelta

    body = " ".join(_PRONOUNS.get(w.lower(), w) for w in task.split())
    body = body[:1].lower() + body[1:]
    when = ""
    if target_date is not None:
        if target_date == today:
            day = "tonight" if (tonight or (clock and clock[0] >= 19)) else "today"
        elif target_date == today + timedelta(days=1):
            day = "tomorrow"
        else:
            day = "on " + target_date.strftime("%A")
        when = f" {day}" + (f" at {_say_time(*clock)}" if clock else "")
    return f"{link or 'about'} {body}{when}".strip()


# --- how Jin talks -----------------------------------------------------------

import random  # noqa: E402

_SAVED = [
    "Got it, saved.",
    "Done. It's in your inbox.",
    "Okay, I've noted that down.",
    "Sure thing, saved it for you.",
]
_SAVED_HI = ["ठीक है, मैंने इसे सेव कर लिया है।", "हो गया, सेव कर दिया।", "ठीक है, नोट कर लिया।"]
_REMINDER_SET = [
    "Okay, I'll remind you {spoken}.",
    "Sure, I'll remind you {spoken}.",
    "Got it. I'll remind you {spoken}.",
    "Done! I'll remind you {spoken}.",
]
_REMINDER_NO_TIME = [
    "Okay, I've added that to your tasks. Tell me when you'd like the reminder?",
    "Noted! Want to tell me a time, so I can remind you?",
]
_WHAT_TO_SAVE = ["Sure, what should I save?", "Of course. What would you like me to note down?"]
_DIDNT_CATCH = ["Sorry, I didn't quite catch that. Could you say it again?", "Hmm, I missed that. One more time?"]
_NOTHING_FOUND = [
    "Hmm, I couldn't find anything about that in your notes.",
    "I looked, but I don't see anything about that in your inbox yet.",
    "Nothing on that in your notes, I'm afraid.",
]
_SAVE_FAILED = "Oops, I couldn't save that. Mind trying again?"
_ANSWER_FAILED = "Sorry, I had trouble looking that up. Can you try again?"


def pick(options: list[str], **kwargs) -> str:
    return random.choice(options).format(**kwargs)


def saved_reply(hindi: bool = False) -> str:
    return random.choice(_SAVED_HI if hindi else _SAVED)


_LEAD_RE = re.compile(r"^Based on your inbox item “[^”]*” \([^)]*\):\s*")


def humanize_answer(answer: dict) -> tuple[str, str]:
    """Return (reply, speech) that sound like a friend, not a search engine."""
    text = (answer.get("answer") or "").strip()
    if answer.get("confidence") == "insufficient" or not text:
        line = pick(_NOTHING_FOUND)
        return line, line
    if _LEAD_RE.match(text):
        lead = _LEAD_RE.sub("", text, count=1)
        lead = lead.split(" Also related:")[0].strip()
        intro = random.choice(["Here's what I found.", "So, from your notes:", "Okay, I found this."])
        return f"{intro} {lead}", f"{intro} {to_speech(lead, 300)}"
    return text, to_speech(text)


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
