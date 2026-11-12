"""Letting other programs use Life Inbox: API tokens and webhooks.

API tokens -- "a password for a program"
    A script, a phone shortcut or the MCP server sends
        Authorization: Bearer li_xxxxxxxx
    with each request. We only store a SHA-256 HASH of the token, like a
    password: if the database leaks, the tokens can't be used. That is also
    why the full token is shown only once, when it is created.

Webhooks -- "call me when something happens"
    Instead of other apps asking "anything new?" every minute (polling), we
    send them an HTTP POST when an item is created. Each webhook has a secret;
    we sign the body with it (HMAC-SHA256) so the receiver can check the
    message really came from us:
        X-LifeInbox-Signature: sha256=<hex>
"""

import hashlib
import hmac
import json
import logging
import secrets

import requests

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "li_"
WEBHOOK_EVENTS = ["item.created"]
WEBHOOK_TIMEOUT_SECONDS = 5


# --- API tokens --------------------------------------------------------------------


def new_token():
    """Return (token_to_show_once, hash_to_store)."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(24)
    return token, hash_token(token)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_from_header(authorization):
    """'Bearer li_abc' -> 'li_abc'; anything else -> None."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX):
        return None
    return token


# --- Webhooks -----------------------------------------------------------------------


def sign(secret, body):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver_webhooks(webhooks, event, data):
    """POST the event to each webhook. Runs after the response is sent.

    A slow or broken receiver must never break Life Inbox, so errors are
    only logged.
    """
    body = json.dumps({"event": event, "data": data}, default=str).encode("utf-8")
    for hook in webhooks:
        try:
            requests.post(
                hook["url"],
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-LifeInbox-Event": event,
                    "X-LifeInbox-Signature": sign(hook["secret"], body),
                },
                timeout=WEBHOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("Webhook %s failed", hook["url"], exc_info=True)
