"""Test-wide setup.

Tests must never read the developer's local ``.env`` (it can hold a real API
key), so the app's ``.env`` loader is disabled before ``app`` is imported.

Automatic analysis (which runs on a background thread after every save) is
off by default so existing tests stay deterministic. Tests that exercise it
turn it on with ``monkeypatch.setenv("LIFE_INBOX_AUTO_ANALYZE", "1")``.
"""

import os

os.environ["LIFE_INBOX_SKIP_DOTENV"] = "1"
os.environ["LIFE_INBOX_AUTO_ANALYZE"] = "0"
