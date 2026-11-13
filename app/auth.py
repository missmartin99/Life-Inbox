"""Server-side session auth. Never trusts browser-supplied user IDs.

The browser holds only a random, unguessable session token in an HttpOnly
cookie. The server stores a hash of it, so sessions can be revoked (logout,
"sign out everywhere") and expire on their own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response

from app.config import AppConfig, load_config
from app.database import (
    SESSION_TTL,
    create_password_account,
    get_password_account,
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


# --- Email + password accounts ------------------------------------------------

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 200
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}

# A slow, salted hash (scrypt) so a stolen database does not reveal passwords.
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: Optional[str]) -> bool:
    try:
        scheme, salt_b64, digest_b64 = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64), **_SCRYPT
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A dummy hash, so a wrong email takes as long to check as a wrong password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))

# Brute-force guard: a few failed attempts per email, then a short lockout.
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
_failed: dict[str, list[float]] = {}
_failed_lock = threading.Lock()


def _recent_failures(email: str) -> list[float]:
    cutoff = time.monotonic() - LOCKOUT_SECONDS
    attempts = [t for t in _failed.get(email, []) if t > cutoff]
    _failed[email] = attempts
    return attempts


def _require_password_login() -> None:
    if not load_config(strict=False).password_login_enabled:
        raise HTTPException(status_code=403, detail="Password sign-in is disabled.")


def _clean_email(email: str) -> str:
    cleaned = (email or "").strip().lower()
    local, _, domain = cleaned.partition("@")
    if not local or "." not in domain or len(cleaned) > 200 or " " in cleaned:
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return cleaned


def _check_new_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
        )
    if len(password) > PASSWORD_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Password is too long.")
    return password


def register_password_user(email: str, password: str, display_name: Optional[str]) -> dict:
    _require_password_login()
    cleaned = _clean_email(email)
    _check_new_password(password)
    name = (display_name or "").strip()[:80]
    try:
        return create_password_account(cleaned, name, hash_password(password))
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists. Sign in instead.",
        )


def authenticate_password_user(email: str, password: str) -> dict:
    """The user for this email and password, or a 401 (same message either way)."""
    _require_password_login()
    cleaned = (email or "").strip().lower()
    with _failed_lock:
        if len(_recent_failures(cleaned)) >= MAX_FAILED_ATTEMPTS:
            raise HTTPException(
                status_code=429,
                detail="Too many failed attempts. Try again in a few minutes.",
            )
    account = get_password_account(cleaned) if cleaned else None
    stored = account["password_hash"] if account else None
    ok = verify_password(password or "", stored or _DUMMY_HASH) and bool(stored)
    if not ok:
        with _failed_lock:
            _failed.setdefault(cleaned, []).append(time.monotonic())
        detail = "Wrong email or password."
        if account and not stored and account["auth_provider"] == "dev":
            detail = "This account has no password yet. Choose Create account to set one."
        raise HTTPException(status_code=401, detail=detail)
    with _failed_lock:
        _failed.pop(cleaned, None)
    account.pop("password_hash", None)
    return account
