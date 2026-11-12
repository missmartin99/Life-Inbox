"""Smart templates: recognise what *kind* of thing an item is, and pull out
structured fields for it.

    "Paneer tikka: 200g paneer, yogurt, spices. Marinate 1 hr, grill 15 min"
        -> kind = "recipe"
           fields = {"name": "Paneer tikka",
                     "ingredients": ["200g paneer", "yogurt", "spices"],
                     "steps": ["Marinate 1 hr", "Grill 15 min"], ...}

How it works:
  1. If AI is configured (and the item is not sensitive), ask the AI for JSON.
  2. Check the AI's answer: known kind? only known fields? right types?
  3. If anything goes wrong, use a simple keyword-based guess instead.

To add a new kind, add one entry to KIND_FIELDS -- nothing else is needed.
"""

import json
import logging
import re

from app.expenses import CATEGORIES, find_amount, find_date
from app.organizer import extract_json_object
from app.privacy import ai_is_configured, ask_ai

logger = logging.getLogger(__name__)

# Each kind and its fields. "list" fields hold several short strings,
# "text" fields hold one string, "number" fields hold a number.
KIND_FIELDS = {
    "recipe": {"name": "text", "ingredients": "list", "steps": "list", "time": "text"},
    "book": {"title": "text", "author": "text", "quotes": "list", "status": "text"},
    "contact": {"name": "text", "phone": "text", "email": "text", "company": "text"},
    "event": {"name": "text", "date": "text", "time": "text", "venue": "text"},
    "receipt": {"vendor": "text", "amount": "number", "date": "text", "category": "text"},
    "other": {},
}

# Words that suggest a kind, used when AI is not available.
KIND_KEYWORDS = {
    "recipe": ["recipe", "ingredients", "tbsp", "tsp", "bake", "marinate", "cook", "grams"],
    "book": ["book", "author", "novel", "chapter", "read ", "reading", "quote"],
    "contact": ["phone", "contact", "number is", "email", "@", "met "],
    "event": ["event", "wedding", "party", "concert", "meetup", "venue", "conference"],
    "receipt": ["receipt", "invoice", "total", "paid", "₹", "rs.", "gst", "bill no"],
}

MAX_TEXT = 200
MAX_LIST_ITEMS = 20


def guess_kind(text):
    """Pick the kind whose keywords appear most often. Ties / none -> "other"."""
    lowered = text.lower()
    scores = {
        kind: sum(1 for word in words if word in lowered)
        for kind, words in KIND_KEYWORDS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def fallback_card(text):
    """No-AI version: guess the kind, and fill the few fields regex can find."""
    kind = guess_kind(text)
    fields = {}
    if kind == "contact":
        email = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
        phone = re.search(r"(?:\+\d{1,3}[ -]?)?(?:\d[ -]?){9}\d", text)
        if email:
            fields["email"] = email.group(0)
        if phone:
            fields["phone"] = phone.group(0)
    if kind == "receipt":
        amount = find_amount(text)
        date = find_date(text)
        if amount is not None:
            fields["amount"] = amount
        if date:
            fields["date"] = date
    return {"kind": kind, "fields": fields, "source": "fallback"}


def clean_card(data):
    """Check the AI's JSON. Return a clean card, or None if it is unusable."""
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if kind not in KIND_FIELDS:
        return None
    raw_fields = data.get("fields") or {}
    if not isinstance(raw_fields, dict):
        return None

    fields = {}
    for name, field_type in KIND_FIELDS[kind].items():
        value = raw_fields.get(name)
        if field_type == "text" and isinstance(value, str) and value.strip():
            fields[name] = value.strip()[:MAX_TEXT]
        elif field_type == "list" and isinstance(value, list):
            items = [str(v).strip()[:MAX_TEXT] for v in value if str(v).strip()]
            if items:
                fields[name] = items[:MAX_LIST_ITEMS]
        elif field_type == "number":
            number = to_number(value)
            if number is not None:
                fields[name] = number
        # Anything else (unknown field, wrong type, empty) is simply dropped.
    return {"kind": kind, "fields": fields, "source": "ai"}


def to_number(value):
    """1250, 1250.5, "1,250.50" or "₹1250" -> a float; anything else -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value)
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def build_prompt(text):
    return f"""Classify this saved note and extract structured fields.

Return ONLY JSON in this shape:
{{"kind": "<one of: {', '.join(KIND_FIELDS)}>", "fields": {{...}}}}

Fields allowed for each kind ("list" means a JSON array of short strings):
{json.dumps(KIND_FIELDS, indent=2)}

Rules:
- Use "other" if the note is none of these kinds (fields must then be {{}}).
- Only use facts written in the note. Leave a field out if it is not there.
- Keep values short.
- Receipts: "amount" is the final total as a number, "date" is YYYY-MM-DD,
  "category" is one of: {', '.join(CATEGORIES)}.

Note:
{text[:3000]}
"""


def make_card(text, is_sensitive=False):
    """Return {"kind", "fields", "source"} for an item's text."""
    if is_sensitive or not ai_is_configured():
        return fallback_card(text)
    try:
        reply = ask_ai(build_prompt(text), feature="smart_card")
        card = clean_card(extract_json_object(reply))
        if card is not None:
            return card
        logger.info("AI card was not usable; using fallback")
    except Exception:
        logger.warning("Smart card AI call failed; using fallback", exc_info=True)
    return fallback_card(text)
