"""Canonical capture lifecycle helpers."""

from __future__ import annotations

STATUS_CAPTURED = "captured"
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"

URL_ITEM_TYPE = "url"
PDF_ITEM_TYPE = "pdf"
IMAGE_ITEM_TYPE = "image"
VOICE_ITEM_TYPE = "voice"
TEXT_ITEM_TYPE = "text"

ANALYZABLE_STATUSES = {STATUS_READY}
ANALYZABLE_TYPES = {
    TEXT_ITEM_TYPE,
    URL_ITEM_TYPE,
    PDF_ITEM_TYPE,
    IMAGE_ITEM_TYPE,
    VOICE_ITEM_TYPE,
}
