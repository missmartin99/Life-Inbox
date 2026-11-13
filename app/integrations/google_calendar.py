"""Google Calendar: keeps tasks that have a due date on a "Life Inbox" calendar.

Permission: ``calendar.app.created``. Life Inbox can create its own calendar
and manage the events on it, and nothing else. It cannot see or change any of
the person's other calendars or events.

How it works:

* Connecting is a second, opt-in Google consent ("incremental authorization")
  that reuses the sign-in callback URL, so no new redirect URI is needed.
* The refresh token is stored encrypted (``app.token_crypto``).
* Syncing compares what each open, dated task should look like on the
  calendar with what Life Inbox last put there, and only sends the changes:
  new tasks are added, changed ones are updated, and finished, dismissed or
  deleted tasks are taken off.
* Syncs run in one background thread (``CalendarSyncer``), so saving a task
  never waits for Google.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import threading
import time as time_module
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

import requests

from app.config import AppConfig
from app.database import CALENDAR_CONNECTED, CALENDAR_NEEDS_RECONNECT
from app.google_oauth import TOKEN_URL, USERINFO_URL, start_authorization
from app.token_crypto import decrypt_token, encrypt_token

logger = logging.getLogger(__name__)


def _db():
    """app.database, looked up when used (tests reload it with a fresh engine)."""
    return importlib.import_module("app.database")

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
CONNECT_SCOPES = f"openid email {CALENDAR_SCOPE}"
API_BASE = "https://www.googleapis.com/calendar/v3"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_URL = "https://calendar.google.com/calendar/r"
CALENDAR_NAME = "Life Inbox"
CALENDAR_DESCRIPTION = (
    "Tasks with a due date from Life Inbox. Life Inbox keeps this calendar up "
    "to date; changes made here are replaced on the next sync."
)
EVENT_MINUTES = 30
# Google Calendar event colours: 11 Tomato, 5 Banana, 8 Graphite.
PRIORITY_COLORS = {"High": "11", "Medium": "5", "Low": "8"}
REQUEST_TIMEOUT_SECONDS = 15
TOKEN_EARLY_REFRESH_SECONDS = 60

MESSAGES = {
    "revoked": (
        "Google Calendar access was removed or has expired. Reconnect to keep "
        "your tasks on your calendar."
    ),
    "key_changed": (
        "This server can no longer read the saved Google Calendar access. "
        "Reconnect to keep your tasks on your calendar."
    ),
    "scope": (
        "Life Inbox no longer has permission to manage its calendar. Reconnect "
        "and leave the Google Calendar box ticked."
    ),
    "api_disabled": (
        "The Google Calendar API is turned off for this server's Google Cloud "
        "project. The server owner needs to enable it."
    ),
    "busy": "Google Calendar is busy right now. Life Inbox will try again.",
    "offline": "Couldn't reach Google Calendar. Life Inbox will try again.",
    "refused": "Google Calendar didn't accept a change. Life Inbox will try again.",
}


class NeedsReconnect(Exception):
    """The saved access no longer works; the person must connect again."""

    def __init__(self, key: str):
        super().__init__(key)
        self.message = MESSAGES[key]


class CalendarSyncError(Exception):
    """Something temporary went wrong; the next sync tries again."""

    def __init__(self, key: str):
        super().__init__(key)
        self.message = MESSAGES[key]


class CalendarGone(Exception):
    """The Life Inbox calendar was deleted in Google Calendar."""


class CalendarConnectError(Exception):
    """Connecting failed. ``code`` is a short key shown on the Settings page."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def calendar_feature_enabled(config: AppConfig) -> bool:
    """On whenever Google sign-in is set up, unless LIFE_INBOX_GOOGLE_CALENDAR=0."""
    flag = os.getenv("LIFE_INBOX_GOOGLE_CALENDAR", "1").strip().lower()
    return config.google_enabled and flag not in {"0", "false", "no", "off"}


def _http(method: str, url: str, **kwargs) -> requests.Response:
    """Every call to Google goes through here (tests replace it)."""
    return requests.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry(expires_in) -> str:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        seconds = 3600
    return (_now() + timedelta(seconds=seconds)).isoformat()


# --- connecting -------------------------------------------------------------------


def build_connect_authorization(
    config: AppConfig, *, user_id: int, login_hint: Optional[str]
) -> tuple[str, str]:
    """Google consent for the calendar. Returns (redirect_url, state_cookie)."""
    params = {
        "access_type": "offline",  # a refresh token, so syncing works later
        "prompt": "consent",  # always hand back a refresh token
        "include_granted_scopes": "true",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return start_authorization(
        config,
        scope=CONNECT_SCOPES,
        params=params,
        state={"p": "calendar", "u": user_id, "n": "/settings"},
    )


def finish_connect(config: AppConfig, user_id: int, code: str, verifier: str) -> dict:
    """Swap Google's code for tokens and save the connection (encrypted)."""
    from app.google_oauth import redirect_uri

    try:
        response = _http(
            "POST",
            TOKEN_URL,
            data={
                "code": code,
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "redirect_uri": redirect_uri(config),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        if response.status_code != 200:
            raise CalendarConnectError("failed")
        tokens = response.json()
        granted = set(str(tokens.get("scope") or "").split())
        if CALENDAR_SCOPE not in granted:
            # Google lets people untick individual permissions.
            raise CalendarConnectError("missing_scope")
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not access_token or not refresh_token:
            raise CalendarConnectError("failed")
        email = None
        info = _http(
            "GET", USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if info.status_code == 200:
            email = str(info.json().get("email") or "").strip().lower() or None
    except requests.RequestException as exc:
        raise CalendarConnectError("failed") from exc
    except ValueError as exc:
        raise CalendarConnectError("failed") from exc

    secret = config.session_secret
    return _db().save_calendar_connection(
        user_id,
        google_email=email,
        scopes=" ".join(sorted(granted)),
        refresh_token_enc=encrypt_token(secret, refresh_token),
        access_token_enc=encrypt_token(secret, access_token),
        access_token_expires_at=_expiry(tokens.get("expires_in")),
    )


# --- talking to Google Calendar -----------------------------------------------------


class TokenSource:
    """A valid access token for one connection, refreshed when it runs out."""

    def __init__(self, config: AppConfig, connection: dict):
        self._config = config
        self._user_id = connection["user_id"]
        secret = config.session_secret
        self._refresh_token = decrypt_token(secret, connection["refresh_token_enc"])
        self._access_token = decrypt_token(secret, connection.get("access_token_enc"))
        self._expires_at = connection.get("access_token_expires_at")
        if not self._refresh_token:
            raise NeedsReconnect("key_changed")

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    def _still_valid(self) -> bool:
        if not self._access_token or not self._expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self._expires_at)
        except ValueError:
            return False
        return expires - timedelta(seconds=TOKEN_EARLY_REFRESH_SECONDS) > _now()

    def token(self) -> str:
        if not self._still_valid():
            self.refresh()
        return self._access_token

    def refresh(self) -> None:
        response = _http(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self._config.google_client_id,
                "client_secret": self._config.google_client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code in (400, 401):
            error = _json(response).get("error")
            if error in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise NeedsReconnect("revoked")
        if response.status_code != 200:
            raise CalendarSyncError("busy" if response.status_code >= 500 else "refused")
        data = _json(response)
        access_token = data.get("access_token")
        if not access_token:
            raise CalendarSyncError("refused")
        granted = data.get("scope")
        if granted and CALENDAR_SCOPE not in str(granted).split():
            raise NeedsReconnect("scope")
        self._access_token = access_token
        self._expires_at = _expiry(data.get("expires_in"))
        _db().update_calendar_connection(
            self._user_id,
            access_token_enc=encrypt_token(self._config.session_secret, access_token),
            access_token_expires_at=self._expires_at,
        )


def _json(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _error_reasons(response: requests.Response) -> set[str]:
    error = _json(response).get("error")
    if not isinstance(error, dict):
        return set()
    reasons = {str(error.get("status") or "")}
    for entry in error.get("errors") or []:
        if isinstance(entry, dict):
            reasons.add(str(entry.get("reason") or ""))
    for entry in error.get("details") or []:
        if isinstance(entry, dict):
            reasons.add(str(entry.get("reason") or ""))
    reasons.discard("")
    return reasons


def _raise_for(response: requests.Response) -> None:
    status = response.status_code
    reasons = _error_reasons(response)
    if status == 401:
        raise NeedsReconnect("revoked")
    if status == 403:
        if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
            raise CalendarSyncError("api_disabled")
        if reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}:
            raise CalendarSyncError("busy")
        if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
            raise NeedsReconnect("scope")
    if status == 429 or status >= 500:
        raise CalendarSyncError("busy")
    raise CalendarSyncError("refused")


class CalendarAPI:
    """The handful of Google Calendar calls Life Inbox needs."""

    def __init__(self, tokens: TokenSource):
        self._tokens = tokens

    def _call(self, method: str, path: str, body: Optional[dict] = None):
        for attempt in (1, 2):
            response = _http(
                method,
                API_BASE + path,
                headers={"Authorization": f"Bearer {self._tokens.token()}"},
                json=body,
            )
            if response.status_code == 401 and attempt == 1:
                self._tokens.refresh()  # the access token ran out early
                continue
            return response
        return response

    @staticmethod
    def _calendar_path(calendar_id: str) -> str:
        return f"/calendars/{quote(calendar_id, safe='')}"

    def create_calendar(self, timezone_name: str) -> str:
        response = self._call(
            "POST",
            "/calendars",
            {
                "summary": CALENDAR_NAME,
                "description": CALENDAR_DESCRIPTION,
                "timeZone": timezone_name,
            },
        )
        if response.status_code != 200:
            _raise_for(response)
        calendar_id = _json(response).get("id")
        if not calendar_id:
            raise CalendarSyncError("refused")
        return str(calendar_id)

    def delete_calendar(self, calendar_id: str) -> None:
        response = self._call("DELETE", self._calendar_path(calendar_id))
        if response.status_code not in (200, 204, 404, 410):
            _raise_for(response)

    def insert_event(self, calendar_id: str, body: dict) -> str:
        response = self._call("POST", f"{self._calendar_path(calendar_id)}/events", body)
        if response.status_code == 404:
            raise CalendarGone()
        if response.status_code != 200:
            _raise_for(response)
        event_id = _json(response).get("id")
        if not event_id:
            raise CalendarSyncError("refused")
        return str(event_id)

    def update_event(self, calendar_id: str, event_id: str, body: dict) -> bool:
        """False when the event no longer exists (someone deleted it)."""
        path = f"{self._calendar_path(calendar_id)}/events/{quote(event_id, safe='')}"
        response = self._call("PUT", path, body)
        if response.status_code in (404, 410):
            return False
        if response.status_code != 200:
            _raise_for(response)
        return True

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        path = f"{self._calendar_path(calendar_id)}/events/{quote(event_id, safe='')}"
        response = self._call("DELETE", path)
        if response.status_code not in (200, 204, 404, 410):
            _raise_for(response)


# --- what goes on the calendar --------------------------------------------------------


def event_for_task(task: dict, *, timezone_name: str, app_url: str) -> dict:
    """The calendar event for one open task that has a due date."""
    due = date.fromisoformat(task["normalized_due_on"])
    priority = task.get("priority") or "Medium"
    lines = [f"{priority} priority task from Life Inbox."]
    if task.get("timing_text"):
        lines.append(f"When: {task['timing_text']}")
    lines.append(f"Open Life Inbox: {app_url}/")
    body = {
        "summary": (task.get("text") or "Task")[:500],
        "description": "\n".join(lines),
        # A task is not a meeting: it never shows you as busy.
        "transparency": "transparent",
        "extendedProperties": {"private": {"lifeInboxTaskId": str(task["id"])}},
    }
    color = PRIORITY_COLORS.get(priority)
    if color:
        body["colorId"] = color
    if task.get("due_time"):
        start = datetime.combine(due, time.fromisoformat(task["due_time"]))
        end = start + timedelta(minutes=EVENT_MINUTES)
        body["start"] = {"dateTime": start.isoformat(), "timeZone": timezone_name}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": timezone_name}
    else:
        body["start"] = {"date": due.isoformat()}
        body["end"] = {"date": (due + timedelta(days=1)).isoformat()}
    return body


def fingerprint(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _wanted_events(user_id: int, config: AppConfig, timezone_name: str) -> dict:
    """{task_id: event body} for this person's open tasks that have a date."""
    previous = _db().get_current_owner_id()
    _db().set_current_owner_id(user_id)
    try:
        board = _db().get_attention_board()
    finally:
        _db().set_current_owner_id(previous)
    wanted = {}
    for tasks in board.values():
        if not isinstance(tasks, list):
            continue  # e.g. reference_date
        for task in tasks:
            if task.get("normalized_due_on"):
                wanted[task["id"]] = event_for_task(
                    task, timezone_name=timezone_name, app_url=config.public_url
                )
    return wanted


# --- syncing ----------------------------------------------------------------------------


def sync_tasks(config: AppConfig, user_id: int) -> Optional[dict]:
    """Bring this person's Life Inbox calendar up to date.

    Returns counts of what changed, or None if nothing could be synced (no
    connection, or an error that is saved on the connection for Settings).
    """
    from app.attention.dates import configured_timezone

    connection = _db().get_calendar_connection(user_id)
    if connection is None or connection["status"] != CALENDAR_CONNECTED:
        return None
    try:
        api = CalendarAPI(TokenSource(config, connection))
        counts = _sync(
            config, user_id, api, connection["calendar_id"], str(configured_timezone())
        )
    except NeedsReconnect as exc:
        _db().update_calendar_connection(
            user_id, status=CALENDAR_NEEDS_RECONNECT, last_error=exc.message
        )
        return None
    except CalendarSyncError as exc:
        _db().update_calendar_connection(user_id, last_error=exc.message)
        return None
    except requests.RequestException:
        _db().update_calendar_connection(user_id, last_error=MESSAGES["offline"])
        return None
    _db().update_calendar_connection(user_id, last_synced_at=_now().isoformat(), last_error=None)
    return counts


def _sync(
    config: AppConfig,
    user_id: int,
    api: CalendarAPI,
    calendar_id: Optional[str],
    timezone_name: str,
    *,
    recreate_if_gone: bool = True,
) -> dict:
    counts = {"added": 0, "updated": 0, "removed": 0}
    wanted = _wanted_events(user_id, config, timezone_name)
    existing = _db().list_calendar_task_events(user_id)
    if not calendar_id:
        if not wanted and not existing:
            return counts  # nothing to show yet; make the calendar when needed
        calendar_id = api.create_calendar(timezone_name)
        _db().replace_calendar_id(user_id, calendar_id)
        existing = {}
    try:
        for task_id, body in wanted.items():
            mark = fingerprint(body)
            saved = existing.get(task_id)
            if saved and saved["fingerprint"] == mark:
                continue
            if saved and api.update_event(calendar_id, saved["event_id"], body):
                counts["updated"] += 1
                _db().save_calendar_task_event(user_id, task_id, saved["event_id"], mark)
                continue
            event_id = api.insert_event(calendar_id, body)
            counts["added"] += 1
            _db().save_calendar_task_event(user_id, task_id, event_id, mark)
        for task_id, saved in existing.items():
            if task_id in wanted:
                continue
            api.delete_event(calendar_id, saved["event_id"])
            counts["removed"] += 1
            _db().delete_calendar_task_event(user_id, task_id)
    except CalendarGone:
        # The person deleted the Life Inbox calendar in Google Calendar while
        # still connected here: make a fresh one and put the tasks back.
        if not recreate_if_gone:
            raise CalendarSyncError("refused")
        calendar_id = api.create_calendar(timezone_name)
        _db().replace_calendar_id(user_id, calendar_id)
        return _sync(
            config, user_id, api, calendar_id, timezone_name, recreate_if_gone=False
        )
    return counts


def disconnect(config: AppConfig, user_id: int) -> dict:
    """Remove the Life Inbox calendar, give the access back, forget the tokens.

    The connection is always forgotten here, even if Google can't be reached,
    so "Disconnect" never leaves someone stuck.
    """
    connection = _db().get_calendar_connection(user_id)
    if connection is None:
        return {"disconnected": False, "calendar_removed": False, "access_removed": False}
    calendar_removed = not connection["calendar_id"]
    access_removed = False
    refresh_token = decrypt_token(config.session_secret, connection["refresh_token_enc"])
    if connection["calendar_id"] and refresh_token:
        try:
            api = CalendarAPI(TokenSource(config, connection))
            api.delete_calendar(connection["calendar_id"])
            calendar_removed = True
        except (NeedsReconnect, CalendarSyncError, requests.RequestException):
            logger.warning("Could not remove the Life Inbox calendar on disconnect")
    if refresh_token:
        try:
            response = _http("POST", REVOKE_URL, data={"token": refresh_token})
            # 400 means Google already considers the token dead.
            access_removed = response.status_code in (200, 400)
        except requests.RequestException:
            logger.warning("Could not revoke Google Calendar access on disconnect")
    _db().delete_calendar_connection(user_id)
    return {
        "disconnected": True,
        "calendar_removed": calendar_removed,
        "access_removed": access_removed,
    }


def calendar_status(config: AppConfig, user_id: int, *, syncing: bool = False) -> dict:
    """What the Settings page shows about this person's calendar connection."""
    available = calendar_feature_enabled(config)
    connection = _db().get_calendar_connection(user_id) if available else None
    status = {
        "available": available,
        "connected": False,
        "status": "not_connected" if available else "unavailable",
        "email": None,
        "calendar_name": CALENDAR_NAME,
        "event_count": 0,
        "last_synced_at": None,
        "last_error": None,
        "syncing": False,
        "calendar_url": GOOGLE_CALENDAR_URL,
    }
    if connection is None:
        return status
    status.update(
        connected=connection["status"] == CALENDAR_CONNECTED,
        status=connection["status"],
        email=connection["google_email"],
        event_count=_db().count_calendar_task_events(user_id),
        last_synced_at=connection["last_synced_at"],
        last_error=connection["last_error"],
        syncing=syncing and connection["status"] == CALENDAR_CONNECTED,
    )
    return status


# --- background worker -------------------------------------------------------------------


class CalendarSyncer:
    """Runs syncs one at a time in a background thread.

    Asking again for someone whose sync is already waiting does nothing extra;
    a change made while their sync is running queues one more, so the
    calendar always ends up matching the latest tasks.
    """

    def __init__(self, run_for_owner: Callable[[int], None]) -> None:
        self._run_for_owner = run_for_owner
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="calendar-sync")
        self._lock = threading.Lock()
        self._queued: set[int] = set()
        self._running: Optional[int] = None
        self._owner_locks: dict[int, threading.Lock] = {}

    def owner_lock(self, owner_id: int) -> threading.Lock:
        """Held while syncing someone; disconnecting takes it too."""
        with self._lock:
            return self._owner_locks.setdefault(owner_id, threading.Lock())

    def is_busy(self, owner_id: int) -> bool:
        with self._lock:
            return owner_id in self._queued or self._running == owner_id

    def schedule(self, owner_id: int) -> None:
        with self._lock:
            if owner_id in self._queued:
                return
            self._queued.add(owner_id)
        try:
            self._executor.submit(self._job, owner_id)
        except RuntimeError:
            with self._lock:
                self._queued.discard(owner_id)
            logger.exception("Could not schedule a calendar sync")

    def _job(self, owner_id: int) -> None:
        with self._lock:
            self._queued.discard(owner_id)
            self._running = owner_id
        try:
            with self.owner_lock(owner_id):
                self._run_for_owner(owner_id)
        except Exception:
            logger.exception("Calendar sync failed")
        finally:
            with self._lock:
                self._running = None

    def wait_for_idle(self, timeout: float = 30.0) -> None:
        """Block until every sync scheduled so far has finished (used by tests)."""
        deadline = time_module.monotonic() + timeout
        while True:
            self._executor.submit(lambda: None).result(timeout=timeout)
            with self._lock:
                if not self._queued and self._running is None:
                    return
            if time_module.monotonic() > deadline:
                raise TimeoutError("calendar sync still running")
