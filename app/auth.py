"""Session-based auth. Never trusts browser-supplied user IDs."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from typing import Optional

from fastapi import Cookie, HTTPException, Request, Response

from app.config import load_config
from app.database import ensure_user, get_user_by_email, get_user_by_id


SESSION_COOKIE = "life_inbox_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14


def _signer_key() -> bytes:
    config = load_config(strict=False)
    return hashlib.sha256(config.session_secret.encode("utf-8")).digest()


def create_session_token(user_id: int) -> str:
    payload = {
        "uid": int(user_id),
        "iat": int(time.time()),
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    sig = hmac.new(_signer_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_session_token(token: str) -> Optional[int]:
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        _signer_key(), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return int(payload["uid"])


def set_session_cookie(response: Response, user_id: int) -> None:
    secure = os.getenv("LIFE_INBOX_ENV", "local") in {"beta", "production"}
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def get_current_user(
    request: Request,
    life_inbox_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    # Never accept X-User-Id / query user_id as identity.
    if "x-user-id" in {k.lower() for k in request.headers.keys()}:
        # Ignore spoof header completely.
        pass

    token = life_inbox_session
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = parse_session_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    user = get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown session user.")
    return user


def optional_current_user(
    request: Request,
    life_inbox_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
) -> Optional[dict]:
    try:
        return get_current_user(request, life_inbox_session)
    except HTTPException:
        return None


def dev_login_user(email: str, display_name: Optional[str] = None) -> dict:
    config = load_config(strict=False)
    if not config.allow_dev_login:
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
