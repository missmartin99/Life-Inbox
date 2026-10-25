"""Local filesystem storage for uploaded capture sources."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

PDF_SUBDIR = "pdfs"
IMAGE_SUBDIR = "images"
AUDIO_SUBDIR = "audio"

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_AUDIO_BYTES = 10 * 1024 * 1024


def upload_root() -> Path:
    configured = os.getenv("LIFE_INBOX_UPLOAD_DIR", "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        root = (
            Path(__file__).resolve().parent.parent.parent / "data" / "uploads"
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def store_pdf_bytes(data: bytes) -> str:
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB upload limit."
        )
    name = f"{uuid.uuid4().hex}.pdf"
    relative = f"{PDF_SUBDIR}/{name}"
    target = upload_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


def store_image_bytes(data: bytes, extension: str) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"Image exceeds the {MAX_IMAGE_BYTES // (1024 * 1024)} MB upload limit."
        )
    ext = extension if extension.startswith(".") else f".{extension}"
    stem = uuid.uuid4().hex
    relative = f"{IMAGE_SUBDIR}/{stem}{ext}"
    target = upload_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


def thumbnail_relative_path(image_relative_path: str) -> str:
    path = Path(image_relative_path)
    return str(path.with_name(f"{path.stem}_thumb.jpg"))


def store_thumbnail_bytes(image_relative_path: str, data: bytes) -> str:
    relative = thumbnail_relative_path(image_relative_path)
    target = upload_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


def store_audio_bytes(data: bytes, extension: str) -> str:
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Audio exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)} MB upload limit."
        )
    ext = extension if extension.startswith(".") else f".{extension}"
    name = f"{uuid.uuid4().hex}{ext}"
    relative = f"{AUDIO_SUBDIR}/{name}"
    target = upload_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


def resolve_storage_path(relative_path: str) -> Path:
    root = upload_root().resolve()
    target = (root / relative_path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError("Invalid storage path.")
    if not target.is_file():
        raise FileNotFoundError("Stored file not found.")
    return target


def read_stored_bytes(relative_path: str) -> bytes:
    return resolve_storage_path(relative_path).read_bytes()


def delete_stored_file(relative_path: str | None) -> None:
    if not relative_path:
        return
    try:
        path = resolve_storage_path(relative_path)
    except (ValueError, FileNotFoundError):
        return
    path.unlink(missing_ok=True)
    # Best-effort thumbnail cleanup for images.
    if relative_path.startswith(f"{IMAGE_SUBDIR}/"):
        thumb = thumbnail_relative_path(relative_path)
        try:
            resolve_storage_path(thumb).unlink(missing_ok=True)
        except (ValueError, FileNotFoundError):
            pass
