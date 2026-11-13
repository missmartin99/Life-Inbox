"""Authentication routes (sessions, Google OAuth, dev-login)."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import load_config
from app.routers._helpers import APP_DIR, schedule_calendar_sync, versioned_static_refs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

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

LOGIN_ERRORS = {
    "denied": "Sign-in was cancelled.",
    "state": "That sign-in link expired. Please try again.",
    "email_unverified": "Your Google email address is not verified.",
    "not_allowed": "This Google account is not on the invite list.",
    "failed": "Google sign-in failed. Please try again.",
}


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


def _finish_calendar_connect(config, saved: dict, code, error) -> RedirectResponse:
    """Second half of "Connect Google Calendar"; always lands back on Settings."""
    from app.database import get_current_owner_id
    from app.google_oauth import OAUTH_COOKIE
    import app.integrations.google_calendar as google_calendar

    def done(result: str) -> RedirectResponse:
        response = RedirectResponse(f"/settings?calendar={result}", status_code=302)
        response.delete_cookie(OAUTH_COOKIE, path="/api/auth/google")
        return response

    if error:
        return done("denied")
    owner_id = get_current_owner_id()
    if owner_id is None or owner_id != saved["user_id"]:
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
    schedule_calendar_sync(owner_id)
    return done("connected")


@router.get("/api/auth/options")
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


@router.get("/api/auth/me")
def auth_me():
    from app.database import get_current_owner_id, get_user_by_id

    owner_id = get_current_owner_id()
    user = get_user_by_id(owner_id) if owner_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


@router.post("/api/auth/dev-login")
def auth_dev_login(payload: dict, request: Request, response: Response):
    from app.auth import dev_login_user, start_session

    email = str(payload.get("email", "")).strip()
    display_name = payload.get("display_name")
    user = dev_login_user(email, display_name=display_name)
    start_session(response, request, user["id"])
    return user


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    from app.auth import end_session

    end_session(response, request)
    return {"status": "logged_out"}


@router.post("/api/auth/logout-all")
def auth_logout_all(request: Request, response: Response):
    """Sign out of every device (all of this user's sessions)."""
    from app.auth import clear_session_cookie
    from app.database import require_owner_id, revoke_all_auth_sessions

    revoked = revoke_all_auth_sessions(require_owner_id())
    clear_session_cookie(response)
    return {"status": "logged_out_everywhere", "sessions_revoked": revoked}


@router.get("/api/auth/google/start")
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


@router.get("/api/auth/google/calendar/start")
def calendar_connect_start():
    """Ask Google for permission to keep a "Life Inbox" calendar (signed in only)."""
    from app.database import get_user_by_id, require_owner_id
    import app.integrations.google_calendar as google_calendar

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


@router.get("/api/auth/google/callback")
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


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: Optional[str] = None):
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
    return versioned_static_refs(
        page.replace("__ERROR__", html.escape(message)).replace("__BUTTON__", button)
    )
