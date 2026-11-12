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


def format_human_range(start_str, end_str):
    if not start_str and not end_str:
        return ""
    from datetime import datetime
    try:
        s = datetime.fromisoformat(start_str.split("T")[0]).strftime("%b %Y") if start_str else None
        e = datetime.fromisoformat(end_str.split("T")[0]).strftime("%b %Y") if end_str else None
        if s and e:
            return f"in {s}" if s == e else f"from {s} to {e}"
        if s:
            return f"since {s}"
        if e:
            return f"up to {e}"
    except Exception:
        return f"{start_str} to {end_str}"


def detect_entity_themes(items):
    texts = [(it.get("title") or it.get("raw_text") or "") for it in items]
    combined = " ".join(texts).lower()
    themes = []
    if any(k in combined for k in ["blood test", "clinic", "hospital", "doctor", "dr.", "prescription", "glucose", "health", "dental", "medicine"]):
        themes.append("medical diagnostic reports and health checkups")
    if any(k in combined for k in ["coffee", "lunch", "dinner", "brewery", "cafe", "restaurant", "recipe", "food", "dish"]):
        themes.append("coffee runs and dining logs")
    if any(k in combined for k in ["trip", "travel", "flight", "hotel", "visit", "fort", "packing", "vacation"]):
        themes.append("travel planning and trip itineraries")
    if any(k in combined for k in ["invoice", "payment", "tax", "bank", "receipt", "recharge", "salary", "expense"]):
        themes.append("expenses and financial records")
    if any(k in combined for k in ["meeting", "sync", "project", "code", "architecture", "interview", "design"]):
        themes.append("project discussions and work syncs")
    if not themes:
        themes.append("personal reflections and daily notes")
    return " and ".join(themes[:2])


def extract_clean_highlight(text: str) -> str:
    """Extract a complete, grammatical highlight sentence without cutoffs or ellipses."""
    line = text.strip().split("\n")[0].strip()
    protected = (
        line.replace("Dr. ", "Dr__ ")
        .replace("Mr. ", "Mr__ ")
        .replace("Ms. ", "Ms__ ")
        .replace("Mrs. ", "Mrs__ ")
    )
    m = re.split(r"\.\s+(?=[A-Z])", protected)
    sentence = (
        m[0]
        .replace("Dr__ ", "Dr. ")
        .replace("Mr__ ", "Mr. ")
        .replace("Ms__ ", "Ms. ")
        .replace("Mrs__ ", "Mrs. ")
    )
    sentence = re.sub(r"\.{2,}|…$", "", sentence).strip().rstrip(",- ")
    if len(sentence) > 160:
        match = re.search(r"^(.{60,150})[,;]", sentence)
        if match:
            sentence = match.group(1).strip()
        else:
            sentence = sentence[:150].rsplit(" ", 1)[0].rstrip(",- ")
    sentence = re.sub(r"[,;\-\s]+[A-Za-z]?$", "", sentence).strip()
    if not sentence.endswith((".", "!", "?")):
        sentence += "."
    return sentence


def fallback_entity_summary(
    entity_name: str,
    entity_type: str,
    items: list,
    start_date: str = None,
    end_date: str = None,
) -> str:
    """Rich, conversational second-brain intelligence dossier synthesized from notes and timeline."""
    count = len(items)
    if count == 0:
        if start_date or end_date:
            range_label = format_human_range(start_date, end_date)
            return f"You don't have any notes mentioning **{entity_name}** {range_label}."
        return f"You don't have any recorded notes mentioning **{entity_name}** yet."

    dates = [it.get("created_at") for it in items if it.get("created_at")]
    if dates:
        d_sorted = sorted(dates)
        time_span = format_human_range(d_sorted[0], d_sorted[-1])
        start_date_human = d_sorted[0][:10]
        end_date_human = d_sorted[-1][:10]
    else:
        time_span = ""
        start_date_human = "earlier"
        end_date_human = "recently"

    theme_desc = detect_entity_themes(items)

    # Gather 3 to 5 clean, non-duplicate concrete facts or quotes
    facts = []
    seen = set()
    for it in items:
        cand = (it.get("title") or it.get("raw_text") or "").strip()
        if not cand:
            continue
        clean_fact = extract_clean_highlight(cand)
        key = clean_fact[:45].lower()
        if key not in seen and len(clean_fact) >= 20:
            seen.add(key)
            facts.append(clean_fact)
        if len(facts) >= 4:
            break

    notes_word = "note" if count == 1 else "notes"
    if entity_type == "person":
        role_desc = f"**{entity_name}** is an important personal and professional contact referenced across **{count} {notes_word}** {time_span}. Your recorded logs primarily center around {theme_desc}."
    elif entity_type == "place":
        role_desc = f"**{entity_name}** is a featured location logged across **{count} {notes_word}** {time_span}. Your notes chronicle {theme_desc}."
    else:
        role_desc = f"**{entity_name}** is an active organization connected to **{count} {notes_word}** {time_span}, centered on {theme_desc}."

    bullet_lines = "\n".join(f"• {f}" for f in facts) if facts else f"• Continuous touchpoints logged {time_span}."

    return f"""{role_desc}

**Key Patterns & Recorded Highlights:**
{bullet_lines}

**Second-Brain Context & Timeline:**
Your records document continuous touchpoints from {start_date_human} to {end_date_human}. Across this timeframe, {entity_name} represents one of your primary anchors for {theme_desc}."""


def summarize_entity(
    entity_name: str,
    entity_type: str,
    items: list,
    start_date: str = None,
    end_date: str = None,
) -> str:
    """Synthesize a comprehensive, user-friendly second-brain executive dossier summary."""
    if not items:
        if start_date or end_date:
            range_label = format_human_range(start_date, end_date)
            return f"You don't have any notes mentioning **{entity_name}** {range_label}."
        return f"You don't have any recorded notes mentioning **{entity_name}** yet."

    has_sensitive = any(item.get("is_sensitive") for item in items)
    if has_sensitive or not ai_is_configured():
        return fallback_entity_summary(entity_name, entity_type, items, start_date, end_date)

    snippets = []
    for it in items[:15]:
        text = (it.get("extracted_text") or it.get("raw_text") or it.get("title") or "").strip()
        if text:
            created = (it.get("created_at") or "")[:10]
            snippets.append(f"- [{created}] {text[:220]}")

    if not snippets:
        return fallback_entity_summary(entity_name, entity_type, items, start_date, end_date)

    time_context = format_human_range(start_date, end_date)
    time_instruction = f"The user has filtered for notes {time_context}." if time_context else ""

    prompt = f"""You are the user's personal second-brain AI assistant. Write a detailed, warm, highly useful dossier briefing about {entity_name} ({entity_type}) based on the user's private notes below.

Entity: {entity_name} ({entity_type})
Total notes in view: {len(items)}
{time_instruction}

Notes mentioning this entity:
{chr(10).join(snippets[:10])}

Format the summary with markdown sections:
1. A conversational 2-sentence executive summary of who/what they are in the user's life and what domains they touch.
2. A bulleted section titled "**Key Patterns & Recorded Highlights:**" with 3-4 specific facts, decisions, prescriptions, venues, or topics extracted from the notes.
3. A concluding sentence titled "**Second-Brain Context & Timeline:**" explaining the time span and relevance to the user.

Ensure the summary is insightful, complete, friendly, and directly useful for the user."""
    try:
        reply = ask_ai(prompt, feature="entity_summary")
        if reply and len(reply.strip()) > 30:
            return reply.strip()
    except Exception:
        logger.warning(f"Entity summary failed for {entity_name}; using fallback", exc_info=True)

    return fallback_entity_summary(entity_name, entity_type, items, start_date, end_date)

