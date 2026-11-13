"""The sign-in page shares the main page's look (fonts, ribbon, theme)."""

import importlib
import sys

import pytest
from fastapi.testclient import TestClient

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.integrations.calendar",
    "app.integrations.google_calendar",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'login.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in ("LIFE_INBOX_ENV", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    with TestClient(main.app) as test_client:
        yield test_client
    importlib.import_module("app.database").engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


def test_login_page_matches_the_main_page(client):
    page = client.get("/login").text
    # Same theme, stylesheet, fonts and ribbon as the main page (versioned).
    for asset in ("/static/theme.js?v=", "/static/styles.css?v=", "/static/ribbon.js?v=", "/static/login.js?v="):
        assert asset in page, asset
    assert page.index("/static/theme.js") < page.index("/static/styles.css")
    assert "family=Inter" in page and "satoshi" in page
    assert '<canvas id="ribbon" class="ribbon"' in page
    assert 'class="hero-title" data-split="glyph">Life Inbox<' in page
    assert 'class="brand" href="/login">Life<span>Inbox</span>' in page


def test_login_page_has_the_google_button(client):
    page = client.get("/login").text
    assert '<a class="gsi-btn" href="/api/auth/google/start">' in page
    assert "Sign in with Google" in page
    assert 'class="login-error" role="alert"></p>' in page  # no error shown


def test_login_errors_are_shown_and_escaped(client):
    page = client.get("/login", params={"error": "not_allowed"}).text
    assert "This Google account is not on the invite list." in page
    unknown = client.get("/login", params={"error": "<script>"}).text
    assert "<script>" not in unknown.split("<body", 1)[1].replace('<script src=', '')


def test_main_page_loads_the_shared_ribbon_before_ui(client):
    page = (importlib.import_module("app.main").APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    assert page.index("/static/ribbon.js") < page.index("/static/ui.js")
    assert client.get("/static/ribbon.js").status_code == 200
    assert "window.LifeInboxRibbon" in client.get("/static/ribbon.js").text
