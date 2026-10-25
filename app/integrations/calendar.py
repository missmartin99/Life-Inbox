"""Google Calendar adapter — preview/confirm only; no real OAuth writes."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional


class CalendarNotConfiguredError(RuntimeError):
    pass


class CalendarWriteBlockedError(RuntimeError):
    """Raised when a real write would be required but is not authorized."""


@dataclass
class CalendarEventDraft:
    draft_id: str
    task_id: int
    title: str
    start_date: str
    end_date: str
    description: str
    timezone_name: str
    provider: str
    status: str = "preview"  # preview | confirmed_mock | blocked
    confirmation_required: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class CalendarAdapterStatus:
    provider: str
    configured: bool
    oauth_connected: bool
    writes_enabled: bool
    mode: str
    message: str


def calendar_config() -> CalendarAdapterStatus:
    provider = os.getenv("LIFE_INBOX_CALENDAR_PROVIDER", "mock").strip() or "mock"
    oauth = os.getenv("LIFE_INBOX_GOOGLE_CALENDAR_OAUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    writes = os.getenv("LIFE_INBOX_CALENDAR_WRITES", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    # Hard safety: never enable real writes in this teaching build without an
    # explicit dual flag — and even then the real Google client is not wired.
    real_client = False
    if provider == "google" and oauth and writes and real_client:
        mode = "live"
        message = "Live Google Calendar writes would be enabled."
        configured = True
    elif provider == "google":
        mode = "preview_only"
        message = (
            "Google Calendar adapter is present in preview-only mode. "
            "Real OAuth consent and Calendar writes are not activated."
        )
        configured = True
    else:
        mode = "mock"
        message = (
            "Using mock Calendar adapter. Previews and mock confirmations only; "
            "no external events are created."
        )
        configured = True
    return CalendarAdapterStatus(
        provider=provider,
        configured=configured,
        oauth_connected=False,
        writes_enabled=False,
        mode=mode,
        message=message,
    )


class CalendarAdapter:
    """Replaceable Calendar interface."""

    def status(self) -> CalendarAdapterStatus:
        raise NotImplementedError

    def preview_event(
        self,
        *,
        task_id: int,
        title: str,
        start_date: date,
        description: str = "",
        timezone_name: str = "UTC",
    ) -> CalendarEventDraft:
        raise NotImplementedError

    def confirm_event(self, draft: CalendarEventDraft) -> CalendarEventDraft:
        raise NotImplementedError


class MockCalendarAdapter(CalendarAdapter):
    """In-memory mock: confirms locally without external side effects."""

    def __init__(self) -> None:
        self._confirmed: dict[str, CalendarEventDraft] = {}

    def status(self) -> CalendarAdapterStatus:
        status = calendar_config()
        status.provider = "mock"
        status.mode = "mock"
        status.oauth_connected = False
        status.writes_enabled = False
        status.message = (
            "Mock Calendar adapter active. Confirm stores a local mock receipt only."
        )
        return status

    def preview_event(
        self,
        *,
        task_id: int,
        title: str,
        start_date: date,
        description: str = "",
        timezone_name: str = "UTC",
    ) -> CalendarEventDraft:
        return CalendarEventDraft(
            draft_id=f"mock-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            title=title,
            start_date=start_date.isoformat(),
            end_date=start_date.isoformat(),
            description=description,
            timezone_name=timezone_name,
            provider="mock",
            status="preview",
            confirmation_required=True,
            notes=[
                "This is a preview only.",
                "Confirming will NOT create a Google Calendar event.",
                "No OAuth tokens are used.",
            ],
        )

    def confirm_event(self, draft: CalendarEventDraft) -> CalendarEventDraft:
        confirmed = CalendarEventDraft(
            draft_id=draft.draft_id,
            task_id=draft.task_id,
            title=draft.title,
            start_date=draft.start_date,
            end_date=draft.end_date,
            description=draft.description,
            timezone_name=draft.timezone_name,
            provider="mock",
            status="confirmed_mock",
            confirmation_required=False,
            notes=[
                "Mock confirmation recorded locally.",
                f"Recorded at {datetime.now(timezone.utc).isoformat()}",
                "No external calendar was modified.",
            ],
        )
        self._confirmed[confirmed.draft_id] = confirmed
        return confirmed


class GoogleCalendarPreviewAdapter(CalendarAdapter):
    """Google-shaped adapter that never performs OAuth or writes."""

    def status(self) -> CalendarAdapterStatus:
        return calendar_config()

    def preview_event(
        self,
        *,
        task_id: int,
        title: str,
        start_date: date,
        description: str = "",
        timezone_name: str = "UTC",
    ) -> CalendarEventDraft:
        return CalendarEventDraft(
            draft_id=f"gcal-preview-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            title=title,
            start_date=start_date.isoformat(),
            end_date=start_date.isoformat(),
            description=description,
            timezone_name=timezone_name,
            provider="google",
            status="preview",
            confirmation_required=True,
            notes=[
                "Preview of a Google Calendar event payload.",
                "OAuth is not connected in this build.",
                "Confirm is blocked until an owner explicitly authorizes live writes.",
            ],
        )

    def confirm_event(self, draft: CalendarEventDraft) -> CalendarEventDraft:
        raise CalendarWriteBlockedError(
            "Google Calendar writes are disabled. "
            "Owner approval + OAuth are required before any live event creation."
        )


_ADAPTER: Optional[CalendarAdapter] = None
_DRAFTS: dict[str, CalendarEventDraft] = {}


def get_calendar_adapter() -> CalendarAdapter:
    global _ADAPTER
    if _ADAPTER is None:
        provider = os.getenv("LIFE_INBOX_CALENDAR_PROVIDER", "mock").strip() or "mock"
        if provider == "google":
            _ADAPTER = GoogleCalendarPreviewAdapter()
        else:
            _ADAPTER = MockCalendarAdapter()
    return _ADAPTER


def reset_calendar_adapter_for_tests() -> None:
    global _ADAPTER, _DRAFTS
    _ADAPTER = None
    _DRAFTS = {}


def store_draft(draft: CalendarEventDraft) -> CalendarEventDraft:
    _DRAFTS[draft.draft_id] = draft
    return draft


def get_draft(draft_id: str) -> Optional[CalendarEventDraft]:
    return _DRAFTS.get(draft_id)
