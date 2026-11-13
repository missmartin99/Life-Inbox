"""Calendar integration routes (status, preview, confirm, Google Calendar sync)."""

from __future__ import annotations

import logging
from datetime import date as date_cls
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.attention.dates import configured_timezone, parse_timing_phrase
from app.config import load_config
from app.database import (
    get_task_by_id,
    require_owner_id,
    save_calendar_receipt,
    task_is_owned,
)
from app.integrations.calendar import (
    CalendarWriteBlockedError,
    get_calendar_adapter,
    get_draft,
    store_draft,
)
import app.integrations.google_calendar as google_calendar
from app.routers._helpers import resolve
from app.schemas import CalendarConfirmRequest, CalendarPreviewRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["calendar"])


@router.get("/api/calendar/status")
def calendar_status():
    adapter = get_calendar_adapter()
    status = adapter.status()
    return {
        "provider": status.provider,
        "configured": status.configured,
        "oauth_connected": status.oauth_connected,
        "writes_enabled": status.writes_enabled,
        "mode": status.mode,
        "message": status.message,
    }


@router.post("/api/calendar/preview")
def calendar_preview(payload: CalendarPreviewRequest):
    task = get_task_by_id(payload.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")

    title = (payload.title or task["text"]).strip()
    if payload.start_date:
        try:
            start = date_cls.fromisoformat(payload.start_date.strip())
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="start_date must be YYYY-MM-DD.",
            ) from exc
    elif task.get("due_on"):
        start = date_cls.fromisoformat(task["due_on"])
    else:
        parsed = parse_timing_phrase(task.get("timing_text"))
        if parsed.due_date is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Task has no date, so there is nothing to put on a "
                    "calendar."
                ),
            )
        start = parsed.due_date

    adapter = get_calendar_adapter()
    draft = adapter.preview_event(
        task_id=task["id"],
        title=title,
        start_date=start,
        description=payload.description or "",
        timezone_name=str(configured_timezone()),
    )
    store_draft(draft)
    return {
        "draft_id": draft.draft_id,
        "task_id": draft.task_id,
        "title": draft.title,
        "start_date": draft.start_date,
        "end_date": draft.end_date,
        "description": draft.description,
        "timezone": draft.timezone_name,
        "provider": draft.provider,
        "status": draft.status,
        "confirmation_required": draft.confirmation_required,
        "notes": draft.notes,
    }


@router.post("/api/calendar/confirm")
def calendar_confirm(payload: CalendarConfirmRequest):
    draft = get_draft(payload.draft_id)
    if draft is None or not task_is_owned(draft.task_id):
        raise HTTPException(
            status_code=404,
            detail="Calendar draft not found. Create a preview first.",
        )
    adapter = get_calendar_adapter()
    try:
        confirmed = adapter.confirm_event(draft)
    except CalendarWriteBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Calendar confirm failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to confirm calendar action.",
        ) from exc

    store_draft(confirmed)
    receipt = save_calendar_receipt(
        draft_id=confirmed.draft_id,
        task_id=confirmed.task_id,
        provider=confirmed.provider,
        status=confirmed.status,
        payload={
            "title": confirmed.title,
            "start_date": confirmed.start_date,
            "end_date": confirmed.end_date,
            "timezone": confirmed.timezone_name,
            "notes": confirmed.notes,
        },
    )
    return {
        "draft_id": confirmed.draft_id,
        "task_id": confirmed.task_id,
        "status": confirmed.status,
        "provider": confirmed.provider,
        "notes": confirmed.notes,
        "receipt": receipt,
    }


@router.get("/api/calendar/google")
def google_calendar_status():
    owner_id = require_owner_id()
    syncer = resolve("calendar_syncer", None)
    is_busy = syncer.is_busy(owner_id) if syncer is not None else False
    return google_calendar.calendar_status(
        load_config(strict=False), owner_id, syncing=is_busy
    )


@router.post("/api/calendar/google/sync")
def google_calendar_sync_now():
    owner_id = require_owner_id()
    config = load_config(strict=False)
    if not google_calendar.calendar_status(config, owner_id)["connected"]:
        raise HTTPException(status_code=409, detail="Google Calendar is not connected.")
    syncer = resolve("calendar_syncer", None)
    if syncer is not None:
        syncer.schedule(owner_id)
    return google_calendar.calendar_status(config, owner_id, syncing=True)


@router.delete("/api/calendar/google")
def google_calendar_disconnect():
    """Take the Life Inbox calendar off Google Calendar and give the access back."""
    owner_id = require_owner_id()
    config = load_config(strict=False)
    syncer = resolve("calendar_syncer", None)
    if syncer is not None:
        with syncer.owner_lock(owner_id):  # never mid-sync
            result = google_calendar.disconnect(config, owner_id)
    else:
        result = google_calendar.disconnect(config, owner_id)
    if not result["disconnected"]:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected.")
    return {**result, "calendar": google_calendar.calendar_status(config, owner_id)}
