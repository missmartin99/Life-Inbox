"""Image / screenshot validation, OCR, and description adapters."""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional

from app.capture.storage import MAX_IMAGE_BYTES

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
ALLOWED_IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
}

# Magic / signature sniffing for common image types.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),  # refined below
]


def _normalize_filename(filename: str) -> str:
    name = Path(filename or "image.png").name
    if not name or name in {".", ".."}:
        return "image.png"
    return name


def _sniff_image(data: bytes) -> tuple[str, str]:
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    for magic, mime, ext in _SIGNATURES:
        if magic != b"RIFF" and data.startswith(magic):
            return mime, ext
    raise ValueError("File does not look like a supported image (PNG/JPEG/WebP/GIF).")


def validate_image_upload(
    filename: str,
    content_type: Optional[str],
    data: bytes,
) -> tuple[str, str, str]:
    """Validate upload; return (safe_filename, detected_mime, storage_extension)."""
    safe_name = _normalize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix and suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError(
            "Only PNG, JPEG, WebP, or GIF images are allowed."
        )

    if not data:
        raise ValueError("Uploaded file is empty.")

    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB upload limit."
        )

    detected_mime, detected_ext = _sniff_image(data)

    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime and mime not in ALLOWED_IMAGE_MIMES:
            raise ValueError(
                "Unsupported content type. Upload a PNG, JPEG, WebP, or GIF image."
            )

    if suffix and suffix != detected_ext:
        # Prefer sniffed type; keep original filename for display.
        pass

    return safe_name, detected_mime, detected_ext


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    return stem or "Image capture"


def make_thumbnail_bytes(data: bytes, *, max_size: int = 320) -> bytes:
    """Create a JPEG thumbnail; raises ValueError if the image cannot be opened."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError(
            "Image processing is unavailable (Pillow not installed)."
        ) from exc

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((max_size, max_size))
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=85)
            return out.getvalue()
    except Exception as exc:
        raise ValueError("Unable to read this image for preview.") from exc


def run_ocr(data: bytes) -> dict:
    """OCR adapter. Graceful when Tesseract/pytesseract is unconfigured.

    Returns:
        {"text": str, "provider": str, "available": bool, "note": str | None}
    """
    if os.getenv("LIFE_INBOX_DISABLE_OCR", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return {
            "text": "",
            "provider": "disabled",
            "available": False,
            "note": "OCR disabled via LIFE_INBOX_DISABLE_OCR.",
        }

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return {
            "text": "",
            "provider": "none",
            "available": False,
            "note": "OCR not configured (install Pillow + pytesseract + Tesseract).",
        }

    try:
        with Image.open(io.BytesIO(data)) as image:
            text = pytesseract.image_to_string(image) or ""
        cleaned = "\n".join(
            line.rstrip() for line in text.splitlines()
        ).strip()
        return {
            "text": cleaned,
            "provider": "tesseract",
            "available": True,
            "note": None if cleaned else "OCR ran but found no text.",
        }
    except Exception as exc:
        logger.info("OCR failed gracefully: %s", exc)
        return {
            "text": "",
            "provider": "tesseract",
            "available": False,
            "note": f"OCR unavailable: {exc}",
        }


def describe_image(
    *,
    original_filename: str,
    mime_type: str,
    ocr_text: str,
    ocr_note: Optional[str],
) -> dict:
    """Factual-ish description adapter. Distinguishes inferred content.

    Live vision APIs are optional; default is a deterministic local summary so
    automated tests never call paid services.
    """
    if os.getenv("LIFE_INBOX_DISABLE_VISION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return _fallback_description(
            original_filename, mime_type, ocr_text, ocr_note
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return _fallback_description(
            original_filename, mime_type, ocr_text, ocr_note
        )

    # Live vision is intentionally not invoked from the default path so tests
    # and local runs stay free of paid calls. Operators can extend this adapter.
    return _fallback_description(
        original_filename, mime_type, ocr_text, ocr_note
    )


def _fallback_description(
    original_filename: str,
    mime_type: str,
    ocr_text: str,
    ocr_note: Optional[str],
) -> dict:
    kind = "screenshot or photo"
    if mime_type == "image/png":
        kind = "PNG image (often a screenshot)"
    elif mime_type in {"image/jpeg", "image/jpg"}:
        kind = "JPEG photo/image"
    elif mime_type == "image/webp":
        kind = "WebP image"
    elif mime_type == "image/gif":
        kind = "GIF image"

    parts = [
        f"Uploaded {kind} named “{original_filename}”.",
    ]
    if ocr_text:
        preview = ocr_text.replace("\n", " ").strip()
        if len(preview) > 160:
            preview = preview[:157] + "…"
        parts.append(f"OCR found readable text starting with: {preview}")
    elif ocr_note:
        parts.append(f"OCR note: {ocr_note}")
    else:
        parts.append("No OCR text was extracted.")

    parts.append(
        "Suggested category: Media / Screenshots. "
        "Possible actions: review text, file under a topic, or create a follow-up task."
    )
    return {
        "description": " ".join(parts),
        "provider": "local_fallback",
        "inferred": True,
    }


def process_image_bytes(
    data: bytes,
    *,
    original_filename: str,
    mime_type: str,
) -> dict:
    """Run thumbnail + OCR + description. Always returns a processable result."""
    # Thumbnail generation validates the image is readable.
    thumbnail = make_thumbnail_bytes(data)
    ocr = run_ocr(data)
    description = describe_image(
        original_filename=original_filename,
        mime_type=mime_type,
        ocr_text=ocr["text"],
        ocr_note=ocr.get("note"),
    )
    title = _title_from_filename(original_filename)
    ocr_text = ocr["text"]
    ai_description = description["description"]

    # Searchable combined body: facts first, then clearly labeled inference.
    raw_parts = []
    if ocr_text:
        raw_parts.append(ocr_text)
    raw_parts.append(f"[AI description — inferred]\n{ai_description}")
    raw_text = "\n\n".join(raw_parts)

    return {
        "title": title,
        "raw_text": raw_text,
        "extracted_text": ocr_text,
        "ai_description": ai_description,
        "mime_type": mime_type,
        "thumbnail_bytes": thumbnail,
        "ocr_provider": ocr["provider"],
        "ocr_available": ocr["available"],
        "ocr_note": ocr.get("note"),
        "description_inferred": True,
    }
