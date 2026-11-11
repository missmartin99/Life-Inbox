"""Server-side session auth. Never trusts browser-supplied user IDs.

The browser holds only a random, unguessable session token in an HttpOnly
cookie. The server stores a hash of it, so sessions can be revoked (logout,
"sign out everywhere") and expire on their own.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response

from app.config import AppConfig, load_config
from app.database import (
    SESSION_TTL,
    create_auth_session,
    ensure_user,
    get_user_by_email,
    get_user_for_session_token,
    revoke_auth_session,
)


SESSION_COOKIE = "life_inbox_session"
SESSION_TTL_SECONDS = int(SESSION_TTL.total_seconds())


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=load_config(strict=False).cookie_secure,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=load_config(strict=False).cookie_secure,
    )


def start_session(response: Response, request: Request, user_id: int) -> None:
    """Begin a fresh session for the user (always a new token on login).

    Any session the browser already had is revoked, which prevents session
    fixation and stops an old login from lingering after switching accounts.
    """
    revoke_auth_session(request.cookies.get(SESSION_COOKIE))
    raw_token = create_auth_session(user_id, request.headers.get("user-agent"))
    set_session_cookie(response, raw_token)


def end_session(response: Response, request: Request) -> None:
    revoke_auth_session(request.cookies.get(SESSION_COOKIE))
    clear_session_cookie(response)


def current_user_from_request(request: Request) -> Optional[dict]:
    # Identity comes only from the session cookie. X-User-Id headers, query
    # parameters and request bodies are never consulted.
    return get_user_for_session_token(request.cookies.get(SESSION_COOKIE))


def get_current_user(request: Request) -> dict:
    user = current_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def optional_current_user(request: Request) -> Optional[dict]:
    return current_user_from_request(request)


def origin_allowed(request: Request, config: AppConfig) -> bool:
    """Block cross-site state-changing requests (CSRF defence in depth).

    SameSite=Lax cookies already stop most cross-site posts; this also rejects
    requests whose Origin is not this app.
    """
    if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    origin = request.headers.get("origin")
    if not origin:
        return True  # non-browser clients (scripts, tests) send no Origin
    origin_host = urlparse(origin).netloc.lower()
    allowed = {
        request.headers.get("host", "").lower(),
        urlparse(config.public_url).netloc.lower(),
    }
    allowed.discard("")
    return origin_host in allowed


def dev_login_user(email: str, display_name: Optional[str] = None) -> dict:
    config = load_config(strict=False)
    if not config.allow_dev_login or config.environment != "local":
        raise HTTPException(status_code=403, detail="Dev login is disabled.")
    cleaned = email.strip().lower()
    if "@" not in cleaned or len(cleaned) < 5:
        raise HTTPException(status_code=400, detail="Valid email required.")
    existing = get_user_by_email(cleaned)
    if existing:
        return existing
    return ensure_user(
        email=cleaned,
        display_name=display_name or cleaned.split("@", 1)[0],
        auth_provider="dev",
    )
