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
# Most API tests talk to the app without signing in; they use the shared
# Local Owner account, which the real app keeps off by default.
os.environ.setdefault("LIFE_INBOX_LOCAL_ANONYMOUS", "1")
os.environ.setdefault("LIFE_INBOX_ALLOW_DEV_LOGIN", "1")
