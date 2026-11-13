"""Checks for what people type on their profile page."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional

MAX_NAME_LENGTH = 80
EARLIEST_BIRTH_DATE = date(1900, 1, 1)


def clean_display_name(raw: str) -> str:
    """Trim and tidy a name; raise ValueError if it can't be used."""
    text = unicodedata.normalize("NFC", raw or "")
    if any(unicodedata.category(ch) in {"Cc", "Cf"} for ch in text):
        raise ValueError("Your name can't contain hidden or control characters.")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("Enter your name.")
    if len(text) > MAX_NAME_LENGTH:
        raise ValueError(f"Keep your name under {MAX_NAME_LENGTH} characters.")
    return text


def latest_birth_date() -> date:
    # "Today" somewhere on Earth: the furthest-ahead time zone is UTC+14, so
    # nobody is told their real birthday is in the future.
    return (datetime.now(timezone.utc) + timedelta(hours=14)).date()


def clean_date_of_birth(raw: Optional[str]) -> Optional[str]:
    """Return the date as YYYY-MM-DD, None to clear it, or raise ValueError."""
    text = (raw or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("Enter your date of birth as a full date.")
    try:
        born = date.fromisoformat(text)
    except ValueError as exc:  # e.g. 2001-02-29 or 2000-13-01
        raise ValueError("That date doesn't exist. Check the day and month.") from exc
    if born > latest_birth_date():
        raise ValueError("Your date of birth can't be in the future.")
    if born < EARLIEST_BIRTH_DATE:
        raise ValueError("Enter a date of birth after 1900.")
    return born.isoformat()
