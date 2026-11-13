"""Google sign-in (OpenID Connect authorization-code flow with PKCE)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional
from urllib.parse import urlencode, urlparse

import requests

from app.config import AppConfig

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

OAUTH_COOKIE = "life_inbox_oauth"
OAUTH_COOKIE_TTL_SECONDS = 600
REQUEST_TIMEOUT_SECONDS = 10


class GoogleAuthError(Exception):
    """Sign-in failed. `code` is a short, safe key shown on the login page."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def redirect_uri(config: AppConfig) -> str:
    return f"{config.public_url}/api/auth/google/callback"


def safe_next_path(value: Optional[str]) -> str:
    """Only allow same-site relative paths (no open redirects)."""
    if not value or not value.startswith("/"):
        return "/"
    if value.startswith("//") or "\\" in value or "\n" in value or "\r" in value:
        return "/"
    return value


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(config: AppConfig, payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    key = hashlib.sha256(config.session_secret.encode("utf-8")).digest()
    signature = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def _unsign(config: AppConfig, token: str) -> Optional[dict]:
    try:
        body, signature = token.rsplit(".", 1)
        key = hashlib.sha256(config.session_secret.encode("utf-8")).digest()
        expected = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def build_authorization(
    config: AppConfig, next_path: Optional[str]
) -> tuple[str, str]:
    """Sign-in. Return (google_redirect_url, signed_cookie_value)."""
    return start_authorization(
        config,
        scope="openid email profile",
        params={"prompt": "select_account"},
        state={"n": safe_next_path(next_path)},
    )


def start_authorization(
    config: AppConfig, *, scope: str, params: dict, state: dict
) -> tuple[str, str]:
    """Build a Google authorization URL (with PKCE) and its state cookie.

    ``state`` is extra data kept in the signed cookie and handed back by
    ``read_state_cookie`` when Google redirects to the callback.
    """
    state_token = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)
    challenge = _b64(hashlib.sha256(verifier.encode("ascii")).digest())
    query = {
        "client_id": config.google_client_id,
        "redirect_uri": redirect_uri(config),
        "response_type": "code",
        "scope": scope,
        "state": state_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **params,
    }
    cookie_value = _sign(
        config,
        {
            **state,
            "s": state_token,
            "v": verifier,
            "exp": int(time.time()) + OAUTH_COOKIE_TTL_SECONDS,
        },
    )
    return f"{AUTH_URL}?{urlencode(query)}", cookie_value


def read_state_cookie(
    config: AppConfig, cookie_value: Optional[str], state_param: Optional[str]
) -> dict:
    """Validate the CSRF `state`.

    Returns {"verifier", "next", "purpose", "user_id"}. ``purpose`` is
    "signin", or "calendar" when a signed-in person is connecting Google
    Calendar (``user_id`` is then who started it).
    """
    if not cookie_value or not state_param:
        raise GoogleAuthError("state")
    payload = _unsign(config, cookie_value)
    if payload is None or not hmac.compare_digest(
        str(payload.get("s", "")), state_param
    ):
        raise GoogleAuthError("state")
    user_id = payload.get("u")
    return {
        "verifier": str(payload.get("v", "")),
        "next": safe_next_path(payload.get("n")),
        "purpose": "calendar" if payload.get("p") == "calendar" else "signin",
        "user_id": user_id if isinstance(user_id, int) else None,
    }


def fetch_google_identity(config: AppConfig, code: str, verifier: str) -> dict:
    """Exchange the code, then read the user's profile from Google."""
    try:
        token_response = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "redirect_uri": redirect_uri(config),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if token_response.status_code != 200:
            raise GoogleAuthError("failed")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise GoogleAuthError("failed")
        info_response = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if info_response.status_code != 200:
            raise GoogleAuthError("failed")
        info = info_response.json()
    except requests.RequestException as exc:
        raise GoogleAuthError("failed") from exc
    except ValueError as exc:
        raise GoogleAuthError("failed") from exc

    sub = str(info.get("sub") or "")
    email = str(info.get("email") or "").strip().lower()
    verified = info.get("email_verified")
    if isinstance(verified, str):
        verified = verified.strip().lower() == "true"
    if not sub or not email:
        raise GoogleAuthError("failed")
    if verified is not True:
        raise GoogleAuthError("email_unverified")
    return {
        "sub": sub,
        "email": email,
        "name": info.get("name"),
        "picture": clean_picture_url(info.get("picture")),
    }


def clean_picture_url(value) -> Optional[str]:
    """Keep Google's profile picture URL only if it really is one.

    The URL ends up in an <img src>, so anything that is not an https link on
    Google's image host is dropped.
    """
    if not isinstance(value, str) or len(value) > 2048:
        return None
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "googleusercontent.com" or host.endswith(".googleusercontent.com")
    ):
        return None
    return value.strip()


def email_allowed(config: AppConfig, email: str) -> bool:
    """Empty allowlists mean any verified Google account may sign in."""
    if not config.allowed_emails and not config.allowed_email_domains:
        return True
    cleaned = email.strip().lower()
    if cleaned in config.allowed_emails:
        return True
    domain = cleaned.rsplit("@", 1)[-1]
    return domain in config.allowed_email_domains
