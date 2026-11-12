"""Test-wide setup.

Tests must never read the developer's local ``.env`` (it can hold a real API
key), so the app's ``.env`` loader is disabled before ``app`` is imported.
"""

import os

os.environ["LIFE_INBOX_SKIP_DOTENV"] = "1"
