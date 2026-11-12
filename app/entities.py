"""People & entities: who and what your notes are about.

    "Call Dr. Mehta about the Goa trip, ask Priya too"
        -> people: Dr. Mehta, Priya   places: Goa

Each entity is saved once (one row per name) and linked to every item that
mentions it, so we can show a page like "everything about Dr. Mehta".

    entities              item_entities
    +----+-----------+    +---------+-----------+
    | id | name      |    | item_id | entity_id |    many-to-many:
    | 1  | Dr. Mehta |<---| 7       | 1         |    one item -> many entities
    | 2  | Goa       |<---| 7       | 2         |    one entity -> many items
    +----+-----------+    | 9       | 1         |
                          +---------+-----------+
"""

import logging
import re

from app.organizer import extract_json_object
from app.privacy import ai_is_configured, ask_ai

logger = logging.getLogger(__name__)

ENTITY_TYPES = ["person", "place", "organization"]
MAX_ENTITIES = 15

# Without AI we only look for people, using two simple clues:
#   "Dr. Mehta", "Mrs. Sharma"           (a title before a capitalised name)
#   "call Priya", "met Rahul", "with Anu" (a verb before a capitalised name)
TITLE_RE = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Prof)\.? [A-Z][a-z]+(?: [A-Z][a-z]+)?")
# (?i:...) makes only the verb case-insensitive ("Call" or "call"); the name
# itself must still start with a capital letter.
VERB_RE = re.compile(
    r"\b(?i:call|met|meet|ask|tell|email|text|with|from)\s+([A-Z][a-z]+)"
)
# Capitalised words that are not names, e.g. "Call Monday" or "with Google".
# Titles are here too: in "meet Dr. Mehta" the verb clue would grab just "Dr".
NOT_NAMES = {
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "Today", "Tomorrow", "Google", "Zoom", "The", "My", "Me",
    "Dr", "Mr", "Mrs", "Ms", "Prof",
}


def clean_name(name):
    """Tidy spacing so "  dr.   Mehta " and "Dr. Mehta" are stored once."""
    return " ".join(str(name).split())[:60]


def fallback_entities(text):
    found = TITLE_RE.findall(text) + VERB_RE.findall(text)
    people = []
    for name in found:
        name = clean_name(name)
        if name not in NOT_NAMES and name.lower() not in [p.lower() for p in people]:
            people.append(name)
    return [{"name": n, "type": "person"} for n in people[:MAX_ENTITIES]]


def clean_entities(data):
    """Check the AI's JSON; keep only well-formed, de-duplicated entities."""
    if not isinstance(data, dict) or not isinstance(data.get("entities"), list):
        return None
    result = []
    seen = set()
    for entity in data["entities"]:
        if not isinstance(entity, dict):
            continue
        name = clean_name(entity.get("name") or "")
        kind = entity.get("type")
        if len(name) < 2 or kind not in ENTITY_TYPES:
            continue
        key = (name.lower(), kind)
        if key in seen:
            continue
        seen.add(key)
        result.append({"name": name, "type": kind})
    return result[:MAX_ENTITIES]


def build_prompt(text):
    return f"""List the people, places and organizations mentioned in this note.

Return ONLY JSON like:
{{"entities": [{{"name": "Dr. Mehta", "type": "person"}},
              {{"name": "Goa", "type": "place"}}]}}

Rules:
- "type" must be one of: {', '.join(ENTITY_TYPES)}.
- Only include names written in the note. Do not guess or add new ones.
- Write each name the way it appears in the note.
- Return {{"entities": []}} if there are none.

Note:
{text[:3000]}
"""


def extract_entities(text, is_sensitive=False):
    """Return a list of {"name", "type"} for the given text."""
    if is_sensitive or not ai_is_configured():
        return fallback_entities(text)
    try:
        reply = ask_ai(build_prompt(text), feature="entities")
        entities = clean_entities(extract_json_object(reply))
        if entities is not None:
            return entities
    except Exception:
        logger.warning("Entity extraction failed; using fallback", exc_info=True)
    return fallback_entities(text)


def fallback_entity_summary(entity_name: str, entity_type: str, items: list) -> str:
    """Fast, local summary synthesized from timeline and recurring themes."""
    count = len(items)
    if count == 0:
        return f"No recorded notes mention {entity_name} yet."
    dates = [it.get("created_at")[:10] for it in items if it.get("created_at")]
    first_date = min(dates) if dates else "unknown date"
    latest_date = max(dates) if dates else "unknown date"

    item_types = sorted(set(it.get("item_type") for it in items if it.get("item_type")))
    type_str = ", ".join(item_types) if item_types else "notes"

    time_span = f"on {first_date}" if first_date == latest_date else f"between {first_date} and {latest_date}"

    sample = ""
    for it in items:
        snippet = (it.get("title") or it.get("raw_text") or "").strip()
        if snippet:
            sample = snippet[:70].strip() + ("…" if len(snippet) > 70 else "")
            break
    sample_text = f" Context includes: \"{sample}\"" if sample else ""
    return f"{entity_name} is a {entity_type} referenced across {count} {type_str} {time_span}.{sample_text}"


def summarize_entity(entity_name: str, entity_type: str, items: list) -> str:
    """Synthesize a concise executive dossier summary of an entity from its items."""
    if not items:
        return f"No recorded notes mention {entity_name} yet."

    has_sensitive = any(item.get("is_sensitive") for item in items)
    if has_sensitive or not ai_is_configured():
        return fallback_entity_summary(entity_name, entity_type, items)

    snippets = []
    for it in items[:15]:
        text = (it.get("extracted_text") or it.get("raw_text") or it.get("title") or "").strip()
        if text:
            created = (it.get("created_at") or "")[:10]
            snippets.append(f"- [{created}] {text[:200]}")

    if not snippets:
        return fallback_entity_summary(entity_name, entity_type, items)

    prompt = f"""Synthesize a concise 2-3 sentence executive dossier summary for the {entity_type} "{entity_name}" based on the user's notes below.

Entity: {entity_name} ({entity_type})
Total notes: {len(items)}

Notes mentioning this entity:
{chr(10).join(snippets[:10])}

Requirements:
- Summarize who/what this {entity_type} is in the context of the user's life (physician, close friend, client, frequent location, store, etc.).
- Highlight key recurring topics, commitments, or patterns.
- Keep it factual, objective, and under 60 words. No intro or greetings.
"""
    try:
        reply = ask_ai(prompt, feature="entity_summary")
        if reply and len(reply.strip()) > 15:
            return reply.strip()
    except Exception:
        logger.warning(f"Entity summary failed for {entity_name}; using fallback", exc_info=True)

    return fallback_entity_summary(entity_name, entity_type, items)

