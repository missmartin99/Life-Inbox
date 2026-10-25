import json
import os

from app.database import add_note_to_db, init_db, list_notes_from_db


NUM_DEMO_NOTES = 25
MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

FALLBACK_DEMO_NOTES = [
    "Call CA tomorrow about the advance tax calculation.",
    "Idea: build a single inbox that organizes notes, reminders, links and random thoughts.",
    "Buy a birthday gift for Mom before the weekend.",
    "Rohan recommended Atomic Habits. Order it this weekend.",
    "Ask Rohan for the name of that Gurgaon restaurant he mentioned.",
    "Finish the architecture review document before Thursday afternoon.",
    "Interesting article on vector databases. Read it when I get time.",
    "Electricity bill is due on the 12th. Pay before the due date.",
    "Book dentist appointment for next week.",
    "Potential app idea: voice notes that automatically become tasks and reminders.",
    "Follow up with recruiter if there is no response by Friday.",
    "Need to compare health insurance renewal options for parents.",
    "Pick up dry cleaning on the way home.",
    "Watch the system design lecture on caching and take notes.",
    "Remember to send the reimbursement documents to finance.",
    "Trip idea: 3-day family break in Jaipur during the next long weekend.",
    "Research whether the Life Inbox app should use Postgres or SQLite initially.",
    "Call Papa in the evening and ask about the property tax receipt.",
    "Need groceries: curd, milk, bread, bananas and coffee.",
    "Idea for class: demonstrate the same API from browser, Python and mobile client.",
    "Prepare questions for tomorrow's interview practice.",
    "Renew domain name before it expires later this month.",
    "Send the meeting summary to the team.",
    "Look into a lightweight workout routine that can fit into 20 minutes.",
    "Read the saved article about prompt injection attacks.",
]


def extract_json_array(raw: str):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON array found in model output")
    value = json.loads(raw[start:end + 1])
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return value


def generate_demo_notes(n=25):
    if not os.getenv("OPENAI_API_KEY"):
        print("No API key found. Using local fallback notes.")
        return FALLBACK_DEMO_NOTES[:n]

    try:
        from openai import OpenAI
        client = OpenAI()

        prompt = f"""
Generate exactly {n} realistic raw notes from one busy person's personal inbox.

The notes should feel like things someone quickly typed during normal life.
Mix:
- work follow-ups
- personal errands
- money / bills
- learning
- shopping
- people to contact
- travel ideas
- product / startup ideas
- appointments
- reminders
- a few informational notes with no action required

Requirements:
- Some notes should contain explicit urgency or timing.
- Some should imply a ToDo.
- Some should be ideas or reference material only.
- Keep each note to one short sentence.
- Do not pre-categorize, number, or explain them.
- Return ONLY a JSON array of strings.
"""

        response = client.responses.create(model=MODEL, input=prompt)
        generated = extract_json_array(response.output_text)

        clean = [str(x).strip() for x in generated if str(x).strip()]
        if len(clean) < 20:
            raise ValueError(f"Model returned only {len(clean)} usable notes")
        return clean[:n]

    except Exception as e:
        print("LLM generation failed; using fallback notes.")
        print("Reason:", str(e)[:300])
        return FALLBACK_DEMO_NOTES[:n]


if __name__ == "__main__":
    init_db()
    generated_notes = generate_demo_notes(NUM_DEMO_NOTES)
    for note_text in generated_notes:
        add_note_to_db(note_text)

    print(f"Added {len(generated_notes)} demo notes to the database.")
    print(f"Database now contains {len(list_notes_from_db())} notes.")
