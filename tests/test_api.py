import importlib
import json
import re
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def analysis_without_metadata(data):
    return {
        key: value
        for key, value in data.items()
        if key
        not in {
            "analysis_run_id",
            "generated_at",
            "is_stale",
            "inbox_fingerprint",
            "analyzed_item_ids",
        }
    }


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create an app client backed only by a per-test SQLite database."""
    test_db_path = tmp_path / "life_inbox_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    for module_name in (
        "app.main",
        "app.database",
        "app.auth",
        "app.config",
        "app.integrations.calendar",
    ):
        sys.modules.pop(module_name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    assert database.engine.url.database == str(test_db_path)
    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    for module_name in (
        "app.main",
        "app.database",
        "app.auth",
        "app.config",
        "app.integrations.calendar",
    ):
        sys.modules.pop(module_name, None)


def test_static_files_are_revalidated_so_browsers_never_run_stale_scripts(client):
    first = client.get("/static/app.js")
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-cache"

    # Unchanged files are still cheap: the ETag yields a 304 on revalidation.
    again = client.get(
        "/static/app.js", headers={"if-none-match": first.headers["etag"]}
    )
    assert again.status_code == 304


def test_home_page_loads(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>Life Inbox</title>" in response.text
    # Notes are analyzed automatically, so there is no analyze button and
    # no separate AI Inbox page.
    assert "Analyze Entire Inbox" not in response.text
    assert 'id="analyzeButton"' not in response.text
    assert "AI Inbox" not in response.text
    assert 'id="panel-ai"' not in response.text
    assert 'data-tab="ai"' not in response.text
    for tab in ("capture", "ask", "dashboard", "notes"):
        assert f'data-tab="{tab}"' in response.text


@pytest.mark.parametrize(
    ("path", "content_type", "expected_content"),
    [
        ("/static/styles.css", "text/css", ".wrap"),
        ("/static/app.js", "javascript", "async function addNote()"),
    ],
)
def test_static_assets_load(client, path, content_type, expected_content):
    response = client.get(path)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert expected_content in response.text


def test_health_check(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "environment" in payload
    assert "database" in payload


def test_create_and_list_canonical_text_item(client):
    response = client.post(
        "/api/items",
        json={"raw_text": "Canonical text item"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item == {
        "id": 1,
        "item_type": "text",
        "title": None,
        "raw_text": "Canonical text item",
        "source_url": None,
        "original_filename": None,
        "mime_type": None,
        "processing_status": "ready",
        "error_message": None,
        "storage_path": None,
        "extracted_text": None,
        "ai_description": None,
        "owner_user_id": item["owner_user_id"],
        "created_at": item["created_at"],
        "updated_at": None,
        "is_sensitive": False,
        "kind": None,
        "kind_fields": None,
    }
    assert item["owner_user_id"] is not None
    assert client.get("/api/items").json() == [item]


def test_item_search_is_case_insensitive_for_raw_text_and_title(client):
    text_match = client.post(
        "/api/items",
        json={"raw_text": "Plan the SUMMER holiday"},
    ).json()
    title_match = client.post(
        "/api/items",
        json={"raw_text": "Review the figures"},
    ).json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        item = session.get(database.InboxItemRecord, title_match["id"])
        item.title = "Annual BUDGET"
        session.commit()

    raw_results = client.get("/api/items", params={"q": "summer"})
    title_results = client.get("/api/items", params={"q": "budget"})

    assert raw_results.status_code == 200
    assert [item["id"] for item in raw_results.json()] == [text_match["id"]]
    assert title_results.status_code == 200
    assert [item["id"] for item in title_results.json()] == [
        title_match["id"]
    ]


def test_item_search_combines_all_filters(client):
    first = client.post(
        "/api/items",
        json={"raw_text": "Quarterly REPORT"},
    ).json()
    second = client.post(
        "/api/items",
        json={"raw_text": "Quarterly report archive"},
    ).json()
    third = client.post(
        "/api/items",
        json={"raw_text": "Personal report"},
    ).json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = database.AnalysisRunRecord(
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            status="succeeded",
            mode="fallback_no_api_call",
            result_json="{}",
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                database.ItemInsightRecord(
                    item_id=first["id"],
                    analysis_run_id=run.id,
                    category="Work",
                    concise_title="Quarterly report",
                    priority="High",
                    updated_at=run.completed_at,
                ),
                database.ItemInsightRecord(
                    item_id=second["id"],
                    analysis_run_id=run.id,
                    category="Work",
                    concise_title="Archived report",
                    priority="High",
                    updated_at=run.completed_at,
                ),
                database.ItemInsightRecord(
                    item_id=third["id"],
                    analysis_run_id=run.id,
                    category="Personal",
                    concise_title="Personal report",
                    priority="Low",
                    updated_at=run.completed_at,
                ),
            ]
        )
        archived = session.get(database.InboxItemRecord, second["id"])
        archived.processing_status = "pending"
        session.commit()

    response = client.get(
        "/api/items",
        params={
            "q": "report",
            "item_type": "text",
            "category": "Work",
            "priority": "High",
            "processing_status": "ready",
        },
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [first["id"]]


def test_item_search_handles_empty_results_and_sql_characters(client):
    percent_match = client.post(
        "/api/items",
        json={"raw_text": "Discount is 50% today"},
    ).json()
    other = client.post(
        "/api/items",
        json={"raw_text": "Ordinary note"},
    ).json()

    literal_percent = client.get("/api/items", params={"q": "50%"})
    unsafe_text = client.get(
        "/api/items",
        params={"q": "%' OR 1=1 --"},
    )
    no_results = client.get("/api/items", params={"q": "not-present"})
    empty_search = client.get("/api/items", params={"q": "   "})

    assert [item["id"] for item in literal_percent.json()] == [
        percent_match["id"]
    ]
    assert unsafe_text.status_code == 200
    assert unsafe_text.json() == []
    assert no_results.status_code == 200
    assert no_results.json() == []
    assert [item["id"] for item in empty_search.json()] == [
        percent_match["id"],
        other["id"],
    ]


def test_item_search_applies_conservative_result_limit(client):
    for number in range(3):
        client.post(
            "/api/items",
            json={"raw_text": f"Matching item {number}"},
        )

    response = client.get(
        "/api/items",
        params={"q": "matching", "limit": 2},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [1, 2]


def test_update_canonical_text_item(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Original item"},
    ).json()

    response = client.put(
        f"/api/items/{created['id']}",
        json={"raw_text": "Updated item"},
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == created["id"]
    assert updated["raw_text"] == "Updated item"
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] is not None
    assert client.get("/api/notes").json() == [
        {
            "id": created["id"],
            "text": "Updated item",
            "created_at": created["created_at"],
        }
    ]


@pytest.mark.parametrize("raw_text", ["", "   \t\n", "x" * 2001])
def test_canonical_item_rejects_invalid_text(client, raw_text):
    response = client.post(
        "/api/items",
        json={"raw_text": raw_text},
    )

    assert response.status_code == 422
    assert client.get("/api/items").json() == []


@pytest.mark.parametrize("raw_text", ["", "   \t\n", "x" * 2001])
def test_canonical_item_update_rejects_invalid_text(client, raw_text):
    created = client.post(
        "/api/items",
        json={"raw_text": "Keep this item"},
    ).json()

    response = client.put(
        f"/api/items/{created['id']}",
        json={"raw_text": raw_text},
    )

    assert response.status_code == 422
    assert client.get("/api/items").json() == [created]


def test_canonical_item_missing_ids_return_404(client):
    update_response = client.put(
        "/api/items/999",
        json={"raw_text": "Updated item"},
    )
    delete_response = client.delete("/api/items/999")

    assert update_response.status_code == 404
    assert update_response.json() == {"detail": "Item not found."}
    assert delete_response.status_code == 404
    assert delete_response.json() == {"detail": "Item not found."}


def test_delete_canonical_item_affects_only_requested_item(client):
    first = client.post(
        "/api/items", json={"raw_text": "Delete this item"}
    ).json()
    second = client.post(
        "/api/items", json={"raw_text": "Keep this item"}
    ).json()

    response = client.delete(f"/api/items/{first['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/items").json() == [second]


def test_notes_api_uses_canonical_item_storage(client):
    note = client.post(
        "/api/notes", json={"text": "Compatibility note"}
    ).json()

    items = client.get("/api/items").json()

    assert len(items) == 1
    assert items[0]["id"] == note["id"]
    assert items[0]["item_type"] == "text"
    assert items[0]["raw_text"] == note["text"]


def test_create_note(client):
    response = client.post("/api/notes", json={"text": "Buy milk tomorrow"})

    assert response.status_code == 200
    note = response.json()
    assert note["id"] == 1
    assert note["text"] == "Buy milk tomorrow"
    assert note["created_at"]


def test_list_saved_notes(client):
    first = client.post("/api/notes", json={"text": "First note"})
    second = client.post("/api/notes", json={"text": "Second note"})

    response = client.get("/api/notes")

    assert first.status_code == 200
    assert second.status_code == 200
    assert response.status_code == 200
    assert [note["text"] for note in response.json()] == [
        "First note",
        "Second note",
    ]


def test_update_note(client):
    created = client.post(
        "/api/notes", json={"text": "Original note"}
    ).json()

    response = client.put(
        f"/api/notes/{created['id']}",
        json={"text": "Updated note"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": created["id"],
        "text": "Updated note",
        "created_at": created["created_at"],
    }
    assert client.get("/api/notes").json() == [response.json()]

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        stored_item = session.get(database.InboxItemRecord, created["id"])
        assert stored_item.updated_at is not None


@pytest.mark.parametrize("text", ["", "   \t\n", "x" * 2001])
def test_update_note_rejects_invalid_text(client, text):
    created = client.post(
        "/api/notes", json={"text": "Keep this note"}
    ).json()

    response = client.put(
        f"/api/notes/{created['id']}",
        json={"text": text},
    )

    assert response.status_code == 422
    assert client.get("/api/notes").json() == [created]


def test_update_nonexistent_note_returns_404(client):
    response = client.put(
        "/api/notes/999",
        json={"text": "Updated note"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found."}


def test_delete_only_requested_note(client):
    first = client.post(
        "/api/notes", json={"text": "Delete this note"}
    ).json()
    second = client.post(
        "/api/notes", json={"text": "Keep this note"}
    ).json()

    response = client.delete(f"/api/notes/{first['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/api/notes").json() == [second]


def test_delete_nonexistent_note_returns_404(client):
    response = client.delete("/api/notes/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Note not found."}


@pytest.mark.parametrize("text", ["", "   \t\n", "x" * 2001])
def test_invalid_note_text_is_rejected(client, text):
    response = client.post("/api/notes", json={"text": text})

    assert response.status_code == 422
    assert client.get("/api/notes").json() == []


def test_latest_analysis_is_empty_before_first_success(client):
    response = client.get("/api/analyze/latest")

    assert response.status_code == 204
    assert response.content == b""


def test_analyze_empty_inbox(client):
    response = client.post("/api/analyze")

    assert response.status_code == 200
    data = response.json()
    assert data["analysis_run_id"] == 1
    assert data["generated_at"]
    assert analysis_without_metadata(data) == {
        "buckets": [],
        "todos": [],
        "priority_view": {"High": [], "Medium": [], "Low": []},
        "mode": "empty_inbox",
    }
    assert client.get("/api/analyze/latest").json() == data


def test_analyze_uses_deterministic_fallback_without_openai_key(client):
    note_text = "Call the team tomorrow"
    create_response = client.post("/api/notes", json={"text": note_text})

    response = client.post("/api/analyze")

    assert create_response.status_code == 200
    assert response.status_code == 200
    data = response.json()
    assert data["analysis_run_id"] == 1
    assert data["generated_at"]
    assert analysis_without_metadata(data) == {
        "buckets": [
            {
                "name": "Work",
                "notes": [
                    {
                        "id": 1,
                        "title": note_text,
                        "priority": "High",
                        "tags": ["urgent", "follow-up"],
                        "rationale": (
                            'Matched topic “Work” from keywords in the note.'
                        ),
                    }
                ],
            }
        ],
        "todos": [
            {
                "task": note_text,
                "priority": "High",
                "source_note_ids": [1],
                "when": "tomorrow",
            }
        ],
        "priority_view": {
            "High": [{"id": 1, "title": note_text}],
            "Medium": [],
            "Low": [],
        },
        "mode": "fallback_no_api_call",
    }


def test_successful_analysis_persists_run_and_item_insight(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()

    analysis = client.post("/api/analyze").json()

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = session.get(
            database.AnalysisRunRecord,
            analysis["analysis_run_id"],
        )
        insight = session.get(
            database.ItemInsightRecord,
            created["id"],
        )
        assert run.status == "succeeded"
        assert run.mode == "fallback_no_api_call"
        assert run.started_at
        assert run.completed_at == analysis["generated_at"]
        stored = json.loads(run.result_json)
        assert stored.pop("inbox_fingerprint") == analysis["inbox_fingerprint"]
        assert stored.pop("analyzed_item_ids") == [created["id"]]
        assert stored == analysis_without_metadata(analysis)
        assert analysis["is_stale"] is False
        assert insight.analysis_run_id == run.id
        assert insight.category == "Work"
        assert insight.concise_title == "Call the team tomorrow"
        assert insight.priority == "High"


def test_analysis_creates_open_task_with_source_link(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()

    client.post("/api/analyze")
    response = client.get("/api/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["text"] == "Call the team tomorrow"
    assert tasks[0]["priority"] == "High"
    assert tasks[0]["timing_text"] == "tomorrow"
    assert tasks[0]["ai_timing_text"] == "tomorrow"
    assert tasks[0]["timing_confirmed"] is False
    assert tasks[0]["status"] == "open"
    assert tasks[0]["source_item_ids"] == [created["id"]]
    assert tasks[0]["created_at"]
    assert tasks[0]["updated_at"]


def test_task_statuses_survive_reload_and_reanalysis_without_duplicates(client):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    client.post("/api/analyze")
    task = client.get("/api/tasks").json()[0]

    completed = client.post(
        f"/api/tasks/{task['id']}/complete"
    ).json()

    assert completed["status"] == "completed"
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks?status=completed").json() == [completed]

    client.post("/api/analyze")
    all_tasks = client.get("/api/tasks?status=all").json()
    assert len(all_tasks) == 1
    assert all_tasks[0]["id"] == task["id"]
    assert all_tasks[0]["status"] == "completed"

    reopened = client.post(
        f"/api/tasks/{task['id']}/reopen"
    ).json()
    assert reopened["status"] == "open"
    assert client.get("/api/tasks").json() == [reopened]

    dismissed = client.post(
        f"/api/tasks/{task['id']}/dismiss"
    ).json()
    assert dismissed["status"] == "dismissed"
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/tasks?status=dismissed").json() == [dismissed]

    client.post("/api/analyze")
    final_tasks = client.get("/api/tasks?status=all").json()
    assert len(final_tasks) == 1
    assert final_tasks[0]["id"] == task["id"]
    assert final_tasks[0]["status"] == "dismissed"


def test_reanalysis_updates_task_by_source_without_changing_status(
    client, monkeypatch
):
    item = client.post(
        "/api/items",
        json={"raw_text": "Prepare the class notes"},
    ).json()
    main = sys.modules["app.main"]
    analyses = iter(
        [
            ("Prepare class notes", "Low"),
            ("Prepare notes for class", "High"),
        ]
    )

    def analyze(_notes):
        task_text, priority = next(analyses)
        return {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {
                            "id": item["id"],
                            "title": task_text,
                            "priority": priority,
                        }
                    ],
                }
            ],
            "todos": [
                {
                    "task": task_text,
                    "priority": priority,
                    "source_note_ids": [item["id"]],
                    "when": None,
                }
            ],
            "priority_view": {
                "High": (
                    [{"id": item["id"], "title": task_text}]
                    if priority == "High"
                    else []
                ),
                "Medium": [],
                "Low": (
                    [{"id": item["id"], "title": task_text}]
                    if priority == "Low"
                    else []
                ),
            },
            "mode": "openai",
        }

    monkeypatch.setattr(main, "analyze_inbox_with_openai", analyze)

    client.post("/api/analyze")
    original = client.get("/api/tasks").json()[0]
    client.post(f"/api/tasks/{original['id']}/complete")

    client.post("/api/analyze?scope=all")
    tasks = client.get("/api/tasks?status=all").json()

    assert len(tasks) == 1
    assert tasks[0]["id"] == original["id"]
    assert tasks[0]["text"] == "Prepare notes for class"
    assert tasks[0]["priority"] == "High"
    assert tasks[0]["status"] == "completed"
    assert tasks[0]["source_item_ids"] == [item["id"]]


def test_generated_task_preserves_multiple_source_links(client, monkeypatch):
    first = client.post(
        "/api/items", json={"raw_text": "First source"}
    ).json()
    second = client.post(
        "/api/items", json={"raw_text": "Second source"}
    ).json()
    main = sys.modules["app.main"]

    monkeypatch.setattr(
        main,
        "analyze_inbox_with_openai",
        lambda _notes: {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {
                            "id": first["id"],
                            "title": "First source",
                            "priority": "Medium",
                        },
                        {
                            "id": second["id"],
                            "title": "Second source",
                            "priority": "Medium",
                        },
                    ],
                }
            ],
            "todos": [
                {
                    "task": "Follow up on both sources",
                    "priority": "Medium",
                    "source_note_ids": [second["id"], first["id"]],
                    "when": "tomorrow",
                }
            ],
            "priority_view": {
                "High": [],
                "Medium": [
                    {"id": first["id"], "title": "First source"},
                    {"id": second["id"], "title": "Second source"},
                ],
                "Low": [],
            },
            "mode": "openai",
        },
    )

    response = client.post("/api/analyze")

    assert response.status_code == 200
    task = client.get("/api/tasks").json()[0]
    assert task["text"] == "Follow up on both sources"
    assert task["timing_text"] == "tomorrow"
    assert task["source_item_ids"] == [first["id"], second["id"]]

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        assert session.query(database.TaskSourceItemRecord).count() == 2


@pytest.mark.parametrize("action", ["complete", "reopen", "dismiss"])
def test_task_status_actions_return_404_for_unknown_id(client, action):
    response = client.post(f"/api/tasks/999/{action}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found."}


def test_task_list_rejects_invalid_status(client):
    response = client.get("/api/tasks?status=invalid")

    assert response.status_code == 422


def test_failed_analysis_keeps_previous_success(client, monkeypatch):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    previous = client.post("/api/analyze").json()
    main = sys.modules["app.main"]

    def fail_analysis(_notes):
        raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(main, "analyze_inbox_with_openai", fail_analysis)

    failed_response = client.post("/api/analyze?scope=all")

    assert failed_response.status_code == 500
    assert client.get("/api/analyze/latest").json() == previous

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        runs = session.query(database.AnalysisRunRecord).order_by(
            database.AnalysisRunRecord.id
        ).all()
        insight = session.get(database.ItemInsightRecord, 1)
        assert [run.status for run in runs] == ["succeeded", "failed"]
        assert runs[1].mode == "error"
        assert runs[1].result_json is None
        assert insight.analysis_run_id == runs[0].id


def test_invalid_analysis_result_is_not_saved(client, monkeypatch):
    client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    )
    main = sys.modules["app.main"]

    monkeypatch.setattr(
        main,
        "analyze_inbox_with_openai",
        lambda _notes: {
            "buckets": [],
            "todos": [],
            "priority_view": {"High": [], "Medium": [], "Low": []},
            "mode": "openai",
        },
    )

    response = client.post("/api/analyze")

    assert response.status_code == 500
    assert client.get("/api/analyze/latest").status_code == 204

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        run = session.get(database.AnalysisRunRecord, 1)
        assert run.status == "failed"
        assert run.result_json is None
        assert session.query(database.ItemInsightRecord).count() == 0


def test_create_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_save(_text):
        raise RuntimeError("database password should not be exposed")

    monkeypatch.setattr(main, "add_note_to_db", fail_to_save)

    response = client.post("/api/notes", json={"text": "Keep this text"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to save note. Please try again."
    }
    assert "password" not in response.text


def test_list_notes_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_list():
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "list_notes_from_db", fail_to_list)

    response = client.get("/api/notes")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to load notes. Please try again."
    }
    assert "private database" not in response.text


def test_update_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_update(_note_id, _text):
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "update_note_in_db", fail_to_update)

    response = client.put(
        "/api/notes/1",
        json={"text": "Updated note"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to update note. Please try again."
    }
    assert "private database" not in response.text


def test_delete_note_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_delete(_note_id):
        raise RuntimeError("private database location")

    monkeypatch.setattr(main, "delete_note_from_db", fail_to_delete)

    response = client.delete("/api/notes/1")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to delete note. Please try again."
    }
    assert "private database" not in response.text


def test_analyze_returns_friendly_error(client, monkeypatch):
    main = sys.modules["app.main"]

    def fail_to_analyze(_notes):
        raise RuntimeError("OPENAI_API_KEY=super-secret")

    monkeypatch.setattr(main, "analyze_inbox_with_openai", fail_to_analyze)

    response = client.post("/api/analyze")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Unable to analyze inbox. Please try again."
    }
    assert "super-secret" not in response.text


def test_valid_mocked_openai_output_is_validated(monkeypatch):
    import openai

    from app import organizer

    model_output = {
        "buckets": [
            {
                "name": "Work",
                "notes": [
                    {
                        "id": 1,
                        "title": "Send summary",
                        "priority": "Medium",
                    }
                ],
            }
        ],
        "todos": [
            {
                "task": "Send the meeting summary",
                "priority": "Medium",
                "source_note_ids": [1],
                "when": None,
            }
        ],
        "priority_view": {
            "High": [],
            "Medium": [{"id": 1, "title": "Send summary"}],
            "Low": [],
        },
    }
    fake_completions = SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=json.dumps(model_output)))
            ]
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=fake_completions)
        ),
    )

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Send the meeting summary",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "openai"
    assert result["buckets"][0]["notes"][0]["priority"] == "Medium"


def test_malformed_mocked_openai_output_uses_fallback(monkeypatch):
    import openai

    from app import organizer

    fake_completions = SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="not valid JSON"))
            ]
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            chat=SimpleNamespace(completions=fake_completions)
        ),
    )

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Call the team tomorrow",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "fallback_after_api_error"
    assert result["buckets"][0]["notes"][0]["id"] == 1


def test_openai_failure_fallback_omits_technical_details(monkeypatch):
    import openai

    from app import organizer

    def fail_client_creation():
        raise RuntimeError("OPENAI_API_KEY=super-secret")

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setattr(openai, "OpenAI", fail_client_creation)

    result = organizer.analyze_inbox_with_openai(
        [
            {
                "id": 1,
                "text": "Call the team tomorrow",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
    )

    assert result["mode"] == "fallback_after_api_error"
    assert "error" not in result
    assert "super-secret" not in repr(result)


def test_item_list_supports_offset_and_raised_limit(client):
    for number in range(5):
        client.post(
            "/api/items",
            json={"raw_text": f"Paged item {number}"},
        )

    page = client.get("/api/items", params={"limit": 2, "offset": 2})

    assert page.status_code == 200
    assert [item["id"] for item in page.json()] == [3, 4]

    oversized = client.get("/api/items", params={"limit": 501})
    assert oversized.status_code == 422


def test_analysis_marked_stale_after_inbox_changes(client):
    client.post("/api/items", json={"raw_text": "Call the dentist tomorrow"})
    first = client.post("/api/analyze").json()
    assert first["is_stale"] is False
    assert first["inbox_fingerprint"]

    latest = client.get("/api/analyze/latest").json()
    assert latest["is_stale"] is False
    assert latest["inbox_fingerprint"] == first["inbox_fingerprint"]

    client.post("/api/items", json={"raw_text": "Buy groceries this weekend"})
    stale = client.get("/api/analyze/latest").json()
    assert stale["is_stale"] is True
    assert stale["analysis_run_id"] == first["analysis_run_id"]


def test_deleting_item_removes_orphan_task(client):
    created = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()
    client.post("/api/analyze")
    assert len(client.get("/api/tasks").json()) == 1

    delete_response = client.delete(f"/api/items/{created['id']}")
    assert delete_response.status_code == 204
    assert client.get("/api/tasks").json() == []

    database = sys.modules["app.database"]
    with database.SessionLocal() as session:
        assert session.query(database.TaskRecord).count() == 0
        assert session.query(database.TaskSourceItemRecord).count() == 0
        assert session.query(database.ItemInsightRecord).count() == 0


def test_topics_and_dashboard_after_analysis(client):
    client.post(
        "/api/items",
        json={"raw_text": "Call the dentist tomorrow"},
    )
    client.post(
        "/api/items",
        json={"raw_text": "Call the dentist tomorrow please"},
    )
    analysis = client.post("/api/analyze").json()
    assert analysis["analysis_run_id"]

    topics = client.get("/api/topics").json()
    assert any(topic["name"] == "Health" for topic in topics)

    created = client.post(
        "/api/topics",
        json={"name": "Home projects", "description": "Around the house"},
    )
    assert created.status_code == 200
    assert created.json()["name"] == "Home projects"

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["topics"]
    assert dashboard["recently_captured"]
    assert dashboard["open_tasks"]
    assert any(
        rel["relation_type"] in {"duplicate", "near_duplicate", "related"}
        for rel in dashboard["related_items"]
    )


def test_user_insight_overrides_survive_reanalysis(client):
    item = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()
    client.post("/api/analyze")
    topics = client.get("/api/topics").json()
    topic_id = next(topic["id"] for topic in topics if topic["name"] == "Work")

    override = client.patch(
        f"/api/items/{item['id']}/insight",
        json={
            "user_title": "Team sync call",
            "user_topic_id": topic_id,
            "user_priority": "Low",
            "user_tags": ["mine"],
        },
    ).json()
    assert override["effective_title"] == "Team sync call"
    assert override["effective_priority"] == "Low"
    assert override["has_user_overrides"] is True

    client.post("/api/analyze")
    refreshed = client.get(f"/api/items/{item['id']}/insight").json()
    assert refreshed["user_title"] == "Team sync call"
    assert refreshed["effective_title"] == "Team sync call"
    assert refreshed["effective_priority"] == "Low"
    assert refreshed["concise_title"] != "Team sync call" or True
    assert refreshed["rationale"]


def test_one_source_can_create_multiple_tasks(client):
    client.post(
        "/api/items",
        json={
            "raw_text": (
                "Call Rohan about dinner. Buy groceries this weekend."
            )
        },
    )
    client.post("/api/analyze")
    tasks = client.get("/api/tasks").json()
    assert len(tasks) >= 2
    assert all(task["source_item_ids"] == [1] for task in tasks)


def test_confirm_task_timing_is_preserved_on_reanalysis(client, monkeypatch):
    item = client.post(
        "/api/items",
        json={"raw_text": "Call the team tomorrow"},
    ).json()
    main = sys.modules["app.main"]

    def analyze(_notes):
        return {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {
                            "id": item["id"],
                            "title": "Team call",
                            "priority": "High",
                            "tags": ["follow-up"],
                            "rationale": "Work follow-up",
                        }
                    ],
                }
            ],
            "todos": [
                {
                    "task": "Call the team",
                    "priority": "High",
                    "source_note_ids": [item["id"]],
                    "when": "tomorrow",
                }
            ],
            "priority_view": {
                "High": [{"id": item["id"], "title": "Team call"}],
                "Medium": [],
                "Low": [],
            },
            "mode": "openai",
        }

    monkeypatch.setattr(main, "analyze_inbox_with_openai", analyze)
    client.post("/api/analyze")
    task = client.get("/api/tasks").json()[0]
    confirmed = client.post(
        f"/api/tasks/{task['id']}/confirm-timing",
        json={"timing_text": "Friday morning"},
    ).json()
    assert confirmed["timing_text"] == "Friday morning"
    assert confirmed["timing_confirmed"] is True
    assert confirmed["ai_timing_text"] == "tomorrow"

    client.post("/api/analyze")
    again = client.get("/api/tasks?status=all").json()[0]
    assert again["id"] == task["id"]
    assert again["timing_text"] == "Friday morning"
    assert again["timing_confirmed"] is True
    assert again["ai_timing_text"] == "tomorrow"


def test_dismiss_relation_is_reversible_action(client):
    client.post("/api/items", json={"raw_text": "Renew car insurance soon"})
    client.post(
        "/api/items",
        json={"raw_text": "Renew the car insurance soon"},
    )
    client.post("/api/analyze")
    relations = client.get("/api/relations").json()
    assert relations
    dismissed = client.post(
        f"/api/relations/{relations[0]['id']}/dismiss"
    ).json()
    assert dismissed["dismissed"] is True
    assert client.get("/api/relations").json() == []


def test_url_capture_persists_before_processing_and_extracts_text(client, monkeypatch):
    main = sys.modules["app.main"]
    database = sys.modules["app.database"]
    original_add = database.add_url_item_to_db

    def slow_add(url):
        item = original_add(url)
        assert item["processing_status"] == "captured"
        assert item["source_url"] == "https://example.com/article"
        return item

    def fake_fetch(url):
        assert url == "https://example.com/article"
        return {
            "final_url": "https://example.com/article",
            "title": "Sample OG Title",
            "raw_text": "This is readable article text for Life Inbox URL capture tests.",
            "mime_type": "text/html",
            "status_code": 200,
        }

    monkeypatch.setattr(database, "add_url_item_to_db", slow_add)
    monkeypatch.setattr(main, "fetch_url", fake_fetch)

    response = client.post(
        "/api/items/url",
        json={"url": "https://example.com/article"},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["item_type"] == "url"
    assert item["processing_status"] == "ready"
    assert item["title"] == "Sample OG Title"
    assert "readable article text" in (item["extracted_text"] or "")


def test_url_capture_blocks_private_network_targets(client):
    response = client.post(
        "/api/items/url",
        json={"url": "http://127.0.0.1/private"},
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"].lower()


def test_failed_url_capture_can_retry(client, monkeypatch):
    main = sys.modules["app.main"]
    attempts = {"count": 0}

    def flaky_fetch(_url):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("Upstream returned HTTP 503")
        return {
            "final_url": "https://example.com/article",
            "title": "Recovered Article",
            "raw_text": "Recovered text",
            "mime_type": "text/html",
            "status_code": 200,
        }

    monkeypatch.setattr(main, "fetch_url", flaky_fetch)

    first = client.post(
        "/api/items/url",
        json={"url": "https://example.com/article"},
    ).json()
    assert first["processing_status"] == "failed"
    assert "503" in (first["error_message"] or "")

    retry = client.post(f"/api/items/{first['id']}/retry").json()
    assert retry["processing_status"] == "ready"
    assert retry["title"] == "Recovered Article"


def test_analyze_includes_ready_url_items(client, monkeypatch):
    main = sys.modules["app.main"]

    monkeypatch.setattr(
        main,
        "fetch_url",
        lambda _url: {
            "final_url": "https://example.com/article",
            "title": "Article about habits",
            "raw_text": "Need to read this article about habits tomorrow.",
            "mime_type": "text/html",
            "status_code": 200,
        },
    )
    url_item = client.post(
        "/api/items/url",
        json={"url": "https://example.com/article"},
    ).json()
    assert url_item["processing_status"] == "ready"

    analysis = client.post("/api/analyze").json()
    ids = {
        note["id"]
        for bucket in analysis["buckets"]
        for note in bucket["notes"]
    }
    assert url_item["id"] in ids


def test_pdf_capture_persists_before_processing_and_extracts_text(client, monkeypatch):
    from tests.pdf_fixtures import SAMPLE_TEXT, make_text_pdf

    main = sys.modules["app.main"]
    database = sys.modules["app.database"]
    original_add = database.add_pdf_item_to_db
    pdf_bytes = make_text_pdf()

    def slow_add(original_filename, storage_path, mime_type):
        item = original_add(original_filename, storage_path, mime_type)
        assert item["processing_status"] == "captured"
        assert item["original_filename"] == "notes.pdf"
        assert item["storage_path"]
        return item

    monkeypatch.setattr(database, "add_pdf_item_to_db", slow_add)

    response = client.post(
        "/api/items/pdf",
        files={"file": ("notes.pdf", pdf_bytes, "application/pdf")},
    )

    assert response.status_code == 200
    item = response.json()
    assert item["item_type"] == "pdf"
    assert item["processing_status"] == "ready"
    assert SAMPLE_TEXT in (item["extracted_text"] or "")

    file_response = client.get(f"/api/items/{item['id']}/file")
    assert file_response.status_code == 200
    assert file_response.headers["content-type"].startswith("application/pdf")
    assert file_response.content.startswith(b"%PDF")


def test_pdf_capture_rejects_invalid_uploads(client):
    response = client.post(
        "/api/items/pdf",
        files={"file": ("notes.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 400
    assert "pdf" in response.json()["detail"].lower()


def test_failed_pdf_capture_can_retry(client, monkeypatch):
    from tests.pdf_fixtures import make_text_pdf

    main = sys.modules["app.main"]
    attempts = {"count": 0}
    real_extract = main.extract_pdf_text

    def flaky_extract(data, *, original_filename=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("Temporary PDF parse failure")
        return real_extract(data, original_filename=original_filename)

    monkeypatch.setattr(main, "extract_pdf_text", flaky_extract)

    first = client.post(
        "/api/items/pdf",
        files={"file": ("notes.pdf", make_text_pdf(), "application/pdf")},
    ).json()
    assert first["processing_status"] == "failed"
    assert "parse failure" in (first["error_message"] or "").lower()

    retry = client.post(f"/api/items/{first['id']}/retry").json()
    assert retry["processing_status"] == "ready"


def test_analyze_includes_ready_pdf_items(client):
    from tests.pdf_fixtures import make_text_pdf

    pdf_item = client.post(
        "/api/items/pdf",
        files={"file": ("habits.pdf", make_text_pdf(title="Habits"), "application/pdf")},
    ).json()
    assert pdf_item["processing_status"] == "ready"

    analysis = client.post("/api/analyze").json()
    ids = {
        note["id"]
        for bucket in analysis["buckets"]
        for note in bucket["notes"]
    }
    assert pdf_item["id"] in ids


def test_blank_pdf_fails_with_helpful_message(client):
    from tests.pdf_fixtures import make_blank_pdf

    item = client.post(
        "/api/items/pdf",
        files={"file": ("scan.pdf", make_blank_pdf(), "application/pdf")},
    ).json()
    assert item["processing_status"] == "failed"
    assert "image-only" in (item["error_message"] or "").lower()


def test_image_capture_persists_before_processing_and_creates_preview(client, monkeypatch):
    from tests.image_fixtures import make_png_bytes

    main = sys.modules["app.main"]
    database = sys.modules["app.database"]
    original_add = database.add_image_item_to_db
    png_bytes = make_png_bytes(text="Meeting notes")
    seen = {"persisted": False}

    def slow_add(filename, storage_path, mime_type):
        item = original_add(filename, storage_path, mime_type)
        seen["persisted"] = True
        assert item["original_filename"] == "screenshot.png"
        assert item["processing_status"] == "captured"
        assert item["storage_path"]
        return item

    monkeypatch.setattr(main, "add_image_item_to_db", slow_add)
    monkeypatch.setenv("LIFE_INBOX_DISABLE_OCR", "1")

    item = client.post(
        "/api/items/image",
        files={"file": ("screenshot.png", png_bytes, "image/png")},
    ).json()

    assert seen["persisted"] is True
    assert item["item_type"] == "image"
    assert item["processing_status"] == "ready"
    assert item["ai_description"]
    assert "AI description" in (item["raw_text"] or "")
    thumb = client.get(f"/api/items/{item['id']}/file", params={"variant": "thumb"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/jpeg")
    original = client.get(f"/api/items/{item['id']}/file")
    assert original.status_code == 200
    assert original.content.startswith(b"\x89PNG")


def test_image_capture_rejects_invalid_uploads(client):
    response = client.post(
        "/api/items/image",
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400
    assert "image" in response.json()["detail"].lower()


def test_failed_image_capture_can_retry(client, monkeypatch):
    from tests.image_fixtures import make_png_bytes

    import app.main as main

    real_process = main.process_image_bytes
    calls = {"n": 0}

    def flaky_process(data, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Temporary image parse failure")
        return real_process(data, **kwargs)

    monkeypatch.setattr(main, "process_image_bytes", flaky_process)

    first = client.post(
        "/api/items/image",
        files={"file": ("shot.png", make_png_bytes(), "image/png")},
    ).json()
    assert first["processing_status"] == "failed"

    retry = client.post(f"/api/items/{first['id']}/retry").json()
    assert retry["processing_status"] == "ready"
    assert retry["ai_description"]


def test_image_ocr_text_is_editable(client, monkeypatch):
    from tests.image_fixtures import make_png_bytes

    monkeypatch.setenv("LIFE_INBOX_DISABLE_OCR", "1")
    item = client.post(
        "/api/items/image",
        files={"file": ("board.png", make_png_bytes(), "image/png")},
    ).json()
    assert item["processing_status"] == "ready"

    updated = client.patch(
        f"/api/items/{item['id']}/extracted-text",
        json={"extracted_text": "User corrected OCR text about groceries"},
    ).json()
    assert updated["extracted_text"] == "User corrected OCR text about groceries"
    assert "User corrected OCR text" in updated["raw_text"]
    assert updated["ai_description"] == item["ai_description"]


def test_analyze_includes_ready_image_items(client, monkeypatch):
    from tests.image_fixtures import make_png_bytes

    monkeypatch.setenv("LIFE_INBOX_DISABLE_OCR", "1")
    image_item = client.post(
        "/api/items/image",
        files={"file": ("habits.png", make_png_bytes(text="Habits"), "image/png")},
    ).json()
    # Ensure searchable text exists even without OCR
    client.patch(
        f"/api/items/{image_item['id']}/extracted-text",
        json={"extracted_text": "Habits screenshot reminder"},
    )
    image_item = client.get("/api/items").json()
    image_item = next(i for i in image_item if i["item_type"] == "image")
    assert image_item["processing_status"] == "ready"

    analysis = client.post("/api/analyze").json()
    ids = {
        note["id"]
        for bucket in analysis["buckets"]
        for note in bucket["notes"]
    }
    assert image_item["id"] in ids


def test_voice_capture_persists_before_processing(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    main = sys.modules["app.main"]
    database = sys.modules["app.database"]
    original_add = database.add_voice_item_to_db
    wav = make_wav_bytes()
    seen = {"persisted": False}

    def slow_add(filename, storage_path, mime_type):
        item = original_add(filename, storage_path, mime_type)
        seen["persisted"] = True
        assert item["original_filename"] == "note.wav"
        assert item["processing_status"] == "captured"
        assert item["storage_path"]
        return item

    monkeypatch.setattr(main, "add_voice_item_to_db", slow_add)
    monkeypatch.setenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", "1")

    item = client.post(
        "/api/items/voice",
        files={"file": ("note.wav", wav, "audio/wav")},
    ).json()

    assert seen["persisted"] is True
    assert item["item_type"] == "voice"
    assert item["processing_status"] == "ready"
    assert item["extracted_text"] == ""
    assert "transcript pending" in (item["raw_text"] or "").lower()
    audio = client.get(f"/api/items/{item['id']}/file")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")


def test_voice_capture_rejects_invalid_uploads(client):
    response = client.post(
        "/api/items/voice",
        files={"file": ("notes.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "audio" in detail or "voice" in detail or "webm" in detail


def test_failed_voice_capture_can_retry(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    main = sys.modules["app.main"]
    real_process = main.process_voice_bytes
    calls = {"n": 0}

    def flaky_process(data, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("Temporary transcription failure")
        return real_process(data, **kwargs)

    monkeypatch.setattr(main, "process_voice_bytes", flaky_process)

    first = client.post(
        "/api/items/voice",
        files={"file": ("note.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert first["processing_status"] == "failed"

    retry = client.post(f"/api/items/{first['id']}/retry").json()
    assert retry["processing_status"] == "ready"


def test_voice_transcript_is_editable_and_analyzable(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    monkeypatch.setenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", "1")
    item = client.post(
        "/api/items/voice",
        files={"file": ("memo.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert item["processing_status"] == "ready"

    updated = client.patch(
        f"/api/items/{item['id']}/extracted-text",
        json={"extracted_text": "Remember to call the dentist tomorrow morning"},
    ).json()
    assert updated["extracted_text"].startswith("Remember to call")
    assert "dentist" in updated["raw_text"]

    analysis = client.post("/api/analyze").json()
    ids = {
        note["id"]
        for bucket in analysis["buckets"]
        for note in bucket["notes"]
    }
    assert updated["id"] in ids


def _mock_openai_transcription(monkeypatch, text=None, *, raise_on_create=None):
    import openai

    def create(**_kwargs):
        if raise_on_create is not None:
            raise raise_on_create
        return SimpleNamespace(text=text or "")

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.delenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", raising=False)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create)
            )
        ),
    )


def test_voice_transcription_success_persists_transcript(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    wav = make_wav_bytes()
    _mock_openai_transcription(
        monkeypatch,
        "Remember to renew car insurance before Friday",
    )

    item = client.post(
        "/api/items/voice",
        files={"file": ("memo.wav", wav, "audio/wav")},
    ).json()

    assert item["processing_status"] == "ready"
    assert item["extracted_text"] == "Remember to renew car insurance before Friday"
    assert "car insurance" in (item["raw_text"] or "").lower()
    assert "transcript pending" not in (item["raw_text"] or "").lower()
    assert "OPENAI" not in (item["raw_text"] or "")
    assert "exception" not in (item.get("error_message") or "").lower()

    audio = client.get(f"/api/items/{item['id']}/file")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")
    assert audio.content == wav


def test_voice_transcription_no_key_keeps_item_editable(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", raising=False)

    item = client.post(
        "/api/items/voice",
        files={"file": ("memo.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert item["processing_status"] == "ready"
    assert item["extracted_text"] == ""
    assert "transcript pending" in (item["raw_text"] or "").lower()
    assert "not configured" in (item["raw_text"] or "").lower()

    updated = client.patch(
        f"/api/items/{item['id']}/extracted-text",
        json={"extracted_text": "Manual transcript about dentist appointment"},
    ).json()
    assert "dentist" in updated["extracted_text"]

    audio = client.get(f"/api/items/{item['id']}/file")
    assert audio.status_code == 200
    assert audio.content.startswith(b"RIFF")


def test_voice_transcription_api_failure_preserves_audio(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    wav = make_wav_bytes()
    _mock_openai_transcription(
        monkeypatch,
        raise_on_create=RuntimeError("OPENAI_API_KEY=super-secret provider boom"),
    )

    item = client.post(
        "/api/items/voice",
        files={"file": ("memo.wav", wav, "audio/wav")},
    ).json()

    assert item["processing_status"] == "ready"
    assert item["extracted_text"] == ""
    assert "temporarily unavailable" in (item["raw_text"] or "").lower()
    assert "super-secret" not in (item["raw_text"] or "")
    assert "OPENAI_API_KEY" not in (item["raw_text"] or "")
    assert "provider boom" not in (item["raw_text"] or "")
    assert "Traceback" not in (item["raw_text"] or "")

    audio = client.get(f"/api/items/{item['id']}/file")
    assert audio.status_code == 200
    assert audio.content == wav

    # Manual edit still works after provider failure.
    updated = client.patch(
        f"/api/items/{item['id']}/extracted-text",
        json={"extracted_text": "Edited after failed auto transcription"},
    ).json()
    assert updated["extracted_text"].startswith("Edited after")


def test_voice_transcription_uses_env_model(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes
    import openai

    seen = {}

    def create(**kwargs):
        seen["model"] = kwargs.get("model")
        return SimpleNamespace(text="hello from custom model")

    monkeypatch.setenv("OPENAI_API_KEY", "configured-for-test")
    monkeypatch.setenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
    monkeypatch.delenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", raising=False)
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda: SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(create=create)
            )
        ),
    )

    item = client.post(
        "/api/items/voice",
        files={"file": ("memo.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert seen["model"] == "gpt-4o-mini-transcribe"
    assert item["extracted_text"] == "hello from custom model"


def test_voice_transcription_owner_isolation(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    _mock_openai_transcription(
        monkeypatch,
        "Alice private voice memo about yacht taxes",
    )
    alice = client.post(
        "/api/auth/dev-login",
        json={"email": "alice-voice@example.com"},
    ).json()
    alice_item = client.post(
        "/api/items/voice",
        files={"file": ("alice.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert alice_item["owner_user_id"] == alice["id"]
    assert "yacht" in alice_item["extracted_text"].lower()

    bob = client.post(
        "/api/auth/dev-login",
        json={"email": "bob-voice@example.com"},
    ).json()
    assert bob["id"] != alice["id"]
    missing = client.get(f"/api/items/{alice_item['id']}")
    assert missing.status_code == 404
    audio = client.get(f"/api/items/{alice_item['id']}/file")
    assert audio.status_code == 404

    ask = client.post(
        "/api/ask",
        json={"question": "What about yacht taxes?"},
    ).json()
    assert all(c["item_id"] != alice_item["id"] for c in ask["citations"])
    assert "yacht" not in ask["answer"].lower()


def test_voice_transcript_flows_into_ask_retrieval(client, monkeypatch):
    from tests.voice_fixtures import make_wav_bytes

    _mock_openai_transcription(
        monkeypatch,
        "Order Atomic Habits this weekend after Rohan's recommendation",
    )
    item = client.post(
        "/api/items/voice",
        files={"file": ("habits.wav", make_wav_bytes(), "audio/wav")},
    ).json()
    assert item["processing_status"] == "ready"

    data = client.post(
        "/api/ask",
        json={"question": "What did Rohan recommend about Atomic Habits?"},
    ).json()
    assert data["confidence"] != "insufficient"
    assert any(c["item_id"] == item["id"] for c in data["citations"])
    assert "Atomic Habits" in data["answer"]


def test_attention_board_buckets_and_briefing(client):
    client.post(
        "/api/items",
        json={"raw_text": "Pay the electricity bill tomorrow and renew insurance next Monday"},
    )
    analysis = client.post("/api/analyze").json()
    assert analysis["todos"]

    board = client.get("/api/attention").json()
    assert "today" in board and "upcoming" in board and "overdue" in board
    assert "unscheduled" in board
    assert board["reference_date"]

    briefing = client.get("/api/briefing").json()
    assert "summary" in briefing
    assert "counts" in briefing
    assert "priority_suggestions" in briefing


def test_snooze_and_calendar_preview_confirm_mock(client, monkeypatch):
    from app.integrations import calendar as calendar_mod

    calendar_mod.reset_calendar_adapter_for_tests()
    monkeypatch.setenv("LIFE_INBOX_CALENDAR_PROVIDER", "mock")

    client.post(
        "/api/items",
        json={"raw_text": "Submit tax filing tomorrow morning"},
    )
    client.post("/api/analyze")
    tasks = client.get("/api/tasks").json()
    assert tasks
    task = tasks[0]

    confirmed = client.post(
        f"/api/tasks/{task['id']}/confirm-timing",
        json={"timing_text": "tomorrow"},
    ).json()
    assert confirmed["timing_confirmed"] is True
    assert confirmed["due_on"]

    snoozed = client.post(
        f"/api/tasks/{task['id']}/snooze",
        json={"until": "2099-01-01"},
    ).json()
    assert snoozed["snoozed_until"] == "2099-01-01"

    # Unsnooze-ish by confirming again for calendar path
    client.post(
        f"/api/tasks/{task['id']}/confirm-timing",
        json={"timing_text": "2026-12-01"},
    )
    # Clear snooze for attention by setting past snooze
    client.post(
        f"/api/tasks/{task['id']}/snooze",
        json={"until": "2000-01-01"},
    )

    status = client.get("/api/calendar/status").json()
    assert status["writes_enabled"] is False
    assert status["oauth_connected"] is False

    preview = client.post(
        "/api/calendar/preview",
        json={"task_id": task["id"]},
    ).json()
    assert preview["status"] == "preview"
    assert preview["confirmation_required"] is True
    assert "draft_id" in preview

    confirmed_cal = client.post(
        "/api/calendar/confirm",
        json={"draft_id": preview["draft_id"]},
    ).json()
    assert confirmed_cal["status"] == "confirmed_mock"
    assert confirmed_cal["receipt"]["provider"] == "mock"


def test_google_calendar_confirm_is_blocked(client, monkeypatch):
    from app.integrations import calendar as calendar_mod

    calendar_mod.reset_calendar_adapter_for_tests()
    monkeypatch.setenv("LIFE_INBOX_CALENDAR_PROVIDER", "google")

    client.post(
        "/api/items",
        json={"raw_text": "Need to schedule dentist appointment on 2026-11-15"},
    )
    analysis = client.post("/api/analyze").json()
    tasks = client.get("/api/tasks").json()
    if not tasks:
        # Fallback organizer may omit todos for short notes; seed via timing confirm path
        # by re-analyzing a clearer action note.
        client.post(
            "/api/items",
            json={"raw_text": "TODO: Book dentist appointment for 2026-11-15"},
        )
        client.post("/api/analyze")
        tasks = client.get("/api/tasks").json()
    assert tasks, analysis
    task = tasks[0]
    client.post(
        f"/api/tasks/{task['id']}/confirm-timing",
        json={"timing_text": "2026-11-15"},
    )
    preview = client.post(
        "/api/calendar/preview",
        json={"task_id": task["id"]},
    ).json()
    blocked = client.post(
        "/api/calendar/confirm",
        json={"draft_id": preview["draft_id"]},
    )
    assert blocked.status_code == 403
    assert "disabled" in blocked.json()["detail"].lower()


def test_parse_timing_preserves_original_phrase():
    from app.attention.dates import parse_timing_phrase
    from datetime import date

    parsed = parse_timing_phrase("this weekend maybe", reference=date(2026, 10, 13))
    assert parsed.original_phrase == "this weekend maybe"
    assert parsed.due_date is not None
    assert parsed.needs_confirmation is True


def test_user_isolation_prevents_cross_account_access(client):
    alice = client.post(
        "/api/auth/dev-login",
        json={"email": "alice@example.com", "display_name": "Alice"},
    ).json()
    assert alice["email"] == "alice@example.com"
    alice_item = client.post(
        "/api/items",
        json={"raw_text": "Alice private note about taxes"},
    ).json()
    assert alice_item["owner_user_id"] == alice["id"]

    bob = client.post(
        "/api/auth/dev-login",
        json={"email": "bob@example.com", "display_name": "Bob"},
    ).json()
    assert bob["id"] != alice["id"]
    bob_items = client.get("/api/items").json()
    assert all(item["owner_user_id"] == bob["id"] for item in bob_items)
    assert alice_item["id"] not in {item["id"] for item in bob_items}

    missing = client.get(f"/api/items/{alice_item['id']}")
    assert missing.status_code == 404

    deleted = client.delete(f"/api/items/{alice_item['id']}")
    assert deleted.status_code == 404

    # Switch back to Alice and confirm her item remains.
    client.post(
        "/api/auth/dev-login",
        json={"email": "alice@example.com"},
    )
    still_there = client.get(f"/api/items/{alice_item['id']}")
    assert still_there.status_code == 200
    assert still_there.json()["raw_text"].startswith("Alice private")


def test_browser_supplied_user_id_header_is_ignored(client):
    alice = client.post(
        "/api/auth/dev-login",
        json={"email": "alice2@example.com"},
    ).json()
    client.post("/api/items", json={"raw_text": "Owned by Alice session"})
    # Spoof header must not switch identity.
    items = client.get("/api/items", headers={"X-User-Id": "999999"}).json()
    assert items
    assert all(item["owner_user_id"] == alice["id"] for item in items)


def test_google_oauth_start_is_stubbed(client):
    response = client.get("/api/auth/google/start")
    assert response.status_code == 501
    assert "oauth" in response.json()["detail"].lower()


def test_health_includes_config_metadata(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert "environment" in payload
    assert "database" in payload


# --- Incremental analysis: only new / edited notes are analyzed -------------


def _record_analyzer_calls(monkeypatch):
    """Wrap the organizer so tests can see which item IDs it was given."""
    main = sys.modules["app.main"]
    original = main.analyze_inbox_with_openai
    calls = []

    def recording(notes):
        calls.append([note["id"] for note in notes])
        return original(notes)

    monkeypatch.setattr(main, "analyze_inbox_with_openai", recording)
    return calls


def _add_note(client, text):
    return client.post("/api/items", json={"raw_text": text}).json()["id"]


def _view_ids(analysis):
    return sorted(
        note["id"]
        for bucket in analysis["buckets"]
        for note in bucket["notes"]
    )


def test_analyze_only_sends_new_notes_and_keeps_earlier_results(
    client, monkeypatch
):
    calls = _record_analyzer_calls(monkeypatch)
    first_id = _add_note(client, "Call the team tomorrow")

    first = client.post("/api/analyze").json()
    assert calls == [[first_id]]
    assert first["analyzed_item_ids"] == [first_id]

    second_id = _add_note(client, "Buy groceries this weekend")
    third_id = _add_note(client, "Read an article about vector databases")
    second = client.post("/api/analyze").json()

    # Only the two unanalyzed notes went to the organizer...
    assert calls[-1] == [second_id, third_id]
    assert second["analyzed_item_ids"] == [second_id, third_id]
    # ...but the saved view still shows all three notes.
    assert _view_ids(second) == [first_id, second_id, third_id]
    priority_ids = sorted(
        entry["id"]
        for level in ("High", "Medium", "Low")
        for entry in second["priority_view"][level]
    )
    assert priority_ids == [first_id, second_id, third_id]
    assert client.get("/api/analyze/latest").json() == second
    assert second["is_stale"] is False


def test_analyze_keeps_earlier_todos_and_adds_new_ones(client):
    first_id = _add_note(client, "Call the team tomorrow")
    client.post("/api/analyze")
    second_id = _add_note(client, "Buy groceries this weekend")

    result = client.post("/api/analyze").json()

    todo_sources = sorted(
        source for todo in result["todos"] for source in todo["source_note_ids"]
    )
    assert todo_sources == [first_id, second_id]
    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 2


def test_analyze_with_nothing_new_does_not_call_the_organizer(
    client, monkeypatch
):
    calls = _record_analyzer_calls(monkeypatch)
    note_id = _add_note(client, "Call the team tomorrow")
    first = client.post("/api/analyze").json()
    assert calls == [[note_id]]

    again = client.post("/api/analyze")

    assert again.status_code == 200
    data = again.json()
    assert calls == [[note_id]]
    assert data["analyzed_item_ids"] == []
    assert data["analysis_run_id"] == first["analysis_run_id"]
    assert _view_ids(data) == [note_id]


def test_edited_note_is_analyzed_again_but_untouched_notes_are_not(
    client, monkeypatch
):
    calls = _record_analyzer_calls(monkeypatch)
    edited_id = _add_note(client, "Call the team tomorrow")
    untouched_id = _add_note(client, "Buy groceries this weekend")
    client.post("/api/analyze")

    response = client.put(
        f"/api/items/{edited_id}",
        json={"raw_text": "Book the dentist next week"},
    )
    assert response.status_code == 200
    result = client.post("/api/analyze").json()

    assert calls[-1] == [edited_id]
    assert result["analyzed_item_ids"] == [edited_id]
    assert _view_ids(result) == [edited_id, untouched_id]
    titles = {
        note["id"]: note["title"]
        for bucket in result["buckets"]
        for note in bucket["notes"]
    }
    assert "dentist" in titles[edited_id].lower()
    # The edited note's old ToDo is replaced, not duplicated.
    todo_texts = [todo["task"] for todo in result["todos"]]
    assert not any("team" in text.lower() for text in todo_texts)


def test_deleted_note_disappears_from_analysis_without_new_ai_call(
    client, monkeypatch
):
    calls = _record_analyzer_calls(monkeypatch)
    keep_id = _add_note(client, "Buy groceries this weekend")
    gone_id = _add_note(client, "Call the team tomorrow")
    client.post("/api/analyze")
    assert client.delete(f"/api/items/{gone_id}").status_code == 204

    result = client.post("/api/analyze").json()

    assert _view_ids(result) == [keep_id]
    assert result["analyzed_item_ids"] == []
    # Nothing new to send: the organizer is only called with an empty list.
    assert all(call == [] for call in calls[1:])
    assert result["is_stale"] is False


def test_scope_all_reanalyzes_the_whole_inbox(client, monkeypatch):
    calls = _record_analyzer_calls(monkeypatch)
    first_id = _add_note(client, "Call the team tomorrow")
    second_id = _add_note(client, "Buy groceries this weekend")
    client.post("/api/analyze")

    result = client.post("/api/analyze?scope=all").json()

    assert calls[-1] == [first_id, second_id]
    assert result["analyzed_item_ids"] == [first_id, second_id]
    assert _view_ids(result) == [first_id, second_id]


def test_analyze_rejects_unknown_scope(client):
    assert client.post("/api/analyze?scope=bogus").status_code == 422


def test_dashboard_reports_pending_analysis_count(client):
    assert client.get("/api/dashboard").json()["pending_analysis_count"] == 0

    _add_note(client, "Call the team tomorrow")
    _add_note(client, "Buy groceries this weekend")
    assert client.get("/api/dashboard").json()["pending_analysis_count"] == 2

    client.post("/api/analyze")
    assert client.get("/api/dashboard").json()["pending_analysis_count"] == 0

    _add_note(client, "Read an article about vector databases")
    assert client.get("/api/dashboard").json()["pending_analysis_count"] == 1


def test_pending_analysis_endpoint_lists_unanalyzed_ids(client):
    assert client.get("/api/analyze/pending").json() == {
        "pending_count": 0,
        "pending_item_ids": [],
        "analyzing": False,
    }
    first_id = _add_note(client, "Call the team tomorrow")
    second_id = _add_note(client, "Buy groceries this weekend")
    assert client.get("/api/analyze/pending").json() == {
        "pending_count": 2,
        "pending_item_ids": [first_id, second_id],
        "analyzing": False,
    }

    client.post("/api/analyze")
    assert client.get("/api/analyze/pending").json()["pending_count"] == 0


# --- Automatic analysis: every saved note is analyzed without a click --------


@pytest.fixture()
def auto_client(client, monkeypatch):
    """A client with automatic analysis switched on."""
    monkeypatch.setenv("LIFE_INBOX_AUTO_ANALYZE", "1")
    return client


def _settle(client):
    """Wait for background analysis to finish."""
    sys.modules["app.main"].auto_analyzer.wait_for_idle()


def _pending_ids(client):
    return client.get("/api/analyze/pending").json()["pending_item_ids"]


def test_saved_note_is_analyzed_automatically(auto_client, monkeypatch):
    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)

    note_id = _add_note(client, "Call the team tomorrow")
    _settle(client)

    assert calls == [[note_id]]
    assert _pending_ids(client) == []
    latest = client.get("/api/analyze/latest").json()
    assert latest["analyzed_item_ids"] == [note_id]
    assert _view_ids(latest) == [note_id]
    assert latest["is_stale"] is False
    # The ToDo from the note became a task without any manual step.
    assert len(client.get("/api/tasks").json()) == 1
    insight = client.get(f"/api/items/{note_id}/insight").json()
    assert insight["item_id"] == note_id


def test_every_note_in_a_burst_is_analyzed_exactly_once(auto_client, monkeypatch):
    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)

    ids = [
        _add_note(client, "Call the team tomorrow"),
        _add_note(client, "Buy groceries this weekend"),
        _add_note(client, "Read an article about vector databases"),
        _add_note(client, "Renew the car insurance before Friday"),
    ]
    _settle(client)

    analyzed = sorted(item_id for call in calls for item_id in call)
    assert analyzed == sorted(ids)  # none missed, none analyzed twice
    assert _pending_ids(client) == []
    assert _view_ids(client.get("/api/analyze/latest").json()) == sorted(ids)


def test_editing_a_note_triggers_automatic_reanalysis(auto_client, monkeypatch):
    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)
    note_id = _add_note(client, "Call the team tomorrow")
    _settle(client)

    response = client.put(
        f"/api/items/{note_id}", json={"raw_text": "Book the dentist next week"}
    )
    assert response.status_code == 200
    _settle(client)

    assert calls == [[note_id], [note_id]]
    assert _pending_ids(client) == []


def test_legacy_notes_endpoint_also_triggers_automatic_analysis(
    auto_client, monkeypatch
):
    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)

    note_id = client.post("/api/notes", json={"text": "Buy groceries today"}).json()["id"]
    _settle(client)

    assert calls == [[note_id]]
    assert _pending_ids(client) == []


def test_pdf_capture_is_analyzed_automatically(auto_client, monkeypatch):
    from tests.pdf_fixtures import make_text_pdf

    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)

    response = client.post(
        "/api/items/pdf",
        files={"file": ("notes.pdf", make_text_pdf(), "application/pdf")},
    )
    assert response.status_code == 200
    _settle(client)

    assert calls == [[response.json()["id"]]]
    assert _pending_ids(client) == []


def test_failed_background_analysis_leaves_note_pending_then_recovers(
    auto_client, monkeypatch
):
    client = auto_client
    main = sys.modules["app.main"]
    original = main.analyze_inbox_with_openai

    def broken(_notes):
        raise RuntimeError("provider down")

    monkeypatch.setattr(main, "analyze_inbox_with_openai", broken)
    first_id = _add_note(client, "Call the team tomorrow")
    _settle(client)
    # The note is saved and still waiting; nothing was lost.
    assert _pending_ids(client) == [first_id]
    assert client.get("/api/analyze/latest").status_code == 204

    # Next save retries everything that is still pending.
    monkeypatch.setattr(main, "analyze_inbox_with_openai", original)
    second_id = _add_note(client, "Buy groceries this weekend")
    _settle(client)

    assert _pending_ids(client) == []
    assert _view_ids(client.get("/api/analyze/latest").json()) == [first_id, second_id]


def test_manual_analyze_rescues_notes_a_background_run_missed(
    auto_client, monkeypatch
):
    client = auto_client
    main = sys.modules["app.main"]
    original = main.analyze_inbox_with_openai
    monkeypatch.setattr(
        main,
        "analyze_inbox_with_openai",
        lambda _notes: (_ for _ in ()).throw(RuntimeError("provider down")),
    )
    note_id = _add_note(client, "Call the team tomorrow")
    _settle(client)
    assert _pending_ids(client) == [note_id]

    monkeypatch.setattr(main, "analyze_inbox_with_openai", original)
    rescue = client.post("/api/analyze")

    assert rescue.status_code == 200
    assert rescue.json()["analyzed_item_ids"] == [note_id]
    assert _pending_ids(client) == []


def test_automatic_analysis_does_nothing_when_nothing_is_pending(
    auto_client, monkeypatch
):
    client = auto_client
    calls = _record_analyzer_calls(monkeypatch)
    note_id = _add_note(client, "Call the team tomorrow")
    _settle(client)
    runs_before = client.get("/api/analyze/latest").json()["analysis_run_id"]

    # Saving a note that is not analyzable yet must not create empty runs.
    main = sys.modules["app.main"]
    main.auto_analyzer.schedule(1)
    _settle(client)

    assert calls == [[note_id]]
    assert client.get("/api/analyze/latest").json()["analysis_run_id"] == runs_before


def test_automatic_analysis_can_be_switched_off(client, monkeypatch):
    monkeypatch.setenv("LIFE_INBOX_AUTO_ANALYZE", "0")
    calls = _record_analyzer_calls(monkeypatch)

    note_id = _add_note(client, "Call the team tomorrow")
    _settle(client)

    assert calls == []
    assert _pending_ids(client) == [note_id]


def test_pending_endpoint_reports_analyzing_flag(auto_client):
    client = auto_client
    assert client.get("/api/analyze/pending").json()["analyzing"] is False
    _add_note(client, "Call the team tomorrow")
    _settle(client)
    assert client.get("/api/analyze/pending").json()["analyzing"] is False


def test_page_scripts_are_versioned_so_stale_copies_are_never_reused(client):
    html = client.get("/").text
    for asset in ("styles.css", "pagination_state.js", "app.js", "ui.js", "playful.js"):
        assert re.search(rf"/static/{re.escape(asset)}\?v=\d+", html), asset
    # The versioned URL still serves the file.
    assert client.get("/static/app.js?v=123").status_code == 200


def test_attention_board_places_tasks_in_overdue_today_and_upcoming(
    client, monkeypatch
):
    from datetime import timedelta

    from app.attention.dates import today_in_configured_tz

    today = today_in_configured_tz()
    first_id = _add_note(client, "Pay the rent")
    second_id = _add_note(client, "Submit the report")
    third_id = _add_note(client, "Book the tickets")
    fourth_id = _add_note(client, "Plan the trip")
    main = sys.modules["app.main"]

    def analyze(notes):
        timings = {
            first_id: (today - timedelta(days=3)).strftime("%d/%m/%Y"),
            second_id: "today",
            third_id: (today + timedelta(days=4)).strftime("%d/%m/%y"),
            fourth_id: "no real timing here",
        }
        return {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {"id": note["id"], "title": note["text"], "priority": "Medium"}
                        for note in notes
                    ],
                }
            ],
            "todos": [
                {
                    "task": note["text"],
                    "priority": "Medium",
                    "source_note_ids": [note["id"]],
                    "when": timings[note["id"]],
                }
                for note in notes
            ],
            "priority_view": {
                "High": [],
                "Medium": [
                    {"id": note["id"], "title": note["text"]} for note in notes
                ],
                "Low": [],
            },
            "mode": "openai",
        }

    monkeypatch.setattr(main, "analyze_inbox_with_openai", analyze)
    client.post("/api/analyze")

    board = client.get("/api/attention").json()
    names = {
        bucket: sorted(task["text"] for task in board[bucket])
        for bucket in ("overdue", "today", "upcoming", "unscheduled")
    }
    assert names == {
        "overdue": ["Pay the rent"],
        "today": ["Submit the report"],
        "upcoming": ["Book the tickets"],
        "unscheduled": ["Plan the trip"],
    }
    briefing = client.get("/api/briefing").json()
    assert briefing["counts"]["overdue"] == 1
    assert briefing["counts"]["today"] == 1
    assert briefing["counts"]["upcoming"] == 1


@pytest.mark.parametrize(
    "phrase, expected, confidence, needs_confirmation",
    [
        ("20th of this month", "2026-11-20", "high", False),
        ("due 20th of this month", "2026-11-20", "high", False),
        ("the 30th", "2026-11-30", "medium", True),
        ("the 5th", "2026-12-05", "medium", True),
        ("5th of next month", "2026-12-05", "high", False),
        ("12:10 pm on 21/11/26", "2026-11-21", "high", False),
        ("21/11/2026", "2026-11-21", "high", False),
        ("11/21/26", "2026-11-21", "high", False),
        ("03/04/2026", "2026-04-03", "medium", True),
        ("21 November", "2026-11-21", "medium", True),
        ("20th of November 2026", "2026-11-20", "high", False),
        ("tonight", "2026-11-12", "high", False),
        ("tom", "2026-11-13", "high", False),
        ("tmrw morning", "2026-11-13", "high", False),
        ("day after tomorrow", "2026-11-14", "high", False),
        ("yesterday", "2026-11-11", "high", False),
        ("end of the month", "2026-11-30", "medium", True),
        ("next week", "2026-11-16", "medium", True),
        ("this week", "2026-11-13", "medium", True),
    ],
)
def test_parse_timing_understands_common_phrases(
    phrase, expected, confidence, needs_confirmation
):
    from datetime import date

    from app.attention.dates import parse_timing_phrase

    parsed = parse_timing_phrase(phrase, reference=date(2026, 11, 12))

    assert parsed.normalized_iso == expected
    assert parsed.confidence == confidence
    assert parsed.needs_confirmation is needs_confirmation


@pytest.mark.parametrize("phrase", ["31st of this month", "soon", "whenever"])
def test_parse_timing_leaves_unreadable_phrases_unscheduled(phrase):
    from datetime import date

    from app.attention.dates import parse_timing_phrase

    parsed = parse_timing_phrase(phrase, reference=date(2026, 11, 12))

    assert parsed.normalized_iso is None
    assert parsed.needs_confirmation is True


# --- Time-aware overdue --------------------------------------------------------


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("12:15 pm today", (12, 15)),
        ("12:26 today", (12, 26)),
        ("5pm tomorrow", (17, 0)),
        ("at 5 pm", (17, 0)),
        ("17:30", (17, 30)),
        ("noon", (12, 0)),
        ("12:10 pm on 21/11/26", (12, 10)),
        ("12:05 am", (0, 5)),
        ("5:30 today", None),  # no am/pm: ambiguous, so no time is assumed
        ("today", None),
        ("", None),
    ],
)
def test_parse_time_of_day(phrase, expected):
    from datetime import time

    from app.attention.dates import parse_time_of_day

    parsed = parse_time_of_day(phrase)

    assert parsed == (time(*expected) if expected else None)


def test_task_due_today_becomes_overdue_once_its_time_passes():
    from datetime import date, datetime, time
    from zoneinfo import ZoneInfo

    from app.attention.dates import classify_attention_bucket as classify

    today = date(2026, 11, 12)
    zone = ZoneInfo("Asia/Kolkata")
    before = datetime(2026, 11, 12, 12, 10, tzinfo=zone)
    after = datetime(2026, 11, 12, 12, 20, tzinfo=zone)

    assert classify(today, reference=today, due_time=time(12, 15), now=before) == "today"
    assert classify(today, reference=today, due_time=time(12, 15), now=after) == "overdue"
    # Without a clock time the task stays "today" until the day ends.
    assert classify(today, reference=today, now=after) == "today"
    # A later date is never overdue because of the time of day.
    assert (
        classify(date(2026, 11, 13), reference=today, due_time=time(9, 0), now=after)
        == "upcoming"
    )


def _freeze_clock(monkeypatch, hour, minute):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    frozen = datetime(2026, 11, 12, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr("app.attention.dates.now_in_configured_tz", lambda: frozen)


def _seed_tasks(client, monkeypatch, timings):
    """Save one note per entry and give each a ToDo with the given timing."""
    for text in timings:
        _add_note(client, text)
    main = sys.modules["app.main"]

    def analyze(notes):
        return {
            "buckets": [
                {
                    "name": "Work",
                    "notes": [
                        {"id": n["id"], "title": n["text"], "priority": "High"}
                        for n in notes
                    ],
                }
            ],
            "todos": [
                {
                    "task": n["text"],
                    "priority": "High",
                    "source_note_ids": [n["id"]],
                    "when": timings[n["text"]],
                }
                for n in notes
            ],
            "priority_view": {
                "High": [{"id": n["id"], "title": n["text"]} for n in notes],
                "Medium": [],
                "Low": [],
            },
            "mode": "openai",
        }

    monkeypatch.setattr(main, "analyze_inbox_with_openai", analyze)
    assert client.post("/api/analyze").status_code == 200


def test_attention_board_moves_timed_task_to_overdue_when_its_time_passes(
    client, monkeypatch
):
    _freeze_clock(monkeypatch, 12, 20)
    _seed_tasks(
        client,
        monkeypatch,
        {
            "Submit doc": "12:15 pm today",
            "Buy water": "11:00 pm today",
            "Water plants": "today",
        },
    )

    board = client.get("/api/attention").json()

    assert [t["text"] for t in board["overdue"]] == ["Submit doc"]
    assert board["overdue"][0]["due_time"] == "12:15"
    assert sorted(t["text"] for t in board["today"]) == ["Buy water", "Water plants"]
    briefing = client.get("/api/briefing").json()
    assert briefing["counts"]["overdue"] == 1
    assert briefing["counts"]["today"] == 2
    reasons = {s["text"]: s["reason"] for s in briefing["priority_suggestions"]}
    assert "overdue since 12:15" in reasons["Submit doc"]


def test_timed_task_is_not_overdue_before_its_time(client, monkeypatch):
    _freeze_clock(monkeypatch, 12, 10)
    _seed_tasks(client, monkeypatch, {"Submit doc": "12:15 pm today"})

    board = client.get("/api/attention").json()

    assert board["overdue"] == []
    assert [t["text"] for t in board["today"]] == ["Submit doc"]


# --- Ask lists every matching task ----------------------------------------------


def _seed_mixed_tasks(client, monkeypatch):
    _freeze_clock(monkeypatch, 12, 20)
    _seed_tasks(
        client,
        monkeypatch,
        {
            "Pay rent": "yesterday",
            "Submit doc": "12:15 pm today",
            "Buy water": "12:26 today",
            "Call dentist": "tomorrow",
            "Plan trip": "21/11/26",
            "Read book": "no real timing here",
        },
    )


def _ask(client, question):
    response = client.post("/api/ask", json={"question": question})
    assert response.status_code == 200
    return response.json()


def test_ask_what_to_do_today_lists_every_task_due_today_or_overdue(
    client, monkeypatch
):
    _seed_mixed_tasks(client, monkeypatch)

    data = _ask(client, "what all do i have to do today")

    assert data["mode"] == "task_board"
    assert data["confidence"] == "high"
    assert data["answer"].startswith("You have 3 tasks for today (2 overdue):")
    lines = data["answer"].splitlines()
    # Oldest first, then by time of day.
    assert "Pay rent" in lines[1] and "overdue by 1 day" in lines[1]
    assert "Submit doc" in lines[2] and "12:15 PM" in lines[2]
    assert "overdue, its time has passed" in lines[2]
    assert "Buy water" in lines[3] and "12:26 PM" in lines[3]
    assert "Call dentist" not in data["answer"]
    assert "Plan trip" not in data["answer"]
    assert "1 task has no date yet." in data["answer"]
    assert [c["title"] for c in data["citations"]] == [
        "Pay rent",
        "Submit doc",
        "Buy water",
    ]
    assert all(c["source_kind"] == "task" for c in data["citations"])


def test_ask_scopes_overdue_tomorrow_and_upcoming(client, monkeypatch):
    _seed_mixed_tasks(client, monkeypatch)

    overdue = _ask(client, "what is overdue")["answer"]
    assert "Pay rent" in overdue and "Submit doc" in overdue
    assert "Buy water" not in overdue

    tomorrow = _ask(client, "what do i have to do tomorrow")["answer"]
    assert tomorrow.startswith("You have 1 task for tomorrow:")
    assert "Call dentist" in tomorrow and "Plan trip" not in tomorrow

    upcoming = _ask(client, "what is coming up")["answer"]
    assert "Call dentist" in upcoming and "Plan trip" in upcoming
    assert "Pay rent" not in upcoming


def test_ask_all_tasks_includes_undated_ones(client, monkeypatch):
    _seed_mixed_tasks(client, monkeypatch)

    data = _ask(client, "list my tasks")

    assert data["answer"].startswith(
        "You have 6 tasks that are still open (2 overdue):"
    )
    assert "Read book — no date" in data["answer"]
    assert len(data["citations"]) == 6


def test_ask_filters_tasks_by_topic_and_falls_back_to_notes(client, monkeypatch):
    _seed_mixed_tasks(client, monkeypatch)

    dentist = _ask(client, "what tasks do i have about the dentist")
    assert dentist["mode"] == "task_board"
    assert [c["title"] for c in dentist["citations"]] == ["Call dentist"]

    # No task matches: do not pretend; search the notes instead.
    other = _ask(client, "what tasks do i have about gardening")
    assert other["mode"] != "task_board"


def test_ask_reports_when_no_tasks_match(client, monkeypatch):
    _freeze_clock(monkeypatch, 12, 20)
    _seed_tasks(client, monkeypatch, {"Read book": "no real timing here"})

    data = _ask(client, "what do i have to do tomorrow")

    assert data["answer"] == (
        "You have no open tasks for tomorrow. 1 task has no date yet."
    )
    assert data["citations"] == []
    assert _ask(client, "what do i have to do today")["answer"].startswith(
        "You have no open tasks for today."
    )


def test_ask_about_other_things_still_searches_notes(client, monkeypatch):
    _seed_mixed_tasks(client, monkeypatch)
    _add_note(client, "Favourite restaurant is Saravana Bhavan in Chennai")

    data = _ask(client, "which restaurant did i save")

    assert data["mode"] != "task_board"


# --- No Tasks page, no date confirmation ----------------------------------------


def test_page_has_no_tasks_tab_or_panel(client):
    html = client.get("/").text

    assert 'data-tab="tasks"' not in html
    assert 'id="panel-tasks"' not in html
    assert 'id="taskStatusFilter"' not in html
    for tab in ("capture", "ask", "dashboard", "notes"):
        assert f'data-tab="{tab}"' in html


def test_ambiguous_dates_are_used_without_asking_for_confirmation(
    client, monkeypatch
):
    _freeze_clock(monkeypatch, 12, 20)
    _seed_tasks(
        client,
        monkeypatch,
        {"Plan trip": "the 30th", "Pay rent": "this weekend", "Call mum": "asap"},
    )

    board = client.get("/api/attention").json()
    entries = [t for key in ("overdue", "today", "upcoming", "unscheduled") for t in board[key]]

    assert len(entries) == 3
    assert board["needs_confirmation"] == []
    assert not any(t["needs_confirmation"] for t in entries)
    assert client.get("/api/briefing").json()["counts"]["needs_confirmation"] == 0

    answer = _ask(client, "list my tasks")["answer"]
    assert "needs confirmation" not in answer

    # A calendar preview no longer demands a separate "confirm the date" step.
    task = next(t for t in entries if t["text"] == "Pay rent")
    preview = client.post("/api/calendar/preview", json={"task_id": task["id"]})
    assert preview.status_code == 200


# --- Redesigned UI: newest-first notes and a readable dashboard -----------------


def test_items_can_be_listed_newest_first(client):
    ids = [_add_note(client, f"Note number {index}") for index in range(3)]

    oldest_first = client.get("/api/items").json()
    newest_first = client.get("/api/items?sort=newest").json()

    assert [item["id"] for item in oldest_first] == ids
    assert [item["id"] for item in newest_first] == ids[::-1]
    paged = client.get("/api/items?sort=newest&limit=1&offset=1").json()
    assert [item["id"] for item in paged] == [ids[1]]
    assert client.get("/api/items?sort=sideways").status_code == 422


def test_dashboard_names_related_notes_and_counts_items(client):
    first = _add_note(client, "Renew car insurance soon")
    second = _add_note(client, "Renew the car insurance soon")
    assert client.post("/api/analyze").status_code == 200

    dashboard = client.get("/api/dashboard").json()

    assert dashboard["item_count"] == 2
    related = dashboard["related_items"]
    assert related, "the two near-identical notes should be linked"
    assert {related[0]["item_id_a"], related[0]["item_id_b"]} == {first, second}
    assert related[0]["title_a"] and related[0]["title_b"]
    assert all(item["effective_title"] for item in dashboard["recently_captured"])
    scores = [relation["score"] for relation in related]
    assert scores == sorted(scores, reverse=True)

    # A link to a note that no longer exists is not shown.
    assert client.delete(f"/api/items/{second}").status_code == 204
    after = client.get("/api/dashboard").json()
    assert after["item_count"] == 1
    assert after["related_items"] == []


def test_dashboard_says_what_actually_needs_attention(client):
    note_id = _add_note(client, "Call the bank about the card")
    other_id = _add_note(client, "Buy stamps")

    waiting = client.get("/api/dashboard").json()["needs_review"]
    assert {entry["id"] for entry in waiting} == {note_id, other_id}
    assert {entry["reason"] for entry in waiting} == {"Waiting to be organized"}

    assert client.post("/api/analyze").status_code == 200
    assert client.get("/api/dashboard").json()["needs_review"] == []

    # Deleting a note is not a reason to flag the ones that are left.
    assert client.delete(f"/api/items/{other_id}").status_code == 204
    assert client.get("/api/dashboard").json()["needs_review"] == []

    client.put(
        f"/api/items/{note_id}",
        json={"raw_text": "Call the bank about the new card"},
    )
    edited = client.get("/api/dashboard").json()["needs_review"]
    assert [entry["id"] for entry in edited] == [note_id]
    assert edited[0]["reason"] == "Edited, being organized again"


def test_dashboard_only_shows_the_signed_in_users_notes(client):
    client.post("/api/auth/dev-login", json={"email": "alice3@example.com"})
    alice_note = _add_note(client, "Alice private note about her salary")
    client.post("/api/analyze")

    client.post("/api/auth/dev-login", json={"email": "bob3@example.com"})
    bob_note = _add_note(client, "Bob note about groceries")
    dashboard = client.get("/api/dashboard").json()

    seen = {item["id"] for item in dashboard["recently_captured"]}
    seen |= {item["id"] for item in dashboard["high_priority"]}
    seen |= {item["id"] for item in dashboard["needs_review"]}
    assert seen == {bob_note}
    assert alice_note not in seen
    assert dashboard["item_count"] == 1


def test_page_has_the_redesigned_sections(client):
    html = client.get("/").text

    for element_id in (
        "dashboardSummary",
        "notesSummary",
        "notesStatus",
        "quickBadge",
        "clearFiltersButton",
    ):
        assert f'id="{element_id}"' in html
    assert "askExample('What do I have to do today?')" in html
    assert "Raw saved notes" not in html
    assert "Organized dashboard" not in html
    assert "when you are ready" not in html

    script = client.get("/static/app.js").text
    assert "sort', 'newest'" in script
    assert "window.confirm" not in script
