import logging
import re
import secrets
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from starlette.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

from app.auto_analysis import AutoAnalyzer, auto_analysis_enabled
from app.capture.image_extract import process_image_bytes, validate_image_upload
from app.capture.pdf_extract import extract_pdf_text, validate_pdf_upload
from app.capture.storage import (
    read_stored_bytes,
    resolve_storage_path,
    store_audio_bytes,
    store_image_bytes,
    store_pdf_bytes,
    store_thumbnail_bytes,
    thumbnail_relative_path,
)
from app.capture.url_fetch import fetch_url, validate_public_http_url
from app.capture.voice_extract import process_voice_bytes, validate_audio_upload
from app.config import load_config
from app.privacy import redact
from app.item_kinds import KIND_FIELDS, make_card
from app.expenses import expense_summary
from app.entities import ENTITY_TYPES, extract_entities, summarize_entity
from app.attention import dates as attention_dates
from app.flashcards import make_cards, next_review
from app.resurface import resurface
from app.api_access import WEBHOOK_EVENTS, deliver_webhooks, new_token
from app.database import (
    AnalysisAlreadyRunningError,
    AuthRequiredError,
    task_is_owned,
    add_api_token,
    add_webhook,
    delete_api_token,
    delete_webhook,
    list_api_tokens,
    list_webhooks,
    webhooks_for_event,
    add_flashcards,
    get_flashcard,
    list_due_flashcards,
    save_flashcard_review,
    get_entity_page,
    list_entities,
    save_item_entities,
    list_items_by_kind,
    save_item_card,
    list_ai_call_log,
    set_item_sensitive,
    add_image_item_to_db,
    add_item_to_db,
    add_note_to_db,
    add_pdf_item_to_db,
    add_url_item_to_db,
    add_voice_item_to_db,
    complete_image_item_processing,
    complete_pdf_item_processing,
    complete_voice_item_processing,
    complete_analysis_run,
    complete_url_item_processing,
    confirm_task_timing,
    create_topic_in_db,
    delete_item_from_db,
    delete_note_from_db,
    dismiss_relation_in_db,
    fail_analysis_run,
    fail_item_processing,
    get_attention_board,
    list_pending_tasks_by_priority,
    get_current_owner_id,
    get_daily_briefing,
    get_item_from_db,
    get_item_insight,
    get_latest_successful_analysis,
    get_organization_dashboard,
    get_task_by_id,
    init_db,
    list_items_from_db,
    list_notes_from_db,
    list_pending_notes_from_db,
    list_relations_from_db,
    list_tasks_from_db,
    list_topics_from_db,
    mark_item_processing,
    save_calendar_receipt,
    set_current_owner_id,
    set_task_reminder_in_db,
    set_task_status,
    snooze_task_in_db,
    start_analysis_run,
    update_item_extracted_text,
    update_item_in_db,
    update_item_insight_overrides,
    update_note_in_db,
    update_topic_in_db,
)
import app.integrations.google_calendar as google_calendar
from app.integrations.calendar import (
    CalendarWriteBlockedError,
    get_calendar_adapter,
    get_draft,
    store_draft,
)
from app.organizer import analyze_inbox_with_openai, validate_analysis_result
from app.retrieval.ask import build_grounded_answer
from app.retrieval.search import search_passages
from app.schemas import (
    ApiTokenCreate,
    WebhookCreate,
    FlashcardReview,
    RedactPreviewRequest,
    SensitiveUpdate,
    AskRequest,
    AskResponse,
    ProfileUpdate,
    UserSettingsUpdate,
    CalendarConfirmRequest,
    CalendarPreviewRequest,
    ExtractedTextUpdate,
    InboxItem,
    InboxItemCreate,
    InboxItemUpdate,
    InsightOverrideUpdate,
    ItemInsight,
    ItemRelation,
    Note,
    NoteCreate,
    NoteUpdate,
    PersistedAnalysis,
    Priority,
    SearchHit,
    SmartCardCreate,
    SmartCardUpdate,
    Task,
    TaskListStatus,
    TaskReminderRequest,
    TaskSnoozeRequest,
    TaskTimingConfirm,
    Topic,
    TopicCreate,
    TopicUpdate,
    UrlItemCreate,
)


APP_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

# Default strictness: beta/production refuse to start when misconfigured
# (SQLite, short session secret, dev login on, missing Google keys, http URL).
_startup_config = load_config()
_docs_kwargs = (
    {}
    if _startup_config.environment == "local"
    else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser must re-check on every load.

    Without this, a browser can keep running an old app.js against a newer
    page and break in confusing ways. The ETag still makes unchanged files a
    cheap 304 response.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="Life Inbox", **_docs_kwargs)
app.mount(
    "/static",
    RevalidatingStaticFiles(directory=APP_DIR / "static"),
    name="static",
)

init_db()

# One analysis at a time: a manual run and an automatic one never overlap, so
# the same notes are never analyzed twice.
_analysis_lock = threading.Lock()


def _run_analysis(scope: str = "new", *, only_if_pending: bool = False):
    """Analyze notes and persist the result.

    scope "new" analyzes notes that are new or edited since their last
    analysis; "all" re-analyzes the whole inbox. With ``only_if_pending`` a
    "new" run does nothing (returns None) when there is nothing to analyze.
    """
    with _analysis_lock:
        run_id = None
        try:
            previous = None
            if scope == "new":
                previous = get_latest_successful_analysis()
                notes = list_pending_notes_from_db()
                if only_if_pending and not notes:
                    return None
                if (
                    previous is not None
                    and not notes
                    and not previous["is_stale"]
                ):
                    return {**previous, "analyzed_item_ids": []}
            else:
                notes = list_notes_from_db()

            run_id = start_analysis_run()
            raw_result = analyze_inbox_with_openai(notes)
            result = validate_analysis_result(raw_result, notes)
            return complete_analysis_run(run_id, result, previous=previous)
        except Exception:
            if run_id is not None:
                try:
                    fail_analysis_run(run_id)
                except Exception:
                    logger.exception("Failed to mark analysis run as failed")
            raise


def _auto_analyze_owner(owner_id: int) -> None:
    set_current_owner_id(owner_id)
    try:
        _run_analysis("new", only_if_pending=True)
    finally:
        set_current_owner_id(None)
    # New tasks may have due dates: put them on the calendar.
    _schedule_calendar_sync(owner_id)


auto_analyzer = AutoAnalyzer(_auto_analyze_owner)


def _sync_calendar_for_owner(owner_id: int) -> None:
    google_calendar.sync_tasks(load_config(strict=False), owner_id)


calendar_syncer = google_calendar.CalendarSyncer(_sync_calendar_for_owner)

# Requests after which tasks may have changed (added, re-dated, finished,
# deleted with their note). Opening the inbox also retries a failed sync.
CALENDAR_SYNC_PREFIXES = ("/api/items", "/api/notes", "/api/tasks", "/api/analyze")


def _schedule_calendar_sync(owner_id: int) -> None:
    """Queue a calendar sync if this person keeps tasks on Google Calendar."""
    from app.database import calendar_sync_wanted

    try:
        if not google_calendar.calendar_feature_enabled(load_config(strict=False)):
            return
        if calendar_sync_wanted(owner_id):
            calendar_syncer.schedule(owner_id)
    except Exception:
        logger.exception("Could not queue a calendar sync")


def _may_change_tasks(method: str, path: str) -> bool:
    if method == "GET":
        return path == "/"
    return method in STATE_CHANGING_METHODS and path.startswith(CALENDAR_SYNC_PREFIXES)


def _trigger_auto_analysis() -> None:
    """Analyze any new note in the background as soon as it is saved."""
    if not auto_analysis_enabled():
        return
    owner_id = get_current_owner_id()
    if owner_id is None:
        return
    auto_analyzer.schedule(owner_id)


PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/google/start",
    "/api/auth/google/callback",
    "/api/auth/dev-login",
    "/api/auth/logout",
    "/api/auth/options",
}
STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SIGNED_IN_PAGES = {"/", "/profile", "/settings"}  # pages that send visitors to /login


def _owner_for_api_token(request: Request) -> Optional[int]:
    """The owner of the API token in the Authorization header, or None."""
    from app.api_access import hash_token, token_from_header
    from app.database import owner_for_token_hash

    api_token = token_from_header(request.headers.get("authorization"))
    if not api_token:
        return None
    return owner_for_token_hash(hash_token(api_token))


@app.middleware("http")
async def bind_owner_from_session(request, call_next):
    """Identify the user from the session cookie and gate the API.

    The signed-in user's id is bound to this request only (a ContextVar), so
    concurrent requests from different users can never see each other's id.
    """
    from app.auth import current_user_from_request, origin_allowed
    from app.database import get_or_create_default_owner_id, set_current_owner_id

    config = load_config(strict=False)
    path = request.url.path
    is_api = path.startswith("/api/")

    if (
        is_api
        and request.method in STATE_CHANGING_METHODS
        and not origin_allowed(request, config)
    ):
        return JSONResponse(
            {"detail": "Cross-origin request blocked."}, status_code=403
        )

    owner_id = None
    if is_api or path in SIGNED_IN_PAGES:
        # Database lookups run in a worker thread so they never block the
        # event loop (and every other user's requests).
        user = await run_in_threadpool(current_user_from_request, request)
        if user is not None:
            owner_id = user["id"]
        elif is_api and request.headers.get("authorization"):
            # Programs (scripts, the MCP server) send "Authorization: Bearer li_...".
            token_owner = await run_in_threadpool(_owner_for_api_token, request)
            if token_owner is None:
                return JSONResponse({"detail": "Invalid API token"}, status_code=401)
            owner_id = token_owner
        elif config.local_anonymous_allowed:
            owner_id = await run_in_threadpool(get_or_create_default_owner_id)

    if is_api and path not in PUBLIC_API_PATHS and owner_id is None:
        return JSONResponse(
            {"detail": "Authentication required."}, status_code=401
        )
    if path in SIGNED_IN_PAGES and owner_id is None:
        return RedirectResponse("/login", status_code=302)

    set_current_owner_id(owner_id)
    try:
        response = await call_next(request)
    finally:
        set_current_owner_id(None)
    if (
        owner_id is not None
        and response.status_code < 400
        and _may_change_tasks(request.method, path)
    ):
        await run_in_threadpool(_schedule_calendar_sync, owner_id)
    return response


@app.exception_handler(AuthRequiredError)
async def auth_required_handler(request, exc):
    return JSONResponse({"detail": "Authentication required."}, status_code=401)


@app.get("/api/health")
def health():
    from app.config import config_warnings

    try:
        config = load_config(strict=False)
        return {
            "status": "ok",
            "environment": config.environment,
            "database": "postgres" if config.is_postgres else "sqlite",
            "auth_mode": config.auth_mode,
            "warnings": config_warnings(config),
        }
    except Exception as exc:
        logger.exception("Health config check failed")
        return {"status": "degraded", "error": str(exc)}


@app.get("/api/auth/options")
def auth_options():
    """Which sign-in controls the browser should show (public, no user data)."""
    config = load_config(strict=False)
    return {
        "google_enabled": config.google_enabled,
        "dev_login_enabled": bool(
            config.allow_dev_login and config.environment == "local"
        ),
        "local_anonymous": config.local_anonymous_allowed,
    }


@app.post("/api/me/avatar")
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


@app.get("/api/me/avatar")
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
    # The URL carries a per-upload version, so the browser may keep it.
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.delete("/api/me/avatar")
def remove_avatar():
    """Remove your uploaded photo; the Google picture shows again."""
    from app.database import require_owner_id, set_user_avatar_path
    from app.storage_backend import get_storage_backend

    user, previous = set_user_avatar_path(require_owner_id(), None)
    get_storage_backend().delete(previous)
    return user


@app.patch("/api/me/profile")
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


@app.patch("/api/me/settings")
def save_settings(payload: UserSettingsUpdate):
    """Change notification and theme settings. Returns every setting."""
    from app.database import require_owner_id, update_user_settings

    changes = {
        key: value
        for key, value in payload.model_dump().items()
        if key in payload.model_fields_set and value is not None
    }
    return update_user_settings(require_owner_id(), changes)


GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/connections"


@app.get("/api/me/connections")
def connected_apps():
    """The outside services this account uses, and whether each is on."""
    import os

    from app.database import get_user_by_id, require_owner_id

    owner_id = require_owner_id()
    user = get_user_by_id(owner_id)
    calendar = google_calendar.calendar_status(
        load_config(strict=False), owner_id, syncing=calendar_syncer.is_busy(owner_id)
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
                "manage_url": None,  # Settings shows Connect / Disconnect instead
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


@app.get("/api/auth/me")
def auth_me():
    from app.database import get_current_owner_id, get_user_by_id

    owner_id = get_current_owner_id()
    user = get_user_by_id(owner_id) if owner_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


@app.post("/api/auth/dev-login")
def auth_dev_login(payload: dict, request: Request, response: Response):
    from app.auth import dev_login_user, start_session

    email = str(payload.get("email", "")).strip()
    display_name = payload.get("display_name")
    user = dev_login_user(email, display_name=display_name)
    start_session(response, request, user["id"])
    return user


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    from app.auth import end_session

    end_session(response, request)
    return {"status": "logged_out"}


@app.post("/api/auth/logout-all")
def auth_logout_all(request: Request, response: Response):
    """Sign out of every device (all of this user's sessions)."""
    from app.auth import clear_session_cookie
    from app.database import require_owner_id, revoke_all_auth_sessions

    revoked = revoke_all_auth_sessions(require_owner_id())
    clear_session_cookie(response)
    return {"status": "logged_out_everywhere", "sessions_revoked": revoked}


@app.get("/api/auth/google/start")
def auth_google_start(next: Optional[str] = None):
    from app.google_oauth import build_authorization

    config = load_config(strict=False)
    if not config.google_enabled:
        raise HTTPException(
            status_code=501,
            detail=(
                "Google OAuth is not configured. Set LIFE_INBOX_AUTH_MODE=google, "
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
            ),
        )
    url, cookie_value = build_authorization(config, next)
    return _redirect_to_google(config, url, cookie_value)


def _redirect_to_google(config, url: str, cookie_value: str) -> RedirectResponse:
    from app.google_oauth import OAUTH_COOKIE, OAUTH_COOKIE_TTL_SECONDS

    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        key=OAUTH_COOKIE,
        value=cookie_value,
        max_age=OAUTH_COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="lax",  # must be Lax: Google redirects back via a top-level GET
        secure=config.cookie_secure,
        path="/api/auth/google",
    )
    return response


@app.get("/api/auth/google/calendar/start")
def calendar_connect_start():
    """Ask Google for permission to keep a "Life Inbox" calendar (signed in only)."""
    from app.database import get_user_by_id, require_owner_id

    config = load_config(strict=False)
    if not google_calendar.calendar_feature_enabled(config):
        raise HTTPException(
            status_code=404, detail="Google Calendar is not available on this server."
        )
    user = get_user_by_id(require_owner_id())
    url, cookie_value = google_calendar.build_connect_authorization(
        config, user_id=user["id"], login_hint=user["email"]
    )
    return _redirect_to_google(config, url, cookie_value)


@app.get("/api/auth/google/callback")
def auth_google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    from app.auth import start_session
    from app.database import upsert_google_user
    from app.google_oauth import (
        OAUTH_COOKIE,
        GoogleAuthError,
        email_allowed,
        fetch_google_identity,
        read_state_cookie,
    )

    config = load_config(strict=False)
    if not config.google_enabled:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured.")

    def fail(reason: str) -> RedirectResponse:
        failed = RedirectResponse(f"/login?error={reason}", status_code=302)
        failed.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
        return failed

    saved, state_error = None, "state"
    try:
        saved = read_state_cookie(config, request.cookies.get(OAUTH_COOKIE), state)
    except GoogleAuthError as exc:
        state_error = exc.code
    if saved is not None and saved["purpose"] == "calendar":
        return _finish_calendar_connect(config, saved, code, error)
    if error:
        return fail("denied")
    if saved is None:
        return fail(state_error)
    try:
        if not code:
            raise GoogleAuthError("failed")
        identity = fetch_google_identity(config, code, saved["verifier"])
        if not email_allowed(config, identity["email"]):
            raise GoogleAuthError("not_allowed")
        user = upsert_google_user(
            sub=identity["sub"],
            email=identity["email"],
            name=identity["name"],
            picture=identity.get("picture"),
        )
    except GoogleAuthError as exc:
        return fail(exc.code)
    except ValueError:
        return fail("failed")
    except Exception:
        logger.exception("Google sign-in failed")
        return fail("failed")

    response = RedirectResponse(saved["next"], status_code=302)
    response.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
    start_session(response, request, user["id"])
    return response


def _finish_calendar_connect(config, saved: dict, code, error) -> RedirectResponse:
    """Second half of "Connect Google Calendar"; always lands back on Settings."""
    from app.database import get_current_owner_id
    from app.google_oauth import OAUTH_COOKIE

    def done(result: str) -> RedirectResponse:
        response = RedirectResponse(f"/settings?calendar={result}", status_code=302)
        response.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
        return response

    if error:
        return done("denied")
    owner_id = get_current_owner_id()
    if owner_id is None or owner_id != saved["user_id"]:
        # Started by someone else (or the session ended): never attach a
        # calendar to the wrong account.
        return done("wrong_user")
    if not google_calendar.calendar_feature_enabled(config):
        return done("unavailable")
    if not code:
        return done("failed")
    try:
        google_calendar.finish_connect(config, owner_id, code, saved["verifier"])
    except google_calendar.CalendarConnectError as exc:
        return done(exc.code)
    except Exception:
        logger.exception("Connecting Google Calendar failed")
        return done("failed")
    calendar_syncer.schedule(owner_id)
    return done("connected")


LOGIN_ERRORS = {
    "denied": "Sign-in was cancelled.",
    "state": "That sign-in link expired. Please try again.",
    "email_unverified": "Your Google email address is not verified.",
    "not_allowed": "This Google account is not on the invite list.",
    "failed": "Google sign-in failed. Please try again.",
}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
    import html

    from app.auth import current_user_from_request

    config = load_config(strict=False)
    if config.local_anonymous_allowed or current_user_from_request(request):
        return RedirectResponse("/", status_code=302)
    message = LOGIN_ERRORS.get(error or "", "")
    page = (APP_DIR / "templates" / "login.html").read_text(encoding="utf-8")
    button = (
        GOOGLE_SIGN_IN_BUTTON
        if config.google_enabled
        else '<p class="login-error">Google sign-in is not configured on this server.</p>'
    )
    return _versioned_static_refs(
        page.replace("__ERROR__", html.escape(message)).replace("__BUTTON__", button)
    )


# Google's standard "Sign in with Google" button (multicolour G, neutral
# background), as its branding guidelines ask.
GOOGLE_SIGN_IN_BUTTON = (
    '<a class="gsi-btn" href="/api/auth/google/start">'
    '<svg class="gsi-icon" viewBox="0 0 48 48" aria-hidden="true">'
    '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
    '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
    '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
    '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>'
    "</svg>"
    '<span class="gsi-text">Sign in with Google</span>'
    "</a>"
)


def _process_url_item(item_id: int) -> dict:
    mark_item_processing(item_id)
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    try:
        fetched = fetch_url(item["source_url"])
        processed = complete_url_item_processing(
            item_id,
            title=fetched["title"],
            raw_text=fetched["raw_text"],
            mime_type=fetched.get("mime_type"),
            final_url=fetched.get("final_url"),
        )
        if processed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return processed
    except ValueError as exc:
        failed = fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("URL processing failed")
        failed = fail_item_processing(
            item_id,
            "Unable to fetch or extract this URL.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def _process_pdf_item(item_id: int) -> dict:
    mark_item_processing(item_id)
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = fail_item_processing(
            item_id,
            "Stored PDF file is missing.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        extracted = extract_pdf_text(
            data,
            original_filename=item.get("original_filename"),
        )
        processed = complete_pdf_item_processing(
            item_id,
            title=extracted["title"],
            raw_text=extracted["raw_text"],
            extracted_text=extracted["extracted_text"],
            mime_type=extracted.get("mime_type"),
        )
        if processed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return processed
    except FileNotFoundError:
        failed = fail_item_processing(
            item_id,
            "Stored PDF file is missing.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("PDF processing failed")
        failed = fail_item_processing(
            item_id,
            "Unable to extract text from this PDF.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def _process_image_item(item_id: int) -> dict:
    mark_item_processing(item_id)
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = fail_item_processing(item_id, "Stored image file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        processed_payload = process_image_bytes(
            data,
            original_filename=item.get("original_filename") or "image.png",
            mime_type=item.get("mime_type") or "image/png",
        )
        store_thumbnail_bytes(storage_path, processed_payload["thumbnail_bytes"])
        processed = complete_image_item_processing(
            item_id,
            title=processed_payload["title"],
            raw_text=processed_payload["raw_text"],
            extracted_text=processed_payload["extracted_text"],
            ai_description=processed_payload["ai_description"],
            mime_type=processed_payload.get("mime_type"),
        )
        if processed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return processed
    except FileNotFoundError:
        failed = fail_item_processing(item_id, "Stored image file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("Image processing failed")
        failed = fail_item_processing(
            item_id,
            "Unable to process this image.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def _process_voice_item(item_id: int) -> dict:
    mark_item_processing(item_id)
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = fail_item_processing(item_id, "Stored audio file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        processed_payload = process_voice_bytes(
            data,
            original_filename=item.get("original_filename") or "voice-note.webm",
            mime_type=item.get("mime_type") or "audio/webm",
        )
        processed = complete_voice_item_processing(
            item_id,
            title=processed_payload["title"],
            raw_text=processed_payload["raw_text"],
            extracted_text=processed_payload["extracted_text"],
            mime_type=processed_payload.get("mime_type"),
        )
        if processed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return processed
    except FileNotFoundError:
        failed = fail_item_processing(item_id, "Stored audio file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("Voice processing failed")
        failed = fail_item_processing(
            item_id,
            "Unable to process this voice note.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


@app.post("/api/items", response_model=InboxItem)
def create_item(payload: InboxItemCreate, background_tasks: BackgroundTasks):
    try:
        item = add_item_to_db(payload.raw_text)
        _trigger_auto_analysis()
        queue_webhooks(background_tasks, "item.created", item)
        return item
    except Exception as exc:
        logger.exception("Failed to save inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save item. Please try again.",
        ) from exc
    _trigger_auto_analysis()
    return item


@app.post("/api/items/url", response_model=InboxItem)
def create_url_item(payload: UrlItemCreate, background_tasks: BackgroundTasks):
    try:
        url = validate_public_http_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        item = add_url_item_to_db(url)
    except Exception as exc:
        logger.exception("Failed to save URL item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save URL. Please try again.",
        ) from exc

    processed = _process_url_item(item["id"])
    _trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@app.post("/api/items/pdf", response_model=InboxItem)
async def create_pdf_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename = validate_pdf_upload(
            file.filename or "document.pdf",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_pdf_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store PDF upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store PDF. Please try again.",
        ) from exc

    mime_type = (file.content_type or "application/pdf").split(";", 1)[0].strip()
    try:
        item = add_pdf_item_to_db(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save PDF item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save PDF. Please try again.",
        ) from exc

    processed = _process_pdf_item(item["id"])
    _trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@app.post("/api/items/image", response_model=InboxItem)
async def create_image_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename, detected_mime, extension = validate_image_upload(
            file.filename or "image.png",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_image_bytes(data, extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store image upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store image. Please try again.",
        ) from exc

    mime_type = detected_mime
    if file.content_type:
        declared = file.content_type.split(";", 1)[0].strip().lower()
        if declared in {
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
        }:
            mime_type = declared
    try:
        item = add_image_item_to_db(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save image item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save image. Please try again.",
        ) from exc

    processed = _process_image_item(item["id"])
    _trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@app.post("/api/items/voice", response_model=InboxItem)
async def create_voice_item(
    background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    data = await file.read()
    try:
        safe_filename, detected_mime, extension = validate_audio_upload(
            file.filename or "voice-note.webm",
            file.content_type,
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        storage_path = store_audio_bytes(data, extension)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to store voice upload")
        raise HTTPException(
            status_code=500,
            detail="Unable to store voice note. Please try again.",
        ) from exc

    mime_type = detected_mime
    if file.content_type:
        declared = file.content_type.split(";", 1)[0].strip().lower()
        if declared in {
            "audio/webm",
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
            "audio/mpeg",
            "audio/mp3",
            "audio/mp4",
            "audio/m4a",
            "audio/x-m4a",
            "audio/ogg",
            "video/webm",
        }:
            mime_type = declared if declared != "video/webm" else "audio/webm"
    try:
        item = add_voice_item_to_db(safe_filename, storage_path, mime_type)
    except Exception as exc:
        logger.exception("Failed to save voice item")
        raise HTTPException(
            status_code=500,
            detail="Unable to save voice note. Please try again.",
        ) from exc

    processed = _process_voice_item(item["id"])
    _trigger_auto_analysis()
    queue_webhooks(background_tasks, "item.created", processed)
    return processed


@app.get("/api/items/{item_id}/file")
def get_item_source_file(
    item_id: int,
    variant: Optional[str] = Query(None, max_length=20),
):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"pdf", "image", "voice"} or not item.get(
        "storage_path"
    ):
        raise HTTPException(
            status_code=404,
            detail="No stored file for this item.",
        )
    relative = item["storage_path"]
    media_type = item.get("mime_type") or "application/octet-stream"
    filename = item.get("original_filename") or "download"
    if variant == "thumb" and item["item_type"] == "image":
        relative = thumbnail_relative_path(item["storage_path"])
        media_type = "image/jpeg"
        filename = f"thumb_{Path(filename).stem}.jpg"
    try:
        path = resolve_storage_path(relative)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Stored file not found.") from exc
    if item["item_type"] == "pdf" and not media_type:
        media_type = "application/pdf"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
    )


@app.patch("/api/items/{item_id}/extracted-text", response_model=InboxItem)
def patch_extracted_text(item_id: int, payload: ExtractedTextUpdate):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"image", "voice", "pdf"}:
        raise HTTPException(
            status_code=400,
            detail="Extracted text can only be edited for image, voice, or PDF items.",
        )
    updated = update_item_extracted_text(item_id, payload.extracted_text)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    _trigger_auto_analysis()
    return updated


@app.post("/api/items/{item_id}/retry", response_model=InboxItem)
def retry_item_processing(item_id: int):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    if item["item_type"] not in {"url", "pdf", "image", "voice"}:
        raise HTTPException(
            status_code=400,
            detail="Only capture items (URL/PDF/image/voice) can be retried here.",
        )
    if item["processing_status"] == "ready":
        return item
    if item["item_type"] == "url":
        processed = _process_url_item(item_id)
    elif item["item_type"] == "pdf":
        processed = _process_pdf_item(item_id)
    elif item["item_type"] == "image":
        processed = _process_image_item(item_id)
    else:
        processed = _process_voice_item(item_id)
    _trigger_auto_analysis()
    return processed


@app.get("/api/items", response_model=List[InboxItem])
def get_items(
    search: Optional[str] = Query(None, alias="q", max_length=200),
    item_type: Optional[str] = Query(None, max_length=100),
    category: Optional[str] = Query(None, max_length=100),
    priority: Optional[Priority] = Query(None),
    processing_status: Optional[str] = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("oldest", pattern="^(oldest|newest)$"),
):
    try:
        return list_items_from_db(
            search=search,
            item_type=item_type,
            category=category,
            priority=priority,
            processing_status=processing_status,
            limit=limit,
            offset=offset,
            sort=sort,
        )
    except Exception as exc:
        logger.exception("Failed to load inbox items")
        raise HTTPException(
            status_code=500,
            detail="Unable to load items. Please try again.",
        ) from exc


@app.get("/api/items/{item_id}", response_model=InboxItem)
def read_item(item_id: int):
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    return item


@app.put("/api/items/{item_id}", response_model=InboxItem)
def update_item(item_id: int, payload: InboxItemUpdate):
    try:
        item = update_item_in_db(item_id, payload.raw_text)
    except Exception as exc:
        logger.exception("Failed to update inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to update item. Please try again.",
        ) from exc

    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    _trigger_auto_analysis()
    return item


@app.delete(
    "/api/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_item(item_id: int):
    try:
        deleted = delete_item_from_db(item_id)
    except Exception as exc:
        logger.exception("Failed to delete inbox item")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete item. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Item not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/notes", response_model=Note, deprecated=True)
def create_note(payload: NoteCreate):
    try:
        note = add_note_to_db(payload.text)
    except Exception as exc:
        logger.exception("Failed to save note")
        raise HTTPException(
            status_code=500,
            detail="Unable to save note. Please try again.",
        ) from exc
    _trigger_auto_analysis()
    return note


@app.get("/api/notes", response_model=List[Note], deprecated=True)
def get_notes():
    try:
        return list_notes_from_db()
    except Exception as exc:
        logger.exception("Failed to load notes")
        raise HTTPException(
            status_code=500,
            detail="Unable to load notes. Please try again.",
        ) from exc


@app.put("/api/notes/{note_id}", response_model=Note, deprecated=True)
def update_note(note_id: int, payload: NoteUpdate):
    try:
        note = update_note_in_db(note_id, payload.text)
    except Exception as exc:
        logger.exception("Failed to update note")
        raise HTTPException(
            status_code=500,
            detail="Unable to update note. Please try again.",
        ) from exc

    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    _trigger_auto_analysis()
    return note


@app.delete(
    "/api/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    deprecated=True,
)
def delete_note(note_id: int):
    try:
        deleted = delete_note_from_db(note_id)
    except Exception as exc:
        logger.exception("Failed to delete note")
        raise HTTPException(
            status_code=500,
            detail="Unable to delete note. Please try again.",
        ) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/tasks", response_model=List[Task])
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


@app.post("/api/tasks/{task_id}/complete", response_model=Task)
def complete_task(task_id: int):
    return _transition_task(task_id, "completed")


@app.post("/api/tasks/{task_id}/reopen", response_model=Task)
def reopen_task(task_id: int):
    return _transition_task(task_id, "open")


@app.post("/api/tasks/{task_id}/dismiss", response_model=Task)
def dismiss_task(task_id: int):
    return _transition_task(task_id, "dismissed")


@app.post("/api/tasks/{task_id}/confirm-timing", response_model=Task)
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


@app.post("/api/tasks/{task_id}/snooze", response_model=Task)
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


@app.post("/api/tasks/{task_id}/reminder", response_model=Task)
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


@app.get("/api/attention")
def attention_board():
    try:
        return get_attention_board()
    except Exception as exc:
        logger.exception("Failed to load attention board")
        raise HTTPException(
            status_code=500,
            detail="Unable to load attention board. Please try again.",
        ) from exc


@app.get("/api/notifications")
def notifications():
    """Pending tasks for the bell, highest priority first, per your settings."""
    from app.database import get_user_settings, require_owner_id

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


@app.get("/api/briefing")
def daily_briefing():
    try:
        return get_daily_briefing()
    except Exception as exc:
        logger.exception("Failed to load daily briefing")
        raise HTTPException(
            status_code=500,
            detail="Unable to load daily briefing. Please try again.",
        ) from exc


@app.get("/api/calendar/status")
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


@app.post("/api/calendar/preview")
def calendar_preview(payload: CalendarPreviewRequest):
    from datetime import date as date_cls

    from app.attention.dates import configured_timezone, parse_timing_phrase

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


@app.post("/api/calendar/confirm")
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


@app.get("/api/calendar/google")
def google_calendar_status():
    from app.database import require_owner_id

    owner_id = require_owner_id()
    return google_calendar.calendar_status(
        load_config(strict=False), owner_id, syncing=calendar_syncer.is_busy(owner_id)
    )


@app.post("/api/calendar/google/sync")
def google_calendar_sync_now():
    from app.database import require_owner_id

    owner_id = require_owner_id()
    config = load_config(strict=False)
    if not google_calendar.calendar_status(config, owner_id)["connected"]:
        raise HTTPException(status_code=409, detail="Google Calendar is not connected.")
    calendar_syncer.schedule(owner_id)
    return google_calendar.calendar_status(config, owner_id, syncing=True)


@app.delete("/api/calendar/google")
def google_calendar_disconnect():
    """Take the Life Inbox calendar off Google Calendar and give the access back."""
    from app.database import require_owner_id

    owner_id = require_owner_id()
    config = load_config(strict=False)
    with calendar_syncer.owner_lock(owner_id):  # never mid-sync
        result = google_calendar.disconnect(config, owner_id)
    if not result["disconnected"]:
        raise HTTPException(status_code=404, detail="Google Calendar is not connected.")
    return {**result, "calendar": google_calendar.calendar_status(config, owner_id)}
from app.assistant_routes import router as assistant_router  # noqa: E402

app.include_router(assistant_router)


@app.get("/api/topics", response_model=List[Topic])
def get_topics(include_archived: bool = Query(False)):
    try:
        return list_topics_from_db(include_archived=include_archived)
    except Exception as exc:
        logger.exception("Failed to load topics")
        raise HTTPException(
            status_code=500,
            detail="Unable to load topics. Please try again.",
        ) from exc


@app.post("/api/topics", response_model=Topic)
def create_topic(payload: TopicCreate):
    try:
        return create_topic_in_db(payload.name.strip(), payload.description)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create topic")
        raise HTTPException(
            status_code=500,
            detail="Unable to create topic. Please try again.",
        ) from exc


@app.patch("/api/topics/{topic_id}", response_model=Topic)
def patch_topic(topic_id: int, payload: TopicUpdate):
    try:
        topic = update_topic_in_db(
            topic_id,
            name=payload.name.strip() if payload.name else None,
            description=payload.description,
            archived=payload.archived,
        )
    except Exception as exc:
        logger.exception("Failed to update topic")
        raise HTTPException(
            status_code=500,
            detail="Unable to update topic. Please try again.",
        ) from exc
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return topic


# --- Smart templates --------------------------------------------------------


@app.post("/api/items/{item_id}/card", response_model=InboxItem)
def create_item_card(item_id: int):
    """Work out what kind of item this is (recipe, book, ...) and its fields."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or item["title"] or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Item has no text yet")

    card = make_card(text, is_sensitive=item["is_sensitive"])
    return save_item_card(item_id, card["kind"], card["fields"])


@app.put("/api/items/{item_id}/card", response_model=InboxItem)
def update_item_card(item_id: int, payload: SmartCardUpdate):
    """Manually update or patch a card's kind and fields."""
    if payload.kind not in KIND_FIELDS and payload.kind != "other":
        raise HTTPException(status_code=400, detail="Unknown kind")
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    updated = save_item_card(item_id, payload.kind, payload.fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return updated


@app.post("/api/cards", response_model=InboxItem)
def create_card_directly(payload: SmartCardCreate):
    """Create a new item and smart card directly."""
    if payload.kind not in KIND_FIELDS and payload.kind != "other":
        raise HTTPException(status_code=400, detail="Unknown kind")
    title = payload.title or payload.fields.get("name") or payload.fields.get("title") or payload.fields.get("vendor") or f"{payload.kind.title()} Note"
    raw_lines = [f"**{title}** ({payload.kind})"]
    for k, v in payload.fields.items():
        if isinstance(v, list):
            raw_lines.append(f"{k.capitalize()}: {', '.join(str(x) for x in v)}")
        else:
            raw_lines.append(f"{k.capitalize()}: {v}")
    raw_text = "\n".join(raw_lines)
    item = add_item_to_db(raw_text)
    updated = save_item_card(item["id"], payload.kind, payload.fields)
    return updated


@app.get("/api/cards", response_model=List[InboxItem])
def list_cards(kind: Optional[str] = Query(None)):
    """Items that have a smart card, optionally only one kind."""
    if kind is not None and kind not in KIND_FIELDS:
        raise HTTPException(status_code=400, detail="Unknown kind")
    return list_items_by_kind(kind)


@app.get("/api/cards/kinds")
def list_card_kinds():
    """The kinds we recognise and the fields each one has."""
    return KIND_FIELDS


# --- Expenses (receipts) ---------------------------------------------------


@app.get("/api/expenses")
def get_expenses(month: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$")):
    """Totals from receipt cards, per month and per category.

    Pass ?month=2026-11 for one month. Make receipts with
    POST /api/items/{id}/card on a photo or note of a bill.
    """
    return expense_summary(list_items_by_kind("receipt"), month=month)


# --- People & entities ------------------------------------------------------


@app.post("/api/items/{item_id}/entities")
def find_item_entities(item_id: int):
    """Find the people, places and organizations in one item and save them."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or item["title"] or ""
    entities = extract_entities(text, is_sensitive=item["is_sensitive"])
    return save_item_entities(item_id, entities)


@app.get("/api/entities")
def get_entities(
    type: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=3650),
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """All entities with how many items mention them (?type=person&start_date=2026-09-01&end_date=2026-09-30)."""
    if type is not None and type not in ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="Unknown entity type")
    return list_entities(type, days=days, start_date=start_date, end_date=end_date)


@app.get("/api/entities/{entity_id}")
def get_entity(
    entity_id: int,
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """ "Everything about X": the entity and every item that mentions it (optionally date-filtered)."""
    page = get_entity_page(entity_id, start_date=start_date, end_date=end_date)
    if page is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return page


@app.get("/api/entities/{entity_id}/summary")
def get_entity_summary(
    entity_id: int,
    start_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
    end_date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}"),
):
    """AI-synthesized profile dossier summary for an entity from its connected items."""
    page = get_entity_page(entity_id, start_date=start_date, end_date=end_date)
    if page is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    summary = summarize_entity(
        page["name"],
        page["type"],
        page.get("items", []),
        start_date=start_date,
        end_date=end_date,
    )
    return {
        "entity_id": entity_id,
        "name": page["name"],
        "type": page["type"],
        "start_date": start_date,
        "end_date": end_date,
        "item_count": len(page.get("items", [])),
        "summary": summary,
    }


# --- Resurfacing and flashcards ---------------------------------------------


@app.get("/api/resurface")
def get_resurfaced_items():
    """Old notes worth another look: "on this day" and a few forgotten ones."""
    today = attention_dates.today_in_configured_tz()
    return resurface(list_items_from_db(limit=None, offset=0), today)


@app.post("/api/items/{item_id}/flashcards")
def create_flashcards(item_id: int):
    """Turn an item into up to 3 flashcards, due for review today."""
    item = get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    text = item["extracted_text"] or item["raw_text"] or ""
    cards = make_cards(text, is_sensitive=item["is_sensitive"])
    if not cards:
        raise HTTPException(status_code=400, detail="Could not make cards from this item")
    today = attention_dates.today_in_configured_tz().isoformat()
    return add_flashcards(item_id, cards, due_date=today)


@app.get("/api/flashcards/due")
def get_due_flashcards():
    """Cards to review today (including overdue ones)."""
    today = attention_dates.today_in_configured_tz().isoformat()
    return list_due_flashcards(today)


@app.post("/api/flashcards/{card_id}/review")
def review_flashcard(card_id: int, body: FlashcardReview):
    """Record whether you remembered the answer; schedules the next review."""
    card = get_flashcard(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Flashcard not found")
    today = attention_dates.today_in_configured_tz()
    box, due = next_review(card["box"], body.remembered, today)
    return save_flashcard_review(card_id, box, due.isoformat())


# --- Public API: tokens and webhooks ----------------------------------------


def queue_webhooks(background_tasks: BackgroundTasks, event: str, data) -> None:
    """Send `event` to the user's webhooks after the response has gone out."""
    hooks = webhooks_for_event(event)
    if hooks:
        background_tasks.add_task(deliver_webhooks, hooks, event, data)


@app.post("/api/tokens")
def create_api_token(body: ApiTokenCreate):
    """Create an API token. The token itself is shown ONLY in this response."""
    token, token_hash = new_token()
    saved = add_api_token(body.name, token_hash)
    return {**saved, "token": token}


@app.get("/api/tokens")
def get_api_tokens():
    return list_api_tokens()


@app.delete("/api/tokens/{token_id}", status_code=204)
def revoke_api_token(token_id: int):
    if not delete_api_token(token_id):
        raise HTTPException(status_code=404, detail="Token not found")
    return Response(status_code=204)


@app.post("/api/webhooks")
def create_webhook(body: WebhookCreate):
    """Register a URL to receive events. The signing secret is shown only once."""
    if body.event not in WEBHOOK_EVENTS:
        raise HTTPException(status_code=400, detail=f"Event must be one of {WEBHOOK_EVENTS}")
    try:
        url = validate_public_http_url(body.url)  # blocks localhost / private IPs
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    secret = secrets.token_hex(16)
    return {**add_webhook(url, body.event, secret), "secret": secret}


@app.get("/api/webhooks")
def get_webhooks():
    return list_webhooks()


@app.delete("/api/webhooks/{webhook_id}", status_code=204)
def remove_webhook(webhook_id: int):
    if not delete_webhook(webhook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return Response(status_code=204)


# --- Privacy --------------------------------------------------------------


@app.post("/api/items/{item_id}/sensitive", response_model=InboxItem)
def mark_item_sensitive(item_id: int, body: SensitiveUpdate):
    """Mark an item sensitive (never sent to AI) or clear the flag."""
    item = set_item_sensitive(item_id, body.sensitive)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.get("/api/privacy/ai-log")
def get_ai_call_log(limit: int = Query(50, ge=1, le=200)):
    """Which AI calls were made for this user (no prompt text is stored)."""
    return list_ai_call_log(limit=limit)


@app.post("/api/privacy/redact-preview")
def redact_preview(body: RedactPreviewRequest):
    """Show what the AI would see for a piece of text, for demos and trust."""
    redacted, mapping = redact(body.text)
    return {"redacted_text": redacted, "hidden_values": len(mapping)}


@app.get("/api/items/{item_id}/insight", response_model=ItemInsight)
def read_item_insight(item_id: int):
    try:
        insight = get_item_insight(item_id)
    except Exception as exc:
        logger.exception("Failed to load item insight")
        raise HTTPException(
            status_code=500,
            detail="Unable to load insight. Please try again.",
        ) from exc
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return insight


@app.patch("/api/items/{item_id}/insight", response_model=ItemInsight)
def patch_item_insight(item_id: int, payload: InsightOverrideUpdate):
    try:
        insight = update_item_insight_overrides(
            item_id,
            user_title=payload.user_title,
            user_topic_id=payload.user_topic_id,
            user_priority=payload.user_priority,
            user_tags=payload.user_tags,
            clear_overrides=payload.clear_overrides,
            needs_review=payload.needs_review,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update item insight")
        raise HTTPException(
            status_code=500,
            detail="Unable to update insight. Please try again.",
        ) from exc
    if insight is None:
        raise HTTPException(status_code=404, detail="Insight not found.")
    return insight


@app.get("/api/relations", response_model=List[ItemRelation])
def get_relations(include_dismissed: bool = Query(False)):
    try:
        return list_relations_from_db(include_dismissed=include_dismissed)
    except Exception as exc:
        logger.exception("Failed to load relations")
        raise HTTPException(
            status_code=500,
            detail="Unable to load related items. Please try again.",
        ) from exc


@app.post("/api/relations/{relation_id}/dismiss", response_model=ItemRelation)
def dismiss_relation(relation_id: int):
    try:
        relation = dismiss_relation_in_db(relation_id)
    except Exception as exc:
        logger.exception("Failed to dismiss relation")
        raise HTTPException(
            status_code=500,
            detail="Unable to dismiss relation. Please try again.",
        ) from exc
    if relation is None:
        raise HTTPException(status_code=404, detail="Relation not found.")
    return relation


@app.get("/api/search", response_model=list[SearchHit])
def search_inbox(
    q: str = Query(..., min_length=1, max_length=200),
    item_type: Optional[str] = Query(None, max_length=100),
    limit: int = Query(8, ge=1, le=25),
):
    try:
        ranked = search_passages(
            q,
            limit=limit,
            item_type=item_type,
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to search inbox. Please try again.",
        ) from exc
    return [
        SearchHit(
            item_id=row.passage.item_id,
            item_type=row.passage.item_type,
            title=row.passage.title,
            excerpt=row.passage.passage[:320],
            source_kind=row.passage.source_kind,
            preview_url=row.passage.preview_url,
            score=round(row.score, 4),
            keyword_score=round(row.keyword_score, 4),
            semantic_score=round(row.semantic_score, 4),
            meaning_score=round(row.meaning_score, 4),
        )
        for row in ranked
    ]


@app.post("/api/ask", response_model=AskResponse)
def ask_my_inbox(payload: AskRequest):
    try:
        return build_grounded_answer(
            payload.question.strip(),
            item_type=payload.item_type,
        )
    except Exception as exc:
        logger.exception("Ask My Inbox failed")
        raise HTTPException(
            status_code=500,
            detail="Unable to answer from your inbox. Please try again.",
        ) from exc


@app.get("/api/dashboard")
def organization_dashboard():
    try:
        return get_organization_dashboard()
    except Exception as exc:
        logger.exception("Failed to load organization dashboard")
        raise HTTPException(
            status_code=500,
            detail="Unable to load dashboard. Please try again.",
        ) from exc


@app.get(
    "/api/analyze/latest",
    response_model=PersistedAnalysis,
)
def get_latest_analysis():
    try:
        result = get_latest_successful_analysis()
    except Exception as exc:
        logger.exception("Failed to load latest analysis")
        raise HTTPException(
            status_code=500,
            detail="Unable to load the latest analysis. Please try again.",
        ) from exc

    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


@app.get("/api/analyze/pending")
def get_pending_analysis():
    """How many notes are new or edited since their last analysis."""
    try:
        pending = list_pending_notes_from_db()
    except Exception as exc:
        logger.exception("Failed to count notes pending analysis")
        raise HTTPException(
            status_code=500,
            detail="Unable to check for new notes. Please try again.",
        ) from exc
    return {
        "pending_count": len(pending),
        "pending_item_ids": [note["id"] for note in pending],
        "analyzing": auto_analyzer.busy or _analysis_lock.locked(),
    }


@app.post("/api/analyze", response_model=PersistedAnalysis)
def analyze_inbox(scope: str = Query("new", pattern="^(new|all)$")):
    """Analyze notes that are new (or edited) since their last analysis.

    New notes are also analyzed automatically when they are saved; this
    endpoint is the manual trigger. ``scope=all`` re-analyzes the whole inbox
    and replaces the saved view.
    """
    try:
        return _run_analysis(scope)
    except AnalysisAlreadyRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail="An analysis is already running. Please wait for it to finish.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to analyze inbox")
        raise HTTPException(
            status_code=500,
            detail="Unable to analyze inbox. Please try again.",
        ) from exc


_STATIC_REF = re.compile(r'(["\'])(/static/[^"\'?]+\.(?:js|css))\1')


def _versioned_static_refs(html: str) -> str:
    """Append ?v=<file mtime> to script/stylesheet URLs.

    The URL changes whenever a file does, so a browser can never keep running
    an old script against a newer page, even on a normal reload.
    """

    def add_version(match):
        quote, url = match.group(1), match.group(2)
        file_path = APP_DIR / url.removeprefix("/")
        try:
            version = file_path.stat().st_mtime_ns
        except OSError:
            return match.group(0)
        return f"{quote}{url}?v={version}{quote}"

    return _STATIC_REF.sub(add_version, html)


@app.get("/", response_class=HTMLResponse)
def index():
    html = (APP_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return _versioned_static_refs(html)


@app.get("/profile", response_class=HTMLResponse)
def profile_page():
    html = (APP_DIR / "templates" / "profile.html").read_text(encoding="utf-8")
    return _versioned_static_refs(html)


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    html = (APP_DIR / "templates" / "settings.html").read_text(encoding="utf-8")
    return _versioned_static_refs(html)
