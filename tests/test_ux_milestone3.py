"""Tests for Milestone 3 UX & Notification Polish (Requirement R3).

Verifies:
1. Universal Toast Notification Engine (window.toast, styles, semantic themes).
2. Elimination of native blocking dialogs (alert, prompt, confirm) in app.js.
3. Rich informative empty states for dashboard lists and smart cards.
4. UX layout, navigation (/assistant), keyboard shortcuts, and tabular numbers.
"""

from pathlib import Path
import re
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_JS_PATH = REPO_ROOT / "app" / "static" / "ui.js"
APP_JS_PATH = REPO_ROOT / "app" / "static" / "app.js"
STYLES_CSS_PATH = REPO_ROOT / "app" / "static" / "styles.css"
INDEX_HTML_PATH = REPO_ROOT / "app" / "templates" / "index.html"


def test_universal_toast_engine_in_ui_and_css():
    """window.toast is defined in ui.js and styled in styles.css with semantic themes."""
    ui_js = UI_JS_PATH.read_text(encoding="utf-8")
    assert "window.toast = toast;" in ui_js
    assert "function toast(message, type = 'info', duration = 4000)" in ui_js
    assert "toast-${normType}" in ui_js
    assert "toast-close-btn" in ui_js

    css = STYLES_CSS_PATH.read_text(encoding="utf-8")
    assert ".toasts" in css
    assert "z-index: 1200;" in css
    assert ".toast-info" in css
    assert ".toast-success" in css
    assert ".toast-warn" in css
    assert ".toast-error" in css
    assert ".toast-close-btn" in css


def test_toast_wired_for_mcp_and_ai_transformations():
    """Copying MCP configuration and running AI transformations dispatch toasts."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")

    # MCP config copy
    assert "window.toast('MCP configuration copied to clipboard!', 'success')" in app_js

    # Smart card conversion & entity extraction toasts
    assert "window.toast(`Converted to ${res.kind} card!`, 'success')" in app_js
    assert "window.toast(`Extracted ${res.length} entities! Check 05 Entities Treemap tab.`, 'success')" in app_js
    assert "window.toast(`Successfully structured ${count} cards!`, 'success')" in app_js
    assert "window.toast(`Scan complete! Discovered ${total} entity connections.`, 'success')" in app_js


def test_zero_blocking_browser_dialogs_in_app_js():
    """All blocking window.alert, window.prompt, and window.confirm calls are eliminated."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")

    # Strip single-line and multi-line comments before checking
    no_comments = re.sub(r"/\*[\s\S]*?\*/", "", app_js)
    lines = [line.split("//")[0] for line in no_comments.splitlines()]
    clean_code = "\n".join(lines)

    assert not re.search(r"\balert\s*\(", clean_code), "Found alert() in app.js"
    assert not re.search(r"\bprompt\s*\(", clean_code), "Found prompt() in app.js"
    assert not re.search(r"\bconfirm\s*\(", clean_code), "Found confirm() in app.js"

    # Verify inline pin creator exists
    assert "function promptAddPin(" in app_js
    assert "calPinInlineForm" in app_js
    assert "saveInlinePin" in app_js
    assert "cancelAddPin" in app_js


def test_rich_informative_empty_states():
    """Dashboard lists and Smart Cards render rich empty states with guidance."""
    app_js = APP_JS_PATH.read_text(encoding="utf-8")
    css = STYLES_CSS_PATH.read_text(encoding="utf-8")

    # Dashboard empty states
    assert "cal-empty-task-card celebration" in app_js
    assert "All clear for today" in app_js
    assert "cal-empty-task-card clean" in app_js
    assert "No overdue tasks" in app_js
    assert "No upcoming tasks" in app_js
    assert "No pinned notes yet" in app_js

    # Smart Cards empty state
    assert "cards-empty-state" in app_js
    assert "clearCardsFilters" in app_js
    assert "No smart cards created yet" in app_js

    # CSS rules
    assert ".cal-empty-task-card" in css
    assert ".cal-empty-task-card.celebration" in css
    assert ".cal-empty-task-card.clean" in css
    assert ".cards-empty-state" in css


def test_ux_layout_navigation_and_typography_polish():
    """Tour FAB is shifted to bottom-left, assistant is linked, shortcuts wired, and tabular-nums applied."""
    css = STYLES_CSS_PATH.read_text(encoding="utf-8")
    html = INDEX_HTML_PATH.read_text(encoding="utf-8")
    app_js = APP_JS_PATH.read_text(encoding="utf-8")

    # Tour FAB moved to bottom-left
    assert re.search(r"\.tour-floater-btn\s*\{[^}]*bottom:\s*24px;\s*left:\s*24px;", css), (
        ".tour-floater-btn should be positioned at bottom: 24px; left: 24px;"
    )

    # Voice assistant linked in header navigation
    assert 'href="/assistant"' in html
    assert 'id="assistantNavBtn"' in html

    # Fast-capture keyboard shortcuts
    assert "noteTextEl.addEventListener('keydown'" in app_js
    assert "(e.metaKey || e.ctrlKey) && e.key === 'Enter'" in app_js
    assert "urlTextEl.addEventListener('keydown'" in app_js

    # Tabular numbers on currency and counters
    assert "font-variant-numeric: tabular-nums;" in css
    assert ".expense-amount" in css
    assert ".cal-count-pill" in css
    assert ".today-count-badge" in css
    assert ".overdue-count-badge" in css
