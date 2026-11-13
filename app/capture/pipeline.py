"""Canonical capture lifecycle helpers and deep ingestion pipeline."""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict, Optional

from fastapi import HTTPException

# 1. Canonical lifecycle and type constants (must remain at top for zero circular dependencies)
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

logger = logging.getLogger(__name__)


def _db():
    """Dynamically return the active app.database module."""
    import app.database as db
    return db


def _resolve(name: str, default: Any) -> Any:
    """Dynamically resolve symbols from app.main if monkeypatched in tests."""
    main = sys.modules.get("app.main")
    if main is not None and hasattr(main, name):
        return getattr(main, name)
    return default


def process_url_item(item_id: int) -> dict:
    """Fetch URL metadata and extract text content into the database."""
    from app.capture.url_fetch import fetch_url as _default_fetch_url

    db = _db()
    db.mark_item_processing(item_id)
    item = db.get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    try:
        fetch_fn = _resolve("fetch_url", _default_fetch_url)
        fetched = fetch_fn(item["source_url"])
        processed = db.complete_url_item_processing(
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
        failed = db.fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("URL processing failed")
        failed = db.fail_item_processing(
            item_id,
            "Unable to fetch or extract this URL.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def process_pdf_item(item_id: int) -> dict:
    """Extract text and metadata from stored PDF documents."""
    from app.capture.pdf_extract import extract_pdf_text as _default_extract_pdf_text
    from app.capture.storage import read_stored_bytes

    db = _db()
    db.mark_item_processing(item_id)
    item = db.get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = db.fail_item_processing(
            item_id,
            "Stored PDF file is missing.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        extract_fn = _resolve("extract_pdf_text", _default_extract_pdf_text)
        extracted = extract_fn(
            data,
            original_filename=item.get("original_filename"),
        )
        processed = db.complete_pdf_item_processing(
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
        failed = db.fail_item_processing(
            item_id,
            "Stored PDF file is missing.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = db.fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("PDF processing failed")
        failed = db.fail_item_processing(
            item_id,
            "Unable to extract text from this PDF.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def process_image_item(item_id: int) -> dict:
    """Extract OCR text, generate thumbnail, and AI description for stored images."""
    from app.capture.image_extract import process_image_bytes as _default_process_image_bytes
    from app.capture.storage import read_stored_bytes, store_thumbnail_bytes

    db = _db()
    db.mark_item_processing(item_id)
    item = db.get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = db.fail_item_processing(item_id, "Stored image file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        process_fn = _resolve("process_image_bytes", _default_process_image_bytes)
        processed_payload = process_fn(
            data,
            original_filename=item.get("original_filename") or "image.png",
            mime_type=item.get("mime_type") or "image/png",
        )
        store_thumbnail_bytes(storage_path, processed_payload["thumbnail_bytes"])
        processed = db.complete_image_item_processing(
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
        failed = db.fail_item_processing(item_id, "Stored image file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = db.fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("Image processing failed")
        failed = db.fail_item_processing(
            item_id,
            "Unable to process this image.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def process_voice_item(item_id: int) -> dict:
    """Transcribe and process stored audio recordings."""
    from app.capture.storage import read_stored_bytes
    from app.capture.voice_extract import process_voice_bytes as _default_process_voice_bytes

    db = _db()
    db.mark_item_processing(item_id)
    item = db.get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")
    storage_path = item.get("storage_path")
    if not storage_path:
        failed = db.fail_item_processing(item_id, "Stored audio file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    try:
        data = read_stored_bytes(storage_path)
        process_fn = _resolve("process_voice_bytes", _default_process_voice_bytes)
        processed_payload = process_fn(
            data,
            original_filename=item.get("original_filename") or "voice-note.webm",
            mime_type=item.get("mime_type") or "audio/webm",
        )
        processed = db.complete_voice_item_processing(
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
        failed = db.fail_item_processing(item_id, "Stored audio file is missing.")
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.")
        return failed
    except ValueError as exc:
        failed = db.fail_item_processing(item_id, str(exc))
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed
    except Exception as exc:
        logger.exception("Voice processing failed")
        failed = db.fail_item_processing(
            item_id,
            "Unable to process this voice note.",
        )
        if failed is None:
            raise HTTPException(status_code=404, detail="Item not found.") from exc
        return failed


def process_item(item_id: int) -> dict:
    """Cohesive ingestion pipeline interface for processing any capture item."""
    db = _db()
    item = db.get_item_from_db(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found.")

    item_type = item.get("item_type")
    if item_type == URL_ITEM_TYPE:
        return process_url_item(item_id)
    elif item_type == PDF_ITEM_TYPE:
        return process_pdf_item(item_id)
    elif item_type == IMAGE_ITEM_TYPE:
        return process_image_item(item_id)
    elif item_type == VOICE_ITEM_TYPE:
        return process_voice_item(item_id)
    elif item_type == TEXT_ITEM_TYPE:
        return item
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported item type for processing: {item_type}",
        )


# Backward compatibility aliases
_process_url_item = process_url_item
_process_pdf_item = process_pdf_item
_process_image_item = process_image_item
_process_voice_item = process_voice_item
