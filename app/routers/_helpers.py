"""Shared helper functions for modular APIRouters."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent.parent
_STATIC_REF = re.compile(r'(["\'])(/static/[^"\'?]+\.(?:js|css))\1')


def resolve(name: str, default: Any) -> Any:
    """Dynamically resolve symbols from app.main if monkeypatched in tests."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, name):
        return getattr(main, name)
    return default


def trigger_auto_analysis() -> None:
    """Trigger background analysis for current owner if enabled."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, "_trigger_auto_analysis"):
        main._trigger_auto_analysis()
        return

    from app.auto_analysis import auto_analysis_enabled
    from app.database import get_current_owner_id

    if not auto_analysis_enabled():
        return
    owner_id = get_current_owner_id()
    if owner_id is None:
        return
    if main is not None and hasattr(main, "auto_analyzer"):
        main.auto_analyzer.schedule(owner_id)


def queue_webhooks(background_tasks: BackgroundTasks, event: str, data: Any) -> None:
    """Send `event` to user webhooks in the background."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, "queue_webhooks"):
        main.queue_webhooks(background_tasks, event, data)
        return

    from app.api_access import deliver_webhooks
    from app.database import webhooks_for_event

    hooks = webhooks_for_event(event)
    if hooks:
        background_tasks.add_task(deliver_webhooks, hooks, event, data)


def schedule_calendar_sync(owner_id: int) -> None:
    """Queue a calendar sync if this person keeps tasks on Google Calendar."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, "_schedule_calendar_sync"):
        main._schedule_calendar_sync(owner_id)
        return

    from app.config import load_config
    from app.database import calendar_sync_wanted
    import app.integrations.google_calendar as google_calendar

    try:
        if not google_calendar.calendar_feature_enabled(load_config(strict=False)):
            return
        if calendar_sync_wanted(owner_id):
            if main is not None and hasattr(main, "calendar_syncer"):
                main.calendar_syncer.schedule(owner_id)
    except Exception:
        logger.exception("Could not queue a calendar sync")


def versioned_static_refs(html: str) -> str:
    """Append ?v=<file mtime> to script/stylesheet URLs."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, "_versioned_static_refs"):
        return main._versioned_static_refs(html)

    def add_version(match):
        quote, url = match.group(1), match.group(2)
        file_path = APP_DIR / url.removeprefix("/")
        try:
            version = file_path.stat().st_mtime_ns
        except OSError:
            return match.group(0)
        return f"{quote}{url}?v={version}{quote}"

    return _STATIC_REF.sub(add_version, html)
