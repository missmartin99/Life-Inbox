"""Jin's voice script is served, versioned, and loaded after playful.js."""

import importlib
import re
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'life_inbox_test.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    for name in (
        "app.main",
        "app.database",
        "app.auth",
        "app.config",
        "app.integrations.calendar",
    ):
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    with TestClient(main.app) as test_client:
        yield test_client
    database.engine.dispose()


def test_cat_voice_script_is_loaded_after_playful(client):
    html = client.get("/").text
    assert re.search(r"/static/cat_voice\.js\?v=\d+", html)
    assert html.index("playful.js") < html.index("cat_voice.js")
    assert client.get("/static/cat_voice.js").status_code == 200
