"""Spoken task commands: "mark the meeting done", "snooze groceries to tomorrow".

Pure parsing and matching, no database imports, so it is easy to test.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

_STOP = {
    "the", "a", "an", "my", "task", "tasks", "reminder", "to", "of", "that",
    "it", "as", "please", "one", "about", "for", "on", "i", "me", "is", "was",
}
_DONE_PATTERNS = [
    re.compile(r"^(?:mark|set|tick|check)\s+(?:off\s+)?(?P<t>.+?)\s+(?:as\s+)?(?:done|complete|completed|finished)$", re.I),
    re.compile(r"^(?:mark|tick|check|cross)\s+off\s+(?P<t>.+)$", re.I),
    re.compile(r"^(?:i\s+)?(?:did|finished|completed|have done|have finished)\s+(?P<t>.+)$", re.I),
    re.compile(r"^(?:done with|finished with|complete)\s+(?P<t>.+)$", re.I),
    re.compile(r"^(?P<t>.+?)\s+(?:is|are)\s+(?:done|complete|completed|finished)$", re.I),
]
_SNOOZE = re.compile(
    r"^(?:snooze|postpone|push|delay|move)\s+(?P<t>.+?)(?:\s+(?:to|until|till|for|by)\s+(?P<when>.+))?$",
    re.I,
)
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_task_command(text: str) -> Optional[dict]:
    """Return {"action": "done"|"snooze", "target": str, "when": str} or None."""
    cleaned = " ".join((text or "").strip().rstrip(".!").split())
    cleaned = re.sub(r"^(?:hey |ok |okay |please |jin )+", "", cleaned, flags=re.I)
    if not cleaned or cleaned.endswith("?"):
        return None
    for pattern in _DONE_PATTERNS:
        match = pattern.match(cleaned)
        if match:
            return {"action": "done", "target": match.group("t"), "when": ""}
    match = _SNOOZE.match(cleaned)
    if match:
        return {
            "action": "snooze",
            "target": match.group("t"),
            "when": (match.group("when") or "").strip(),
        }
    return None


def snooze_date(when: str, today: date) -> Optional[date]:
    """Turn "tomorrow", "next week", "friday", "3 days" into a date."""
    w = (when or "").lower().strip()
    if not w or w in {"later", "tomorrow", "a day"}:
        return today + timedelta(days=1)
    if "next week" in w:
        return today + timedelta(days=7)
    if "tonight" in w or "today" in w:
        return today
    match = re.search(r"(\d+)\s+days?", w)
    if match:
        return today + timedelta(days=int(match.group(1)))
    for index, name in enumerate(_WEEKDAYS):
        if name in w:
            return today + timedelta(days=(index - today.weekday()) % 7 or 7)
    return None


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP}


def match_task(target: str, tasks: list[dict]) -> tuple[Optional[dict], list[dict]]:
    """Best open task for the spoken target. (task, []) or (None, candidates)."""
    wanted = _words(target)
    if not wanted:
        return None, []
    scored = []
    for task in tasks:
        overlap = len(wanted & _words(task["text"]))
        if overlap:
            scored.append((overlap / len(wanted), overlap, task))
    if not scored:
        return None, []
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best = scored[0]
    ties = [row for row in scored if row[:2] == best[:2]]
    if len(ties) > 1:
        return None, [row[2] for row in ties]
    return best[2], []
