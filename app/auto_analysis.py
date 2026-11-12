"""Background worker that analyzes new notes right after they are saved.

One worker thread runs every job in order, so two saves in quick succession
never analyze the same notes twice: each job re-reads what is still pending,
and a job that finds nothing new does nothing.

This module deliberately imports nothing from the rest of the app. The
callable that does the real work is passed in by ``app.main``.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


def auto_analysis_enabled() -> bool:
    """Automatic analysis is on unless LIFE_INBOX_AUTO_ANALYZE is 0/false/off."""
    value = os.getenv("LIFE_INBOX_AUTO_ANALYZE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


class AutoAnalyzer:
    def __init__(self, run_for_owner: Callable[[int], None]) -> None:
        self._run_for_owner = run_for_owner
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="auto-analyze"
        )
        self._lock = threading.Lock()
        self._jobs_waiting = 0

    @property
    def busy(self) -> bool:
        """True while a background job is queued or running."""
        with self._lock:
            return self._jobs_waiting > 0

    def schedule(self, owner_id: int) -> None:
        with self._lock:
            self._jobs_waiting += 1
        try:
            self._executor.submit(self._job, owner_id)
        except Exception:
            with self._lock:
                self._jobs_waiting -= 1
            logger.exception("Could not schedule automatic analysis")

    def _job(self, owner_id: int) -> None:
        try:
            self._run_for_owner(owner_id)
        except Exception:
            # Notes stay pending, so the next save or page load retries them.
            logger.exception("Automatic analysis failed")
        finally:
            with self._lock:
                self._jobs_waiting -= 1

    def wait_for_idle(self, timeout: float = 30.0) -> None:
        """Block until every job scheduled so far has finished (used by tests)."""
        self._executor.submit(lambda: None).result(timeout=timeout)
