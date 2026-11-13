"""Voice note validation and transcription adapters."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from app.capture.gnani_stt import (
    GnaniError,
    gnani_configured,
    transcribe_with_gnani,
)
from app.capture.storage import (
    MAX_AUDIO_BYTES,
    normalize_filename,
    title_from_filename,
)

logger = logging.getLogger(__name__)

PENDING_MARKER = "[Voice note — transcript pending]"
ALLOWED_AUDIO_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mpeg"}
ALLOWED_AUDIO_MIMES = {
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
    "video/webm",  # MediaRecorder often reports webm as video/webm
}

# Loose magic sniffing for common containers.
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"RIFF", "audio/wav", ".wav"),  # refined for WAVE
    (b"ID3", "audio/mpeg", ".mp3"),
    (b"\xff\xfb", "audio/mpeg", ".mp3"),
    (b"\xff\xf3", "audio/mpeg", ".mp3"),
    (b"\xff\xf2", "audio/mpeg", ".mp3"),
    (b"OggS", "audio/ogg", ".ogg"),
    (b"\x1aE\xdf\xa3", "audio/webm", ".webm"),  # EBML / Matroska / WebM
]


def _sniff_audio(data: bytes, declared_ext: str) -> tuple[str, str]:
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return "audio/wav", ".wav"
    if data[4:8] == b"ftyp":
        return "audio/mp4", ".m4a"
    for magic, mime, ext in _SIGNATURES:
        if magic != b"RIFF" and data.startswith(magic):
            return mime, ext
    # MediaRecorder webm may not always match; allow by declared extension.
    if declared_ext in ALLOWED_AUDIO_EXTENSIONS:
        mime_guess = {
            ".webm": "audio/webm",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".mpeg": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".ogg": "audio/ogg",
        }.get(declared_ext, "audio/webm")
        return mime_guess, declared_ext
    raise ValueError(
        "File does not look like a supported audio note (webm/wav/mp3/m4a/ogg)."
    )


def validate_audio_upload(
    filename: str,
    content_type: Optional[str],
    data: bytes,
) -> tuple[str, str, str]:
    """Validate upload; return (safe_filename, mime, extension)."""
    safe_name = normalize_filename(filename, "voice-note.webm")
    suffix = Path(safe_name).suffix.lower()
    if suffix and suffix not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError(
            "Only webm, wav, mp3, m4a, or ogg audio files are allowed."
        )

    if not data:
        raise ValueError("Uploaded audio is empty.")

    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Audio exceeds the {MAX_AUDIO_BYTES // (1024 * 1024)} MB upload limit."
        )

    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime and mime not in ALLOWED_AUDIO_MIMES:
            raise ValueError(
                "Unsupported content type. Upload a voice note audio file."
            )

    detected_mime, detected_ext = _sniff_audio(data, suffix or ".webm")
    if not suffix:
        safe_name = f"{Path(safe_name).stem}{detected_ext}"
    return safe_name, detected_mime, detected_ext


def _transcription_model() -> str:
    """Model for audio transcription; override via OPENAI_TRANSCRIPTION_MODEL."""
    return (
        os.getenv("OPENAI_TRANSCRIPTION_MODEL", "").strip()
        or "whisper-1"
    )


def _friendly_transcription_unavailable_note() -> str:
    return (
        "Transcription is temporarily unavailable. You can edit the "
        "transcript manually, or retry processing later."
    )


def transcribe_audio(
    data: bytes,
    *,
    original_filename: str,
    mime_type: str,
) -> dict:
    """Transcription adapter. Graceful when no live API is configured.

    Returns:
        text, provider, available, note
    """
    if os.getenv("LIFE_INBOX_DISABLE_TRANSCRIPTION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return {
            "text": "",
            "provider": "disabled",
            "available": False,
            "note": "Transcription disabled via LIFE_INBOX_DISABLE_TRANSCRIPTION.",
        }

    gnani_note: Optional[str] = None
    if gnani_configured():
        try:
            text = transcribe_with_gnani(
                data, original_filename=original_filename
            )
            return {
                "text": text,
                "provider": "gnani",
                "available": True,
                "note": None if text else "Transcription returned no spoken text.",
            }
        except GnaniError as exc:
            gnani_note = str(exc)
            logger.warning("Gnani transcription failed: %s", gnani_note)
        except Exception:
            logger.exception("Unexpected Gnani transcription error")
            gnani_note = _friendly_transcription_unavailable_note()

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {
            "text": "",
            "provider": "unavailable" if gnani_note else "none",
            "available": False,
            "note": gnani_note
            or (
                "Transcription not configured. Set GNANI_API_KEY or "
                "OPENAI_API_KEY for live transcription, or edit the "
                "transcript manually."
            ),
        }

    try:
        from openai import OpenAI

        client = OpenAI()
        model = _transcription_model()
        safe_name = normalize_filename(original_filename, "voice-note.webm")
        result = client.audio.transcriptions.create(
            model=model,
            file=(safe_name, data, mime_type or "application/octet-stream"),
        )
        if isinstance(result, str):
            text = result.strip()
        else:
            text = str(getattr(result, "text", "") or "").strip()
        return {
            "text": text,
            "provider": "openai",
            "available": True,
            "note": None if text else "Transcription returned no spoken text.",
        }
    except Exception:
        logger.exception(
            "Voice transcription failed for %s; preserving audio with empty transcript",
            original_filename,
        )
        return {
            "text": "",
            "provider": "unavailable",
            "available": False,
            "note": _friendly_transcription_unavailable_note(),
        }


def process_voice_bytes(
    data: bytes,
    *,
    original_filename: str,
    mime_type: str,
) -> dict:
    """Persist-ready processing result for a voice note."""
    transcription = transcribe_audio(
        data,
        original_filename=original_filename,
        mime_type=mime_type,
    )
    title = title_from_filename(original_filename, "Voice note")
    transcript = transcription["text"]
    note = transcription.get("note")

    if transcript:
        raw_text = transcript
    else:
        # Search, analysis and the assistant skip notes with this marker.
        raw_text = (
            f"{PENDING_MARKER}\n"
            f"Filename: {original_filename}\n"
            f"{note or 'No transcript yet. Edit to add text.'}"
        )

    return {
        "title": title,
        "raw_text": raw_text,
        "extracted_text": transcript,
        "mime_type": mime_type,
        "transcription_provider": transcription["provider"],
        "transcription_available": transcription["available"],
        "transcription_note": note,
    }
