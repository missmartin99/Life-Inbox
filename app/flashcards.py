"""Flashcards with spaced repetition, using the Leitner box system.

Turn a note into question/answer cards, then review them on a schedule that
spaces out the cards you know well:

    box:          0      1      2      3      4
    review after: 1 day  3 days 7 days 14 days 30 days

    remembered it?  yes -> move one box up (seen less often)
                    no  -> back to box 0   (seen again tomorrow)

This is the paper flashcard method by Sebastian Leitner (1970s). Apps like
Anki use a fancier formula (SM-2), but the idea is the same: spend review
time on what you are forgetting, not on what you already know.
"""

import logging
import re
from datetime import timedelta

from app.organizer import extract_json_object
from app.privacy import ai_is_configured, ask_ai

logger = logging.getLogger(__name__)

INTERVAL_DAYS = [1, 3, 7, 14, 30]  # one entry per box
MAX_CARDS_PER_ITEM = 3


# --- scheduling ------------------------------------------------------------------


def next_review(box, remembered, today):
    """Return (new_box, next_due_date) after one review."""
    if remembered:
        box = min(box + 1, len(INTERVAL_DAYS) - 1)
    else:
        box = 0
    return box, today + timedelta(days=INTERVAL_DAYS[box])


# --- making cards ------------------------------------------------------------------


def fallback_cards(text):
    """No-AI cards: "fill in the blank" by hiding the longest word of a sentence.

    "Pay rent of 15000 to the landlord by the 5th"
        -> Q: "Pay rent of 15000 to the ____ by the 5th"   A: "landlord"
    """
    cards = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        sentence = sentence.strip()
        words = re.findall(r"[A-Za-z]{5,}", sentence)
        if not words:
            continue
        answer = max(words, key=len)
        question = sentence.replace(answer, "____", 1)
        cards.append({"question": question, "answer": answer})
        if len(cards) == MAX_CARDS_PER_ITEM:
            break
    return cards


def clean_cards(data):
    """Check the AI's JSON: a list of short, non-empty question/answer pairs."""
    if not isinstance(data, dict) or not isinstance(data.get("cards"), list):
        return None
    cards = []
    for card in data["cards"]:
        if not isinstance(card, dict):
            continue
        question = str(card.get("question") or "").strip()[:300]
        answer = str(card.get("answer") or "").strip()[:300]
        if question and answer:
            cards.append({"question": question, "answer": answer})
    return cards[:MAX_CARDS_PER_ITEM] or None


def build_prompt(text):
    return f"""Write up to {MAX_CARDS_PER_ITEM} flashcards to help me remember this note.

Return ONLY JSON like:
{{"cards": [{{"question": "...", "answer": "..."}}]}}

Rules:
- Ask about the most useful facts: names, numbers, dates, key ideas.
- Use only facts written in the note. Keep answers short.

Note:
{text[:3000]}
"""


def make_cards(text, is_sensitive=False):
    """Return a list of {"question", "answer"} for a note."""
    if is_sensitive or not ai_is_configured():
        return fallback_cards(text)
    try:
        cards = clean_cards(extract_json_object(ask_ai(build_prompt(text), feature="flashcards")))
        if cards:
            return cards
    except Exception:
        logger.warning("Flashcard AI call failed; using fallback", exc_info=True)
    return fallback_cards(text)
