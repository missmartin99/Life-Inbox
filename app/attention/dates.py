"""Date/deadline phrase normalization for attention views."""

from __future__ import annotations

import calendar
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "Asia/Kolkata"  # India time, unless LIFE_INBOX_TIMEZONE says otherwise


def configured_timezone() -> ZoneInfo:
    name = os.getenv("LIFE_INBOX_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


END_OF_DAY = time(23, 59)


def timezone_label() -> str:
    """Short name of the app's time zone, e.g. "IST"."""
    return now_in_configured_tz().tzname() or str(configured_timezone())


def format_clock(value: time) -> str:
    """15:00 -> "3:00 PM"."""
    hour = value.hour % 12 or 12
    return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"


def format_deadline(due: date, due_time: Optional[time] = None) -> str:
    """A full, unambiguous deadline: "Fri, 13 Nov 2026, 11:59 PM IST".

    Without a time of day a task is due by the end of that day.
    """
    clock = format_clock(due_time or END_OF_DAY)
    return f"{due.strftime('%a')}, {due.day} {due.strftime('%b %Y')}, {clock} {timezone_label()}"


def format_timestamp(value: Optional[str]) -> Optional[str]:
    """A stored UTC timestamp shown in the app's time zone: "12 Nov 2026, 4:59 PM IST"."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(configured_timezone())
    zone = local.tzname() or str(configured_timezone())
    return f"{local.day} {local.strftime('%b %Y')}, {format_clock(local.time())} {zone}"


def now_in_configured_tz() -> datetime:
    return datetime.now(configured_timezone())


def today_in_configured_tz() -> date:
    return now_in_configured_tz().date()


@dataclass
class ParsedTiming:
    original_phrase: str
    due_date: Optional[date]
    confidence: str  # high | medium | low | none
    needs_confirmation: bool
    normalized_iso: Optional[str]
    note: Optional[str] = None
    # Time of day when the phrase names one ("12:15 pm today"); else None.
    due_time: Optional[time] = None


_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_HOUR_ONLY = re.compile(r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)", re.I)


def parse_time_of_day(phrase: Optional[str]) -> Optional[time]:
    """Pull a clock time out of a phrase, or None when it is missing/ambiguous.

    "12:15 pm", "5pm", "17:30" and "noon" are understood. A bare "5:30" with
    no am/pm is ambiguous, so it counts as no time rather than guessing and
    marking a task overdue too early.
    """
    text = (phrase or "").strip().lower()
    if not text:
        return None
    if re.search(r"\bnoon\b", text):
        return time(12, 0)

    match = _CLOCK.search(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        meridiem = (match.group(3) or "").replace(".", "")
    else:
        match = _HOUR_ONLY.search(text)
        if not match:
            return None
        hour, minute = int(match.group(1)), 0
        meridiem = match.group(2).replace(".", "")

    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
    elif hour in (0, 12) or 13 <= hour <= 23:
        pass  # unambiguous on a 24-hour clock ("00:30" is just after midnight)
    else:
        return None
    return time(hour, minute)


_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_MDY = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\s+(\d{1,2})(?:,?\s*(20\d{2}))?\b",
    re.I,
)
_MONTH_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?"
)
# "21 November", "20th of November 2026"
_DMY = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\b(?:,?\s*(20\d{{2}}))?",
    re.I,
)
# "21/11/26", "21-11-2026", "21.11.2026" (day first, as is usual in India/UK)
_NUMERIC_DATE = re.compile(
    r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)"
)
# "the 30th", "20th of this month", "5th of next month"
_ORDINAL = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)\b(?:\s+of\s+(this|next|the)\s+month)?",
    re.I,
)
# "in 3 days", "in a week", "within two weeks", "in a month", "in a couple of days"
_IN_SPAN = re.compile(
    r"\b(?:in|within)\s+(\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"(?:a\s+)?couple\s+(?:of\s+)?|(?:a\s+)?few)\s*(day|week|fortnight|month)s?\b",
    re.I,
)
_SPAN_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
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


def _parse_date_phrase(
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

    if re.search(
        r"\b(today|tonight|this (?:morning|afternoon|evening)|end of (?:the )?day|eod)\b",
        lower,
    ):
        return ParsedTiming(
            original_phrase=original,
            due_date=ref,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=ref.isoformat(),
        )

    if re.search(r"\bday after tomorrow\b", lower):
        due = ref + timedelta(days=2)
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=due.isoformat(),
        )

    if re.search(r"\byesterday\b", lower):
        due = ref - timedelta(days=1)
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="high",
            needs_confirmation=False,
            normalized_iso=due.isoformat(),
        )

    if re.search(r"\b(?:tomorrow|tmrw|tmr|tom)\b", lower):
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

    numeric = _NUMERIC_DATE.search(original)
    if numeric:
        first, second = int(numeric.group(1)), int(numeric.group(2))
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        ambiguous = False
        if first > 12 >= second:
            day, month = first, second
        elif second > 12 >= first:
            day, month = second, first  # month/day/year
        else:
            day, month = first, second
            ambiguous = first != second and first <= 12 and second <= 12
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
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium" if ambiguous else "high",
            needs_confirmation=ambiguous,
            normalized_iso=due.isoformat(),
            note="Read as day/month/year; confirm." if ambiguous else None,
        )

    span = _IN_SPAN.search(original)
    if span:
        due, vague = _after_span(ref, span.group(1), span.group(2))
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium" if vague else "high",
            needs_confirmation=vague,
            normalized_iso=due.isoformat(),
            note="\u201cA few\u201d read as 3; confirm if needed." if vague else None,
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

    dmy = _DMY.search(original)
    if dmy:
        day = int(dmy.group(1))
        month = _MONTHS[dmy.group(2).lower()]
        year = int(dmy.group(3)) if dmy.group(3) else ref.year
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
        if dmy.group(3) is None and due < ref:
            due = date(year + 1, month, day)
        confidence = "high" if dmy.group(3) else "medium"
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence=confidence,
            needs_confirmation=confidence != "high",
            normalized_iso=due.isoformat(),
            note=None if confidence == "high" else "Year inferred; confirm.",
        )

    ordinal = _ORDINAL.search(original)
    if ordinal:
        day = int(ordinal.group(1))
        scope = (ordinal.group(2) or "").lower()
        if scope == "next":
            month_index = ref.year * 12 + ref.month  # 0-based month + 1
        elif scope in {"this", "the"}:
            month_index = ref.year * 12 + ref.month - 1
        else:
            # "the 30th": the next time that day comes around.
            month_index = ref.year * 12 + ref.month - 1
            if day < ref.day:
                month_index += 1
        year, month = divmod(month_index, 12)
        month += 1
        if 1 <= day <= calendar.monthrange(year, month)[1]:
            due = date(year, month, day)
            explicit = bool(scope)
            return ParsedTiming(
                original_phrase=original,
                due_date=due,
                confidence="high" if explicit else "medium",
                needs_confirmation=not explicit,
                normalized_iso=due.isoformat(),
                note=None if explicit else "Month inferred; confirm.",
            )
        return ParsedTiming(
            original_phrase=original,
            due_date=None,
            confidence="low",
            needs_confirmation=True,
            normalized_iso=None,
            note="That day does not exist in the inferred month.",
        )

    if re.search(r"\b(?:end of (?:the |this )?month|eom)\b", lower):
        due = date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium",
            needs_confirmation=True,
            normalized_iso=due.isoformat(),
            note="End of month normalized to the last day; confirm if needed.",
        )

    if re.search(r"\bnext week\b", lower):
        due = ref + timedelta(days=7 - ref.weekday())
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium",
            needs_confirmation=True,
            normalized_iso=due.isoformat(),
            note="Next week normalized to Monday; confirm if needed.",
        )

    if re.search(r"\b(?:this week|end of (?:the |this )?week)\b", lower):
        due = ref if ref.weekday() > 4 else ref + timedelta(days=4 - ref.weekday())
        return ParsedTiming(
            original_phrase=original,
            due_date=due,
            confidence="medium",
            needs_confirmation=True,
            normalized_iso=due.isoformat(),
            note="This week normalized to Friday; confirm if needed.",
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


def _after_span(ref: date, amount: str, unit: str) -> tuple[date, bool]:
    """ref + "3 days" / "a week" / "a month". Returns (date, is_vague)."""
    words = " ".join(amount.lower().split())
    vague = "few" in words
    if words.isdigit():
        count = int(words)
    elif "couple" in words:
        count = 2
    elif vague:
        count = 3
    else:
        count = _SPAN_NUMBERS[words]
    unit = unit.lower()
    if unit == "month":
        month_index = ref.year * 12 + ref.month - 1 + count
        year, month = divmod(month_index, 12)
        month += 1
        day = min(ref.day, calendar.monthrange(year, month)[1])  # 31 Jan + 1 month = 28/29 Feb
        return date(year, month, day), vague
    days = {"day": 1, "week": 7, "fortnight": 14}[unit] * count
    return ref + timedelta(days=days), vague


def parse_timing_phrase(
    phrase: Optional[str],
    *,
    reference: Optional[date] = None,
) -> ParsedTiming:
    parsed = _parse_date_phrase(phrase, reference=reference)
    if parsed.due_date is not None:
        parsed.due_time = parse_time_of_day(phrase)
    return parsed


def classify_attention_bucket(
    due: Optional[date],
    *,
    reference: Optional[date] = None,
    snoozed_until: Optional[date] = None,
    due_time: Optional[time] = None,
    now: Optional[datetime] = None,
) -> str:
    """Bucket a task as snoozed/unscheduled/overdue/today/upcoming.

    A task due today with a clock time becomes overdue once that time has
    passed. The time check uses ``now`` (or the real clock when no reference
    date was given).
    """
    ref = reference or today_in_configured_tz()
    if snoozed_until and snoozed_until > ref:
        return "snoozed"
    if due is None:
        return "unscheduled"
    if due < ref:
        return "overdue"
    if due == ref:
        current = now
        if current is None and reference is None:
            current = now_in_configured_tz()
        if (
            due_time is not None
            and current is not None
            and current.replace(tzinfo=None).time() > due_time
        ):
            return "overdue"
        return "today"
    return "upcoming"


# --- finding the date words inside a longer note -----------------------------------

_WEEKDAY_NAMES = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
_LEAD = r"(?:(?:due|by|on|before|until|till|for|from)\s+)?"
# Same rules as _parse_date_phrase, in the same order, but only the ones that
# are safe to read inside free text. Left out on purpose: "tom" (usually a
# name, not "tomorrow"), "asap"/"urgent" (no real date), "yesterday" (past),
# and a bare "1st" (often "my 1st draft").
_FINDERS = tuple(
    re.compile(_LEAD + core, re.I)
    for core in (
        r"\bday after tomorrow\b",
        r"\b(?:today|tonight|this (?:morning|afternoon|evening)|end of (?:the )?day|eod)\b",
        r"\b(?:tomorrow|tmrw|tmr)\b",
        r"\b(?:this )?weekend\b",
        _ISO.pattern,
        _NUMERIC_DATE.pattern,
        _IN_SPAN.pattern,
        _MDY.pattern,
        _DMY.pattern,
        r"\b(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\s+of\s+(?:this|next|the)\s+month\b",
        r"\bthe\s+\d{1,2}(?:st|nd|rd|th)\b",
        r"\b(?:end of (?:the |this )?month|eom)\b",
        r"\bnext week\b",
        r"\b(?:this week|end of (?:the |this )?week)\b",
        rf"\b(?:this\s+|next\s+)?(?:{_WEEKDAY_NAMES})\b",
    )
)
_TIME_FINDER = re.compile(
    r"(?:\bat\s+)?(?:\b\d{1,2}:\d{2}\s*(?:am|pm|a\.m\.|p\.m\.)?|\b\d{1,2}\s*(?:am|pm|a\.m\.|p\.m\.)|\bnoon\b)",
    re.I,
)


def find_timing_phrase(
    text: Optional[str], *, reference: Optional[date] = None
) -> Optional[str]:
    """The words in a note that say when, e.g. "in a week" or "by Friday at 5pm".

    Returns None when the note names no date. The phrase always parses (with
    parse_timing_phrase) to a real date, and keeps the time of day if the note
    gives one.
    """
    if not text or not text.strip():
        return None
    for finder in _FINDERS:
        for match in finder.finditer(text):
            phrase = " ".join(match.group(0).split())
            if parse_timing_phrase(phrase, reference=reference).due_date is None:
                continue
            if parse_time_of_day(phrase) is None:
                clock = _TIME_FINDER.search(text)
                if clock and parse_time_of_day(clock.group(0)) is not None:
                    phrase = f"{phrase} at {' '.join(clock.group(0).split()).removeprefix('at ').strip()}"
            return phrase
    return None
