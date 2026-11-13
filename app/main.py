"""FastAPI application entry point, middleware, and router mounts."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

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
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api_access import deliver_webhooks
import app.capture.pipeline as pipeline
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
from app.database import (
    AuthRequiredError,
    add_image_item_to_db,
    add_item_to_db,
    add_note_to_db,
    add_pdf_item_to_db,
    add_url_item_to_db,
    add_voice_item_to_db,
    complete_image_item_processing,
    complete_pdf_item_processing,
    complete_url_item_processing,
    complete_voice_item_processing,
    delete_item_from_db,
    delete_note_from_db,
    fail_item_processing,
    get_current_owner_id,
    get_item_from_db,
    init_db,
    list_items_from_db,
    list_notes_from_db,
    mark_item_processing,
    set_current_owner_id,
    update_item_extracted_text,
    update_item_in_db,
    update_note_in_db,
    webhooks_for_event,
)
import app.integrations.google_calendar as google_calendar
from app.organizer import analyze_inbox_with_openai, validate_analysis_result
from app.auto_analysis import AutoAnalyzer, auto_analysis_enabled
# Clear any cached router modules so they re-bind cleanly to the current database/app
for mod in list(sys.modules):
    if mod.startswith("app.routers.") or mod == "app.routers":
        sys.modules.pop(mod, None)

# Import modular APIRouters
from app.assistant_routes import router as assistant_router
from app.routers.auth import (
    GOOGLE_SIGN_IN_BUTTON,
    LOGIN_ERRORS,
    auth_dev_login,
    auth_google_callback,
    auth_google_start,
    auth_logout,
    auth_logout_all,
    auth_me,
    auth_options,
    calendar_connect_start,
    login_page,
    router as auth_router,
)
from app.routers.calendar import (
    calendar_confirm,
    calendar_preview,
    calendar_status,
    google_calendar_disconnect,
    google_calendar_status,
    google_calendar_sync_now,
    router as calendar_router,
)
from app.routers.intelligence import (
    create_card_directly,
    create_flashcards,
    create_item_card,
    create_topic,
    find_item_entities,
    get_due_flashcards,
    get_entities,
    get_entity,
    get_entity_summary,
    get_expenses,
    get_resurfaced_items,
    get_topics,
    list_card_kinds,
    list_cards,
    patch_topic,
    review_flashcard,
    router as intelligence_router,
    update_item_card,
)
from app.routers.items import (
    create_image_item,
    create_item,
    create_note,
    create_pdf_item,
    create_url_item,
    create_voice_item,
    delete_item,
    delete_note,
    get_item_source_file,
    get_items,
    get_notes,
    patch_extracted_text,
    read_item,
    retry_item_processing,
    router as items_router,
    update_item,
    update_note,
)
from app.routers.me import (
    GOOGLE_PERMISSIONS_URL,
    connected_apps,
    get_avatar,
    remove_avatar,
    router as me_router,
    save_settings,
    update_profile,
    upload_avatar,
)
from app.routers.system import (
    _analysis_lock,
    _run_analysis,
    analyze_inbox,
    ask_my_inbox,
    create_api_token,
    create_webhook,
    dismiss_relation,
    get_ai_call_log,
    get_api_tokens,
    get_latest_analysis,
    get_pending_analysis,
    get_relations,
    get_webhooks,
    mark_item_sensitive,
    organization_dashboard,
    patch_item_insight,
    read_item_insight,
    redact_preview,
    remove_webhook,
    revoke_api_token,
    router as system_router,
    search_inbox,
)
from app.routers.tasks import (
    attention_board,
    complete_task,
    confirm_timing,
    daily_briefing,
    dismiss_task,
    get_tasks,
    notifications,
    reopen_task,
    router as tasks_router,
    set_task_reminder,
    snooze_task,
)

# Pipeline aliases and interfaces
_process_url_item = pipeline.process_url_item
_process_pdf_item = pipeline.process_pdf_item
_process_image_item = pipeline.process_image_item
_process_voice_item = pipeline.process_voice_item
process_item = pipeline.process_item

APP_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

# Default strictness: beta/production refuse to start when misconfigured
_startup_config = load_config()
_docs_kwargs = (
    {}
    if _startup_config.environment == "local"
    else {"docs_url": None, "redoc_url": None, "openapi_url": None}
)


class RevalidatingStaticFiles(StaticFiles):
    """Static files the browser must re-check on every load."""

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
SIGNED_IN_PAGES = {"/", "/profile", "/settings"}


def _owner_for_api_token(request: Request) -> Optional[int]:
    """The owner of the API token in the Authorization header, or None."""
    from app.api_access import hash_token, token_from_header
    from app.database import owner_for_token_hash

    api_token = token_from_header(request.headers.get("authorization"))
    if not api_token:
        return None
    return owner_for_token_hash(hash_token(api_token))


def queue_webhooks(background_tasks: BackgroundTasks, event: str, data) -> None:
    """Send `event` to the user's webhooks after the response has gone out."""
    hooks = webhooks_for_event(event)
    if hooks:
        background_tasks.add_task(deliver_webhooks, hooks, event, data)


@app.middleware("http")
async def bind_owner_from_session(request, call_next):
    """Identify the user from the session cookie and gate the API."""
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
        user = await run_in_threadpool(current_user_from_request, request)
        if user is not None:
            owner_id = user["id"]
        elif is_api and request.headers.get("authorization"):
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


_STATIC_REF = re.compile(r'(["\'])(/static/[^"\'?]+\.(?:js|css))\1')


def _versioned_static_refs(html: str) -> str:
    """Append ?v=<file mtime> to script/stylesheet URLs."""

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


# Mount modular APIRouters
app.include_router(auth_router)
app.include_router(me_router)
app.include_router(items_router)
app.include_router(tasks_router)
app.include_router(calendar_router)
app.include_router(assistant_router)
app.include_router(intelligence_router)
app.include_router(system_router)
