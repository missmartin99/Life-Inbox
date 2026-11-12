"""Life Inbox application package.

Loads a local ``.env`` file (if present) before any submodule reads its
settings. Variables already set in the real environment always win.
"""

from pathlib import Path as _Path
import os as _os


def _load_dotenv(path: _Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in _os.environ:
            _os.environ[key] = value


if not _os.environ.get("LIFE_INBOX_SKIP_DOTENV"):
    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env")
