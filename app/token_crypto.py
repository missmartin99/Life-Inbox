"""Encrypts OAuth tokens before they are stored in the database.

A Google refresh token works for months, so it is never written in plain
text: anyone who gets a copy of the database (a backup, a leaked dump) still
cannot use it. The key is derived from LIFE_INBOX_SESSION_SECRET, which lives
only in the server's environment.

If that secret changes, old tokens can no longer be read. Life Inbox then asks
the person to reconnect instead of failing.
"""

from __future__ import annotations

import base64
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_PURPOSE = b"life-inbox oauth tokens v1"


def _fernet(secret: str) -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_PURPOSE
    ).derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(secret: str, value: str) -> str:
    return _fernet(secret).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_token(secret: str, value: Optional[str]) -> Optional[str]:
    """The original token, or None if it is missing or the key has changed."""
    if not value:
        return None
    try:
        return _fernet(secret).decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
