"""Resurfacing: bring old notes back so they don't die in the inbox.

Two simple ideas:

  * "On this day": notes saved exactly 1 week, 1 month, 3 months or 1 year
    ago today.
  * "Forgotten": a few notes older than two weeks, picked at random -- but
    the SAME pick all day (the random generator is seeded with today's date),
    so refreshing the page doesn't shuffle them.
"""

import random
from datetime import date, timedelta

LOOKBACKS = [
    ("1 week ago", timedelta(weeks=1)),
    ("1 month ago", timedelta(days=30)),
    ("3 months ago", timedelta(days=91)),
    ("1 year ago", timedelta(days=365)),
]
FORGOTTEN_AFTER_DAYS = 14
FORGOTTEN_COUNT = 3


def saved_on(item):
    return date.fromisoformat(item["created_at"][:10])


def on_this_day(items, today):
    result = []
    for label, gap in LOOKBACKS:
        for item in items:
            if saved_on(item) == today - gap:
                result.append({"label": label, "item": item})
    return result


def forgotten(items, today):
    cutoff = today - timedelta(days=FORGOTTEN_AFTER_DAYS)
    old = [item for item in items if saved_on(item) <= cutoff]
    picker = random.Random(today.isoformat())  # same "random" pick all day
    return picker.sample(old, min(FORGOTTEN_COUNT, len(old)))


def resurface(items, today):
    return {
        "date": today.isoformat(),
        "on_this_day": on_this_day(items, today),
        "forgotten": forgotten(items, today),
    }
