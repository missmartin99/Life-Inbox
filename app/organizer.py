import json
import logging
import os
import re

from app.schemas import AnalysisResult


MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
logger = logging.getLogger(__name__)


def extract_json_object(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in model output")
    return json.loads(raw[start:end + 1])


def priority_for_text(text: str) -> str:
    lowered = text.lower()

    high_markers = [
        "today", "tomorrow", "urgent", "asap", "due", "before friday",
        "before thursday", "expires", "deadline"
    ]
    medium_markers = [
        "this week", "weekend", "next week", "follow up", "remember",
        "need to", "appointment", "renew"
    ]

    if any(x in lowered for x in high_markers):
        return "High"
    if any(x in lowered for x in medium_markers):
        return "Medium"
    return "Low"


def bucket_for_text(text: str) -> str:
    lowered = text.lower()

    rules = [
        ("Work", ["team", "meeting", "architecture", "recruiter", "finance", "interview", "document"]),
        ("Money & Admin", ["bill", "tax", "insurance", "domain", "property tax", "reimbursement", "ca "]),
        ("Learning", ["read", "watch", "lecture", "article", "learn", "vector database", "prompt injection"]),
        ("Shopping & Errands", ["buy", "groceries", "pick up", "order", "dry cleaning"]),
        ("Health", ["dentist", "workout", "health"]),
        ("Travel", ["trip", "travel", "hotel", "jaipur"]),
        ("Ideas", ["idea", "build", "app", "startup", "product"]),
        ("People", ["call", "ask", "rohan", "papa", "mom"]),
    ]

    for name, markers in rules:
        if any(marker in lowered for marker in markers):
            return name
    return "Personal / Reference"


def _tags_for_text(text: str) -> list[str]:
    lowered = text.lower()
    tags = []
    tag_rules = [
        ("urgent", ["today", "tomorrow", "urgent", "asap", "deadline"]),
        ("errand", ["buy", "pick up", "order", "groceries"]),
        ("follow-up", ["call", "ask", "follow up", "email"]),
        ("learning", ["read", "watch", "learn", "article"]),
        ("money", ["bill", "tax", "pay", "insurance"]),
    ]
    for tag, markers in tag_rules:
        if any(marker in lowered for marker in markers):
            tags.append(tag)
    return tags


def _extract_timing(text: str) -> str | None:
    """The words that say when ("tomorrow", "in a week", "by Friday at 5pm")."""
    from app.attention.dates import find_timing_phrase

    return find_timing_phrase(text)


def _upcoming_due(when: str | None):
    """The due date for a timing phrase, if it is today or later."""
    from app.attention.dates import parse_timing_phrase, today_in_configured_tz

    if not when:
        return None
    due = parse_timing_phrase(when).due_date
    if due is None or due < today_in_configured_tz():
        return None
    return due


_PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2}


def _priority_with_date(text: str, when: str | None) -> str:
    """Keyword priority, raised when the due date is close.

    Due today or tomorrow is High; due within a week is at least Medium.
    """
    from app.attention.dates import today_in_configured_tz

    priority = priority_for_text(text)
    due = _upcoming_due(when)
    if due is None:
        return priority
    days = (due - today_in_configured_tz()).days
    by_date = "High" if days <= 1 else "Medium" if days <= 7 else "Low"
    return max(priority, by_date, key=_PRIORITY_RANK.__getitem__)


ACTION_MARKERS = (
    "call", "buy", "send", "ask", "finish", "submit", "pay", "book",
    "pick up", "prepare", "renew", "follow up", "need to", "remember to",
    "research", "look into", "order",
    # deadlines people write as nouns ("submission tomorrow", "exam on Friday")
    "submission", "deadline", "assignment", "exam", "appointment",
)


def _split_action_tasks(text: str, note_id: int, priority: str) -> list[dict]:
    """One source may yield multiple durable tasks when sentences look actionable.

    A sentence is a task when it has an action word ("call", "pay",
    "submission") or names an upcoming date ("in a week", "on 20 Nov"):
    something with a date is something to do.
    """
    chunks = [
        part.strip()
        for part in re.split(r"[.!?\n]+", text)
        if part and part.strip()
    ]
    todos = []
    for chunk in chunks:
        when = _extract_timing(chunk) or _extract_timing(text)
        has_action = any(marker in chunk.lower() for marker in ACTION_MARKERS)
        has_own_date = _upcoming_due(_extract_timing(chunk)) is not None
        if has_action or has_own_date:
            todos.append(
                {
                    "task": chunk,
                    "priority": _priority_with_date(chunk, when),
                    "source_note_ids": [note_id],
                    "when": when,
                }
            )
    if not todos and any(marker in text.lower() for marker in ACTION_MARKERS):
        when = _extract_timing(text)
        todos.append(
            {
                "task": text,
                "priority": _priority_with_date(text, when),
                "source_note_ids": [note_id],
                "when": when,
            }
        )
    return todos


def fallback_analyze(notes: list[dict]) -> dict:
    """Deterministic fallback so the demo still works if the API is unavailable."""
    buckets = {}
    todos = []
    priority_view = {"High": [], "Medium": [], "Low": []}

    for note in notes:
        text = note["text"]
        bucket = bucket_for_text(text)
        priority = _priority_with_date(text, _extract_timing(text))
        title = text[:72] + ("..." if len(text) > 72 else "")
        tags = _tags_for_text(text)

        item = {
            "id": note["id"],
            "title": title,
            "priority": priority,
            "tags": tags,
            "rationale": (
                f"Matched topic “{bucket}” from keywords in the note."
            ),
        }

        buckets.setdefault(bucket, []).append(item)
        priority_view[priority].append({"id": note["id"], "title": title})
        todos.extend(_split_action_tasks(text, note["id"], priority))

    return {
        "buckets": [
            {"name": name, "notes": items}
            for name, items in buckets.items()
        ],
        "todos": todos,
        "priority_view": priority_view,
        "mode": "fallback_no_api_call",
    }


def validate_analysis_result(result: dict, notes: list[dict]) -> dict:
    validated = AnalysisResult.model_validate(result)
    validated.validate_for_item_ids({note["id"] for note in notes})
    return validated.model_dump()


def analyze_inbox_with_openai(notes: list[dict]) -> dict:
    if not notes:
        return validate_analysis_result(
            {
                "buckets": [],
                "todos": [],
                "priority_view": {"High": [], "Medium": [], "Low": []},
                "mode": "empty_inbox",
            },
            notes,
        )

    if not os.getenv("OPENAI_API_KEY"):
        return validate_analysis_result(fallback_analyze(notes), notes)

    try:
        from openai import OpenAI
        client = OpenAI()

        compact_notes = [{"id": n["id"], "text": n["text"]} for n in notes]

        prompt = f"""
You are the organization engine for a personal Life Inbox.

Analyze the notes listed below together and turn them into a practical dashboard.
These may be only the newest notes of a larger inbox; older notes are handled separately.

Return ONLY valid JSON in exactly this shape:

{{
  "buckets": [
    {{
      "name": "short useful category name",
      "notes": [
        {{
          "id": 1,
          "title": "concise title preserving the note's meaning",
          "priority": "High",
          "tags": ["follow-up"],
          "rationale": "short reason this note belongs in the bucket"
        }}
      ]
    }}
  ],
  "todos": [
    {{
      "task": "concrete action",
      "priority": "High",
      "source_note_ids": [1],
      "when": "timing mentioned in the note, or null"
    }}
  ],
  "priority_view": {{
    "High": [
      {{"id": 1, "title": "concise title"}}
    ],
    "Medium": [],
    "Low": []
  }}
}}

Rules:
1. Organize the listed notes together, not each note independently.
2. Every listed note must appear in exactly one bucket.
3. Prefer existing topic/bucket names when they fit: Work, Money & Admin,
   Learning, Shopping & Errands, Health, Travel, Ideas, People,
   Personal / Reference. Invent a new bucket only when genuinely clearer.
4. Priority must be one of High, Medium, Low.
5. High priority means explicit urgency, a near deadline, or a clearly time-sensitive obligation.
6. Medium means actionable but not immediately urgent.
7. Low means reference material, someday ideas, or non-urgent items.
8. Extract ToDos only when the note contains an explicit or strongly implied action.
9. A note can produce multiple ToDos if it genuinely contains multiple actions.
10. Do not invent dates, deadlines, people, facts, or actions.
11. Preserve relative timing exactly when present, for example "tomorrow" or "this weekend".
12. Keep titles and ToDos short and concrete. No motivational language, commentary, or prose outside the JSON.
13. priority_view should include every note once, grouped by priority.
14. Include a short rationale and a few useful tags for each note.

Saved notes:
{json.dumps(compact_notes, ensure_ascii=False)}
"""

        response = client.responses.create(
            model=MODEL,
            input=prompt,
            max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "8000")),
        )
        result = extract_json_object(response.output_text)
        result["mode"] = "openai"
        return validate_analysis_result(result, notes)

    except Exception:
        logger.exception("OpenAI analysis failed; using deterministic fallback")
        fallback = fallback_analyze(notes)
        fallback["mode"] = "fallback_after_api_error"
        return validate_analysis_result(fallback, notes)
