"""Date/deadline phrase normalization for attention views."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def configured_timezone() -> ZoneInfo:
    name = os.getenv("LIFE_INBOX_TIMEZONE", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def today_in_configured_tz() -> date:
    return datetime.now(configured_timezone()).date()


@dataclass
class ParsedTiming:
    original_phrase: str
    due_date: Optional[date]
    confidence: str  # high | medium | low | none
    needs_confirmation: bool
    normalized_iso: Optional[str]
    note: Optional[str] = None


_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MDY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})(?:,?\s*(20\d{2}))?\b",
    re.I,
)
_IN_DAYS = re.compile(r"\bin\s+(\d+)\s+days?\b", re.I)
_WEEKDAY = re.compile(
    r"\b(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.I,
)

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_timing_phrase(
    phrase: Optional[str],
    *,
    reference: Optional[date] = None,
) -> ParsedTiming:
    original = (phrase or "").strip()
    if not original:
        return ParsedTiming(
            original_phrase="",
            due_date=None,
            confidence="none",
            needs_confirmation=False,
            normalized_iso=None,
            note="No timing phrase.",
        )

    ref = reference or today_in_configured_tz()
    lower = original.lower()

    if "asap" in lower or "urgent" in lower:
        return ParsedTiming(
            original_phrase=original,
            due_date=ref,
            confidence="low",
            needs_confirmation=True,
            normalized_iso=ref.isoformat(),
            note="Urgent/ASAP treated as today but needs confirmation.",
        )

    if re.search(r"\btoday\b", lower):
        return ParsedTiming(
            original_phrase=original,
            due_date=ref,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=ref.isoformat(),
        )

    if re.search(r"\btomorrow\b", lower):
        due = ref + timedelta(days=1)
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=due.isoformat(),
        )

    if "this weekend" in lower or "weekend" in lower:
        # Next Saturday from ref (or today if Saturday).
        days_ahead = (5 - ref.weekday()) % 7
        due = ref + timedelta(days=days_ahead)
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium",
            needs_confirmation=True,
            normalized_iso=due.isoformat(),
            note="Weekend normalized to Saturday; confirm if needed.",
        )

    iso = _ISO.search(original)
    if iso:
        due = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=due.isoformat(),
        )

    in_days = _IN_DAYS.search(original)
    if in_days:
        due = ref + timedelta(days=int(in_days.group(1)))
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=due.isoformat(),
        )

    mdy = _MDY.search(original)
    if mdy:
        month = _MONTHS[mdy.group(1).lower()]
        day = int(mdy.group(2))
        year = int(mdy.group(3)) if mdy.group(3) else ref.year
        try:
            due = date(year, month, day)
        except ValueError:
            return ParsedTiming(
                original_phrase=original,
                due_date=None,
                confidence="low",
                needs_confirmation=True,
                normalized_iso=None,
                note="Could not parse calendar date.",
            )
        if mdy.group(3) is None and due < ref:
            due = date(year + 1, month, day)
        confidence = "high" if mdy.group(3) else "medium"
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence=confidence,
            needs_confirmation=confidence != "high",
            normalized_iso=due.isoformat(),
            note=None if confidence == "high" else "Year inferred; confirm.",
        )

    weekday = _WEEKDAY.search(original)
    if weekday:
        target = _WEEKDAYS[weekday.group(2).lower()]
        days_ahead = (target - ref.weekday()) % 7
        if days_ahead == 0 or weekday.group(1):
            days_ahead = 7 if days_ahead == 0 else days_ahead
            if weekday.group(1) and days_ahead < 7:
                days_ahead += 7
        due = ref + timedelta(days=days_ahead or 7)
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium",
            needs_confirmation=True,
            normalized_iso=due.isoformat(),
            note="Weekday timing is ambiguous; confirm the intended date.",
        )

    return ParsedTiming(
        original_phrase=original,
        due_date=None,
        confidence="low",
        needs_confirmation=True,
        normalized_iso=None,
        note="Could not confidently normalize this timing phrase.",
    )


def classify_attention_bucket(
    due: Optional[date],
    *,
    reference: Optional[date] = None,
    snoozed_until: Optional[date] = None,
) -> str:
    ref = reference or today_in_configured_tz()
    if snoozed_until and snoozed_until > ref:
        return "snoozed"
    if due is None:
        return "unscheduled"
    if due < ref:
        return "overdue"
    if due == ref:
        return "today"
    return "upcoming"
