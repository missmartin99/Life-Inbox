"""PDF upload validation and text extraction."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

from app.capture.storage import MAX_PDF_BYTES

PDF_MAGIC = b"%PDF"
ALLOWED_PDF_MIMES = {
    "application/pdf",
    "application/x-pdf",
}


def _normalize_filename(filename: str) -> str:
    name = Path(filename or "document.pdf").name
    if not name or name in {".", ".."}:
        return "document.pdf"
    return name


def validate_pdf_upload(
    filename: str,
    content_type: Optional[str],
    data: bytes,
) -> str:
    """Validate upload bytes; return sanitized original filename."""
    safe_name = _normalize_filename(filename)
    if not safe_name.lower().endswith(".pdf"):
        raise ValueError("Only .pdf files are allowed.")

    if not data:
        raise ValueError("Uploaded file is empty.")

    if len(data) > MAX_PDF_BYTES:
        raise ValueError(
            f"PDF exceeds the {MAX_PDF_BYTES // (1024 * 1024)} MB upload limit."
        )

    if not data.startswith(PDF_MAGIC):
        raise ValueError("File does not look like a valid PDF.")

    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime and mime not in ALLOWED_PDF_MIMES:
            raise ValueError(
                "Unsupported content type. Upload a PDF (application/pdf)."
            )

    return safe_name


def _title_from_filename(filename: str) -> str:
    stem = Path(filename).stem.strip()
    return stem or "PDF document"


def extract_pdf_text(
    data: bytes,
    *,
    original_filename: Optional[str] = None,
) -> dict:
    """Extract text and metadata from PDF bytes."""
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        raise ValueError(
            "This PDF is password-protected or encrypted and cannot be read."
        )

    meta_title = None
    if reader.metadata and reader.metadata.title:
        meta_title = str(reader.metadata.title).strip() or None

    parts: list[str] = []
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError("Unable to read text from this PDF.") from exc
        cleaned = " ".join(page_text.split())
        if cleaned:
            parts.append(cleaned)

    extracted = "\n\n".join(parts).strip()
    if not extracted:
        raise ValueError(
            "No extractable text found. This PDF may be scanned or image-only."
        )

    title = meta_title or _title_from_filename(original_filename or "document.pdf")
    return {
        "title": title,
        "raw_text": extracted,
        "extracted_text": extracted,
        "mime_type": "application/pdf",
    }
