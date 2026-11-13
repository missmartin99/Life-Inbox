from datetime import date

from app.assistant_actions import match_task, parse_task_command, snooze_date

TASKS = [{"text": "Have a meeting at 9 pm"}, {"text": "Get cigarettes at 10 pm"}]


def test_done_and_snooze_are_parsed():
    assert parse_task_command("mark the meeting done")["action"] == "done"
    cmd = parse_task_command("snooze cigarettes to tomorrow")
    assert (cmd["action"], cmd["target"], cmd["when"]) == ("snooze", "cigarettes", "tomorrow")
    assert parse_task_command("what is on today?") is None


def test_task_matching_and_ambiguity():
    task, _ = match_task("the meeting", TASKS)
    assert task["text"].startswith("Have a meeting")
    assert match_task("dentist", TASKS) == (None, [])
    _, options = match_task("pm", TASKS)
    assert len(options) == 2


def test_snooze_dates():
    monday = date(2026, 11, 9)
    assert snooze_date("tomorrow", monday) == date(2026, 11, 10)
    assert snooze_date("friday", monday) == date(2026, 11, 13)
    assert snooze_date("next week", monday) == date(2026, 11, 16)
    assert snooze_date("whenever", monday) is None
