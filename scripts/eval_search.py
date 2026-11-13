"""Measure search quality: keyword-only vs keyword + embeddings.

Run it (downloads the ~90 MB model on first use):

    python -m scripts.eval_search

It saves 15 sample notes into a temporary database, asks 12 questions that
are worded DIFFERENTLY from the notes (e.g. "teeth checkup" for a dentist
note), and counts how often the right note comes back:

    hit@1 = the right note is the top result
    hit@3 = the right note is somewhere in the top 3
"""

import os
import sys
import tempfile
from pathlib import Path

NOTES = [
    "Pay rent of 15000 to the landlord by the 5th",
    "Buy a birthday cake for Anu's party on Saturday",
    "Dentist appointment Thursday 4pm at Smile Clinic",
    "Read Atomic Habits chapter 3 tonight",
    "Renew car insurance before March 15",
    "Book train tickets to Jaipur for the long weekend",
    "Call the plumber about the leaking kitchen tap",
    "Submit the quarterly report to my manager by Friday",
    "Try the new Korean place in Cyber Hub that Rohan suggested",
    "Pick up blood pressure tablets from the pharmacy",
    "Gym membership renewal is due next week",
    "Learn basic Spanish on Duolingo for 10 minutes daily",
    "Send Diwali gifts to the extended family",
    "Electricity bill of 2300 due on the 20th",
    "Fix the laptop battery that drains too fast",
]

# (question, index of the note that answers it) -- no shared keywords on purpose
QUESTIONS = [
    ("money I owe for the flat", 0),
    ("teeth checkup", 2),
    ("what book am I reading", 3),
    ("vehicle policy deadline", 4),
    ("holiday travel plans", 5),
    ("water problem at home", 6),
    ("work deadline", 7),
    ("restaurant recommendation", 8),
    ("medicine to collect", 9),
    ("fitness subscription", 10),
    ("language practice", 11),
    ("festival presents", 12),
]

# Exact-word searches must keep working too (embeddings must not hurt these).
KEYWORD_QUESTIONS = [
    ("Jaipur", 5),
    ("plumber", 6),
    ("Duolingo", 11),
    ("electricity bill", 13),
    ("laptop battery", 14),
]


def run(client, label, questions):
    hits_at_1 = hits_at_3 = 0
    for question, gold in questions:
        results = client.get("/api/search", params={"q": question, "limit": 3}).json()
        ids = [r["item_id"] for r in results]
        gold_id = gold + 1  # items are created in order with ids 1..15
        hits_at_1 += ids[:1] == [gold_id]
        hits_at_3 += gold_id in ids
    n = len(questions)
    print(f"{label:28s} hit@1 = {hits_at_1}/{n}   hit@3 = {hits_at_3}/{n}")


def main():
    tmp = tempfile.mkdtemp()
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'eval.db'}"
    os.environ["LIFE_INBOX_AUTO_CREATE_TABLES"] = "1"
    os.environ["LIFE_INBOX_UPLOAD_DIR"] = str(Path(tmp) / "uploads")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        for note in NOTES:
            client.post("/api/items", json={"raw_text": note})

        for name, questions in [
            ("Reworded questions", QUESTIONS),
            ("Exact-word questions", KEYWORD_QUESTIONS),
        ]:
            print(name)
            os.environ["LIFE_INBOX_EMBEDDINGS"] = "0"
            run(client, "  keyword only (before)", questions)
            os.environ["LIFE_INBOX_EMBEDDINGS"] = "1"
            run(client, "  keyword + embeddings (after)", questions)


if __name__ == "__main__":
    sys.exit(main())
