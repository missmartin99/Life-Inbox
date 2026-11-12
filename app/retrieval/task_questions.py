"""Answer "what do I have to do ..." questions straight from the task board.

Tasks live in their own table, not in the searchable note text, so a keyword
search over notes finds only the few notes that happen to share words with the
question and silently misses the rest. For task questions we list every
matching open task instead, with due dates and times.

This module imports nothing from the database at import time; the attention
board is loaded inside the function.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

_TOKEN = re.compile(r"[a-z0-9]+")

# A question is about tasks when it names them, or asks what there is to do.
_TASK_WORDS = {
    "task", "tasks", "todo", "todos", "due", "overdue", "upcoming",
    "deadline", "deadlines", "pending", "reminder", "reminders", "agenda",
    "schedule",
}
_TASK_PHRASES = re.compile(
    r"\b(?:have|need|got|must|should|supposed)\s+to\s+do\b"
    r"|\bwhat\s+(?:should|can|shall)\s+i\s+do\b"
    r"|\bcoming\s+up\b"
    r"|\bto[\s-]?dos?\s+list\b|\bto-dos?\b",
    re.I,
)

# Words that only say "list my tasks" or "when". Anything else in the question
# is treated as a topic the tasks are filtered by ("tasks about the dentist").
_FILLER = _TASK_WORDS | {
    "what", "whats", "all", "do", "does", "did", "i", "ive", "im", "id", "ill",
    "me", "my", "mine", "we", "have", "has", "had", "to", "the", "a", "an",
    "is", "are", "am", "of", "for", "on", "in", "at", "by", "any", "there",
    "list", "show", "give", "tell", "us", "and", "or", "should", "must",
    "need", "needs", "got", "can", "will", "shall", "please", "dont", "today",
    "tonight", "tomorrow", "now", "this", "next", "week", "coming", "up",
    "soon", "later", "open", "still", "left", "remaining", "things", "thing",
    "stuff", "anything", "everything", "that", "it", "which", "when",
    "supposed", "ahead", "right", "late", "missed", "past", "behind", "s",
}

_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}
_ALL_OPEN = "that are still open"


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("'", ""))


def is_task_question(question: str) -> bool:
    return bool(set(_tokens(question)) & _TASK_WORDS) or bool(
        _TASK_PHRASES.search(question)
    )


def _scope(question: str, today: date):
    """Return (label, predicate(due_date | None, bucket) -> bool)."""
    q = question.lower()
    if re.search(r"\b(overdue|late|missed|past due|behind)\b", q):
        return "that are overdue", lambda due, bucket: bucket == "overdue"
    if re.search(r"\btomorrow\b", q):
        tomorrow = today + timedelta(days=1)
        return "for tomorrow", lambda due, bucket: due == tomorrow
    if re.search(r"\b(today|tonight|right now)\b", q):
        return (
            "for today",
            lambda due, bucket: due is not None and due <= today,
        )
    if re.search(r"\bnext week\b", q):
        start, end = today + timedelta(days=7), today + timedelta(days=13)
        return (
            "for next week",
            lambda due, bucket: due is not None and start <= due <= end,
        )
    if re.search(r"\bthis week\b", q):
        end = today + timedelta(days=6)
        return (
            "for this week",
            lambda due, bucket: due is not None and due <= end,
        )
    if re.search(r"\b(upcoming|coming up|soon|later|ahead)\b", q):
        return (
            "coming up",
            lambda due, bucket: due is not None and due > today,
        )
    return _ALL_OPEN, lambda due, bucket: True


def _clock(hhmm: str) -> str:
    moment = datetime.strptime(hhmm, "%H:%M")
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {suffix}"


def _when(due: Optional[date], due_time: Optional[str], today: date) -> str:
    if due is None:
        return "no date"
    if due == today:
        word = "today"
    elif due == today + timedelta(days=1):
        word = "tomorrow"
    elif due == today - timedelta(days=1):
        word = "yesterday"
    else:
        word = f"{due:%a} {due.day} {due:%b} {due.year}"
    return f"{word} at {_clock(due_time)}" if due_time else word


def _flags(task: dict, due: Optional[date], today: date) -> str:
    bucket = task.get("attention_bucket")
    if bucket == "overdue" and due is not None:
        if due == today:
            return " · overdue, its time has passed"
        days = (today - due).days
        return f" · overdue by {days} day{'s' if days != 1 else ''}"
    if bucket == "snoozed" and task.get("snoozed_until"):
        return f" · snoozed until {task['snoozed_until']}"
    return ""


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _undated_note(count: int) -> str:
    verb = "has" if count == 1 else "have"
    return f"{_plural(count, 'task')} {verb} no date yet."


def _payload(question: str, answer: str, citations: list[dict]) -> dict:
    return {
        "question": question,
        "answer": answer,
        "confidence": "high",
        "citations": citations,
        "answer_kind": "grounded_summary",
        "mode": "task_board",
    }


def answer_task_question(question: str) -> Optional[dict]:
    """Build a complete task answer, or None when this is not a task question."""
    if not is_task_question(question):
        return None

    from app.database import get_attention_board

    board = get_attention_board()
    today = date.fromisoformat(board["reference_date"])
    label, in_scope = _scope(question, today)
    topic = [word for word in _tokens(question) if word not in _FILLER]

    tasks: list[dict] = []
    for bucket in ("overdue", "today", "upcoming", "unscheduled", "snoozed"):
        tasks.extend(board[bucket])

    def due_of(task: dict) -> Optional[date]:
        raw = task.get("normalized_due_on")
        return date.fromisoformat(raw) if raw else None

    selected = [
        task
        for task in tasks
        if in_scope(due_of(task), task.get("attention_bucket"))
    ]
    if topic:
        selected = [
            task
            for task in selected
            if any(word in task["text"].lower() for word in topic)
        ]
        if not selected:
            return None  # not really a task-board question; search the notes

    undated = (
        [task for task in tasks if due_of(task) is None]
        if label != _ALL_OPEN and not topic
        else []
    )

    if not selected:
        answer = (
            "You have no open tasks."
            if label == _ALL_OPEN
            else f"You have no open tasks {label}."
        )
        if undated:
            answer += " " + _undated_note(len(undated))
        return _payload(question, answer, [])

    selected.sort(
        key=lambda task: (
            due_of(task) or date.max,
            task.get("due_time") or "99:99",
            _PRIORITY_RANK.get(task.get("priority"), 9),
            task["id"],
        )
    )

    lines: list[str] = []
    citations: list[dict] = []
    for index, task in enumerate(selected, start=1):
        due = due_of(task)
        line = (
            f"{index}. {task['text']} — {_when(due, task.get('due_time'), today)}"
            f" · {task['priority']} priority{_flags(task, due, today)}"
        )
        lines.append(line)
        sources = task.get("source_item_ids") or [task["id"]]
        citations.append(
            {
                "id": f"task-{task['id']}",
                "item_id": sources[0],
                "item_type": "task",
                "title": task["text"][:120],
                "excerpt": line[:320],
                "source_kind": "task",
                "preview_url": None,
                "score": 1.0,
            }
        )

    overdue = sum(
        1 for task in selected if task.get("attention_bucket") == "overdue"
    )
    header = f"You have {_plural(len(selected), 'task')} {label}"
    if overdue and label != "that are overdue":
        header += f" ({overdue} overdue)"
    answer = header + ":\n" + "\n".join(lines)
    if undated:
        answer += "\n" + _undated_note(len(undated))
    return _payload(question, answer, citations)
