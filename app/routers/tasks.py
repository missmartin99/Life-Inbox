"""Task actions, attention board, notifications, and daily briefing routes."""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from app.database import (
    confirm_task_timing,
    get_attention_board,
    get_daily_briefing,
    get_user_settings,
    list_pending_tasks_by_priority,
    list_tasks_from_db,
    require_owner_id,
    set_task_reminder_in_db,
    set_task_status,
    snooze_task_in_db,
)
from app.schemas import (
    Task,
    TaskListStatus,
    TaskReminderRequest,
    TaskSnoozeRequest,
    TaskTimingConfirm,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tasks"])


def _transition_task(task_id: int, task_status: str):
    try:
        task = set_task_status(task_id, task_status)
    except Exception as exc:
        logger.exception("Failed to update task status")
        raise HTTPException(
            status_code=500,
            detail="Unable to update task. Please try again.",
        ) from exc

    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.get("/api/tasks", response_model=List[Task])
def get_tasks(
    task_status: TaskListStatus = Query("open", alias="status"),
):
    try:
        return list_tasks_from_db(task_status)
    except Exception as exc:
        logger.exception("Failed to load tasks")
        raise HTTPException(
            status_code=500,
            detail="Unable to load tasks. Please try again.",
        ) from exc


@router.post("/api/tasks/{task_id}/complete", response_model=Task)
def complete_task(task_id: int):
    return _transition_task(task_id, "completed")


@router.post("/api/tasks/{task_id}/reopen", response_model=Task)
def reopen_task(task_id: int):
    return _transition_task(task_id, "open")


@router.post("/api/tasks/{task_id}/dismiss", response_model=Task)
def dismiss_task(task_id: int):
    return _transition_task(task_id, "dismissed")


@router.post("/api/tasks/{task_id}/confirm-timing", response_model=Task)
def confirm_timing(task_id: int, payload: TaskTimingConfirm):
    try:
        task = confirm_task_timing(task_id, payload.timing_text)
    except Exception as exc:
        logger.exception("Failed to confirm task timing")
        raise HTTPException(
            status_code=500,
            detail="Unable to confirm task timing. Please try again.",
        ) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/api/tasks/{task_id}/snooze", response_model=Task)
def snooze_task(task_id: int, payload: TaskSnoozeRequest):
    try:
        task = snooze_task_in_db(task_id, payload.until.strip())
    except Exception as exc:
        logger.exception("Failed to snooze task")
        raise HTTPException(
            status_code=500,
            detail="Unable to snooze task. Please try again.",
        ) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/api/tasks/{task_id}/reminder", response_model=Task)
def set_task_reminder(task_id: int, payload: TaskReminderRequest):
    try:
        task = set_task_reminder_in_db(task_id, payload.reminder_at.strip())
    except Exception as exc:
        logger.exception("Failed to set reminder")
        raise HTTPException(
            status_code=500,
            detail="Unable to set reminder. Please try again.",
        ) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.get("/api/attention")
def attention_board():
    try:
        return get_attention_board()
    except Exception as exc:
        logger.exception("Failed to load attention board")
        raise HTTPException(
            status_code=500,
            detail="Unable to load attention board. Please try again.",
        ) from exc


@router.get("/api/notifications")
def notifications():
    """Pending tasks for the bell, highest priority first, per your settings."""
    settings = get_user_settings(require_owner_id())
    try:
        return {
            **list_pending_tasks_by_priority(settings["bell_scope"]),
            "show_badge": settings["bell_badge"],
            "overdue_alert": settings["bell_overdue_alert"],
        }
    except Exception as exc:
        logger.exception("Failed to load notifications")
        raise HTTPException(
            status_code=500,
            detail="Unable to load your pending tasks. Please try again.",
        ) from exc


@router.get("/api/briefing")
def daily_briefing():
    try:
        return get_daily_briefing()
    except Exception as exc:
        logger.exception("Failed to load daily briefing")
        raise HTTPException(
            status_code=500,
            detail="Unable to load daily briefing. Please try again.",
        ) from exc
