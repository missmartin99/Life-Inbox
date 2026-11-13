"""User profile, settings, avatar, and connected integrations routes."""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Response, UploadFile

from app.capture.image_extract import validate_image_upload
from app.config import load_config
from app.routers._helpers import resolve
from app.schemas import ProfileUpdate, UserSettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["me"])

GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/connections"


def _calendar_detail(calendar: dict) -> str:
    name = calendar["calendar_name"]
    if calendar["status"] == "unavailable":
        return "Not available on this server. Your tasks stay in Life Inbox."
    if calendar["status"] == "not_connected":
        return (
            f"Adds tasks that have a due date to a separate \u201c{name}\u201d "
            "calendar in your Google Calendar. Life Inbox can only see and "
            "change that one calendar, never your other events."
        )
    if calendar["status"] == "needs_reconnect":
        return calendar["last_error"] or "Reconnect to keep your tasks on your calendar."
    account = f" for {calendar['email']}" if calendar["email"] else ""
    return (
        f"Tasks with a due date go on the \u201c{name}\u201d calendar{account}, "
        "and come off when you finish or dismiss them."
    )


@router.post("/api/me/avatar")
async def upload_avatar(file: UploadFile = File(...)):
    """Set your profile photo. It replaces the Google picture until removed."""
    from app.database import require_owner_id, set_user_avatar_path
    from app.profile_photos import make_avatar_bytes, new_avatar_path
    from app.storage_backend import get_storage_backend

    user_id = require_owner_id()
    data = await file.read()
    try:
        validate_image_upload(file.filename or "photo", file.content_type, data)
        photo = make_avatar_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage = get_storage_backend()
    path = storage.store_bytes(new_avatar_path(user_id), photo)
    try:
        user, previous = set_user_avatar_path(user_id, path)
    except Exception:
        storage.delete(path)
        raise
    storage.delete(previous)
    return user


@router.get("/api/me/avatar")
def get_avatar():
    """Your uploaded profile photo (only ever your own)."""
    from app.database import get_user_avatar_path, require_owner_id
    from app.storage_backend import get_storage_backend

    path = get_user_avatar_path(require_owner_id())
    if not path:
        raise HTTPException(status_code=404, detail="No profile photo.")
    try:
        data = get_storage_backend().read_bytes(path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="No profile photo.") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@router.delete("/api/me/avatar")
def remove_avatar():
    """Remove your uploaded photo; the Google picture shows again."""
    from app.database import require_owner_id, set_user_avatar_path
    from app.storage_backend import get_storage_backend

    user, previous = set_user_avatar_path(require_owner_id(), None)
    get_storage_backend().delete(previous)
    return user


@router.patch("/api/me/profile")
def update_profile(payload: ProfileUpdate):
    """Edit your name and date of birth. Email comes from Google."""
    from app.database import require_owner_id, update_user_profile
    from app.profile import clean_date_of_birth, clean_display_name

    changes = {}
    try:
        if "display_name" in payload.model_fields_set:
            changes["display_name"] = clean_display_name(payload.display_name or "")
        if "date_of_birth" in payload.model_fields_set:
            changes["date_of_birth"] = clean_date_of_birth(payload.date_of_birth)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return update_user_profile(require_owner_id(), **changes)


@router.patch("/api/me/settings")
def save_settings(payload: UserSettingsUpdate):
    """Change notification and theme settings. Returns every setting."""
    from app.database import require_owner_id, update_user_settings

    changes = {
        key: value
        for key, value in payload.model_dump().items()
        if key in payload.model_fields_set and value is not None
    }
    return update_user_settings(require_owner_id(), changes)


@router.get("/api/me/connections")
def connected_apps():
    """The outside services this account uses, and whether each is on."""
    from app.database import get_user_by_id, require_owner_id
    import app.integrations.google_calendar as google_calendar

    owner_id = require_owner_id()
    user = get_user_by_id(owner_id)
    syncer = resolve("calendar_syncer", None)
    is_busy = syncer.is_busy(owner_id) if syncer is not None else False
    calendar = google_calendar.calendar_status(
        load_config(strict=False), owner_id, syncing=is_busy
    )
    ai_on = bool(os.getenv("OPENAI_API_KEY", "").strip())
    return {
        "apps": [
            {
                "id": "google",
                "name": "Google",
                "purpose": "Sign-in, your name and profile picture",
                "status": "connected" if user["google_connected"] else "not_connected",
                "detail": (
                    f"Signed in as {user['email']}. You sign in with Google, so it "
                    "stays connected. You can remove Life Inbox's access in your "
                    "Google Account."
                    if user["google_connected"]
                    else "This account does not use Google sign-in."
                ),
                "manage_url": GOOGLE_PERMISSIONS_URL if user["google_connected"] else None,
            },
            {
                "id": "google_calendar",
                "name": "Google Calendar",
                "purpose": "Put tasks with due dates on your calendar",
                "status": calendar["status"],
                "detail": _calendar_detail(calendar),
                "manage_url": None,
                "calendar": calendar,
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "purpose": "Organizes your notes and answers questions with AI",
                "status": "active" if ai_on else "off",
                "detail": (
                    "On for everyone on this server. The text of your notes is "
                    "sent to OpenAI to be organized."
                    if ai_on
                    else "Off on this server. Notes are organized by Life Inbox's "
                    "built-in rules, and nothing is sent to OpenAI."
                ),
                "manage_url": None,
            },
        ]
    }
