"""Tests for Milestone 2: modular APIRouters and deepened ingestion pipeline."""

import importlib
import sys
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.capture.pipeline as pipeline


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create an app client backed only by a per-test SQLite database."""
    test_db_path = tmp_path / "test_arch.db"
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

    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()


def test_routers_mounted_and_accessible(client):
    """Verify that all domain APIRouters respond through FastAPI."""
    # Auth routes
    assert client.get("/api/auth/options").status_code == 200
    assert client.get("/login").status_code == 200

    # Items routes
    assert client.get("/api/items").status_code == 200

    # Tasks routes
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/attention").status_code == 200
    assert client.get("/api/notifications").status_code == 200
    assert client.get("/api/briefing").status_code == 200

    # Calendar routes
    assert client.get("/api/calendar/status").status_code == 200

    # Intelligence routes
    assert client.get("/api/cards").status_code == 200
    assert client.get("/api/cards/kinds").status_code == 200
    assert client.get("/api/expenses").status_code == 200
    assert client.get("/api/entities").status_code == 200
    assert client.get("/api/resurface").status_code == 200
    assert client.get("/api/flashcards/due").status_code == 200

    # System routes
    assert client.get("/api/tokens").status_code == 200
    assert client.get("/api/webhooks").status_code == 200
    assert client.get("/api/privacy/ai-log").status_code == 200
    assert client.get("/api/relations").status_code == 200
    assert client.get("/api/dashboard").status_code == 200
    assert client.get("/api/analyze/pending").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_main_reexports_backward_compatibility(client):
    """Verify that all public and test-utilized symbols remain exported on app.main."""
    main = sys.modules["app.main"]
    assert hasattr(main, "app")
    assert hasattr(main, "auto_analyzer")
    assert hasattr(main, "calendar_syncer")
    assert hasattr(main, "APP_DIR")
    assert hasattr(main, "process_item")
    assert hasattr(main, "_process_url_item")
    assert hasattr(main, "_process_pdf_item")
    assert hasattr(main, "_process_image_item")
    assert hasattr(main, "_process_voice_item")
    assert callable(main.process_item)
    assert callable(main.extract_pdf_text)
    assert callable(main.process_image_bytes)
    assert callable(main.process_voice_bytes)
    assert callable(main.analyze_inbox_with_openai)


def test_pipeline_process_item_missing_item_raises_404(client):
    """process_item should raise HTTPException(404) for non-existent item IDs."""
    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_item(9999999)
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


def test_pipeline_process_item_text_type(client):
    """process_item on a text item returns the item as-is without re-processing."""
    from app.database import add_item_to_db

    item = add_item_to_db("A simple text note for testing.")
    result = pipeline.process_item(item["id"])
    assert result["id"] == item["id"]
    assert result["item_type"] == "text"


def test_pipeline_process_item_unsupported_type(client, monkeypatch):
    """process_item should raise HTTPException(400) if item_type is unknown."""
    from app.database import add_item_to_db

    item = add_item_to_db("Note with weird type")
    original_get = pipeline._db().get_item_from_db

    def mock_get(item_id):
        res = dict(original_get(item_id))
        res["item_type"] = "unsupported_format"
        return res

    monkeypatch.setattr(pipeline._db(), "get_item_from_db", mock_get)

    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_item(item["id"])
    assert exc_info.value.status_code == 400
    assert "Unsupported item type" in exc_info.value.detail


def test_pipeline_individual_processors_missing_item_raises_404(client):
    """Each individual processor should raise 404 when item does not exist."""
    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_url_item(9999991)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_pdf_item(9999992)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_image_item(9999993)
    assert exc_info.value.status_code == 404

    with pytest.raises(HTTPException) as exc_info:
        pipeline.process_voice_item(9999994)
    assert exc_info.value.status_code == 404
