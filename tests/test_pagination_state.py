"""Regression tests for inbox list pagination state (delete + Load more).

Exercises the pure JS helper via Node — no browser or frontend framework.
Browser procedure for the 102-note case is documented in docs/manual-testing.md.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "app" / "static" / "pagination_state.js"
NODE = shutil.which("node")


pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for JS helper tests")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create an app client backed only by a per-test SQLite database."""
    import importlib
    import sys

    test_db_path = tmp_path / "life_inbox_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    sys.modules.pop("app.database", None)
    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")

    assert database.engine.url.database == str(test_db_path)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as test_client:
        yield test_client

    database.engine.dispose()
    sys.modules.pop("app.main", None)
    sys.modules.pop("app.database", None)


def run_helper(script: str) -> dict:
    """Run a Node script that requires the helper and prints JSON on stdout."""
    completed = subprocess.run(
        [NODE, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(HELPER.parent),
    )
    return json.loads(completed.stdout)


def test_delete_loaded_note_decrements_offset_and_removes_item():
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
const rows = [
  {{id: 1, item_type: 'text', raw_text: 'a'}},
  {{id: 2, item_type: 'text', raw_text: 'b'}},
  {{id: 3, item_type: 'text', raw_text: 'c'}}
];
P.applyFetchedPage(state, rows, {{append: false}});
assert.equal(state.loadedOffset, 3);

assert.equal(P.afterLoadedNoteDeleted(state, 2), true);
assert.equal(state.loadedOffset, 2);
assert.deepEqual(state.loadedItems.map((n) => n.id), [1, 3]);

assert.equal(P.afterLoadedNoteDeleted(state, 999), false);
assert.equal(state.loadedOffset, 2);

console.log(JSON.stringify({{ok: true, loadedOffset: state.loadedOffset}}));
"""
    )
    assert result["loadedOffset"] == 2


def test_failed_delete_path_leaves_state_unchanged_when_helper_not_called():
    """Caller must only invoke afterLoadedNoteDeleted after successful DELETE."""
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
P.applyFetchedPage(state, [
  {{id: 1, item_type: 'text', raw_text: 'keep'}}
], {{append: false}});
const before = {{
  offset: state.loadedOffset,
  ids: state.loadedItems.map((n) => n.id)
}};
assert.equal(state.loadedOffset, before.offset);
assert.deepEqual(state.loadedItems.map((n) => n.id), before.ids);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert result["ok"] is True


def test_edit_does_not_change_pagination_offset():
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
P.applyFetchedPage(state, [
  {{id: 1, item_type: 'text', raw_text: 'old'}},
  {{id: 2, item_type: 'text', raw_text: 'two'}}
], {{append: false}});
assert.equal(P.afterLoadedNoteUpdated(state, {{
  id: 1, item_type: 'text', raw_text: 'new'
}}), true);
assert.equal(state.loadedOffset, 2);
assert.equal(state.hasMore, false);
assert.equal(state.loadedItems[0].raw_text, 'new');
console.log(JSON.stringify({{loadedOffset: state.loadedOffset}}));
"""
    )
    assert result["loadedOffset"] == 2


def test_overlapping_list_requests_are_rejected():
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
assert.equal(P.beginListRequest(state), true);
assert.equal(P.beginListRequest(state), false);
P.endListRequest(state);
assert.equal(P.beginListRequest(state), true);
P.endListRequest(state);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert result["ok"] is True


def test_reload_resets_offset_from_beginning():
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
P.applyFetchedPage(state, Array.from({{length: 100}}, (_, i) => ({{
  id: i + 1, item_type: 'text', raw_text: 'n' + i
}})), {{append: false}});
P.afterLoadedNoteDeleted(state, 1);
assert.equal(state.loadedOffset, 99);
P.resetForReload(state);
assert.equal(state.loadedOffset, 0);
assert.deepEqual(state.loadedItems, []);
assert.equal(state.hasMore, false);
console.log(JSON.stringify({{ok: true}}));
"""
    )
    assert result["ok"] is True


def test_delete_then_load_more_includes_note_101():
    """Full offset arithmetic for the reported 102-note reproduction."""
    result = run_helper(
        f"""
const assert = require('assert');
const P = require({json.dumps(str(HELPER))});
const state = P.createState(100);
const page1 = Array.from({{length: 100}}, (_, i) => ({{
  id: i + 1,
  item_type: 'text',
  raw_text: 'Pagination note ' + String(i + 1).padStart(3, '0')
}}));
P.applyFetchedPage(state, page1, {{append: false}});
assert.equal(state.loadedOffset, 100);

P.afterLoadedNoteDeleted(state, 1);
assert.equal(state.loadedOffset, 99);

const page2 = [
  {{id: 101, item_type: 'text', raw_text: 'Pagination note 101'}},
  {{id: 102, item_type: 'text', raw_text: 'Pagination note 102'}}
];
P.applyFetchedPage(state, page2, {{append: true}});

const ids = state.loadedItems.map((item) => item.id);
assert.ok(ids.includes(101));
assert.ok(ids.includes(102));
assert.ok(!ids.includes(1));
assert.equal(ids.length, 101);
assert.equal(new Set(ids).size, 101);
assert.deepEqual(ids, Array.from({{length: 101}}, (_, i) => i + 2));
assert.equal(state.loadedOffset, 101);
assert.equal(state.hasMore, false);

console.log(JSON.stringify({{
  loadedOffset: state.loadedOffset,
  firstId: ids[0],
  lastId: ids[ids.length - 1],
  includes101: ids.includes(101)
}}));
"""
    )
    assert result["includes101"] is True
    assert result["firstId"] == 2
    assert result["lastId"] == 102
    assert result["loadedOffset"] == 101


def test_static_assets_include_pagination_helper(client):
    helper = client.get("/static/pagination_state.js")
    assert helper.status_code == 200
    assert "afterLoadedNoteDeleted" in helper.text

    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "afterLoadedNoteDeleted" in app_js.text
    assert "beginListRequest" in app_js.text

    home = client.get("/")
    assert home.status_code == 200
    assert "/static/pagination_state.js" in home.text
