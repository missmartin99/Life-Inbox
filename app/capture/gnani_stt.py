"""Gnani Vachana speech-to-text adapter (Indian languages, Hinglish).

The REST endpoint accepts WAV/MP3/OGG/FLAC/AAC/M4A clips of at most 30 s, so
browser recordings (WebM) and longer notes are first converted to 16 kHz mono
WAV with ffmpeg and split into short segments, which are transcribed in order.

Environment variables:
    GNANI_API_KEY     required to enable this provider
    GNANI_MODEL       default "prisma-v3.0" ("prisma-v2.5" also works)
    GNANI_LANGUAGE    default "hi-IN,en-IN"; prisma-v3.0 accepts "indic-auto" or
                      up to 4 comma-separated codes; prisma-v2.5 uses the first
    GNANI_FORMAT      default "transcribe" (also: verbatim, codemix,
                      transliterate on prisma-v3.0)
    GNANI_TOPIC       optional hint about what your notes are about
"""

from __future__ import annotations

import io
import logging
import os
import wave
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

GNANI_STT_URL = "https://api.vachana.ai/stt/v3"
SEGMENT_SECONDS = 25  # API limit is 30 s; leave headroom
REQUEST_TIMEOUT_SECONDS = 60
MAX_SEGMENTS = 40  # ~16 minutes; protects credits from huge uploads


class GnaniError(Exception):
    """Raised when Gnani transcription cannot be completed.

    The message is safe to show to the user (never contains the API key).
    """


def gnani_configured() -> bool:
    return bool(os.getenv("GNANI_API_KEY", "").strip())


def _model() -> str:
    return os.getenv("GNANI_MODEL", "").strip() or "prisma-v3.0"


def _language_code(model: str) -> str:
    raw = os.getenv("GNANI_LANGUAGE", "").strip() or "hi-IN,en-IN"
    if model == "prisma-v3.0":
        return raw
    # prisma-v2.5 accepts exactly one language.
    return raw.split(",")[0].strip() or "hi-IN"


def _usable_wav_as_is(data: bytes) -> bool:
    """True for a 16-bit mono WAV short enough to send without ffmpeg."""
    try:
        with wave.open(io.BytesIO(data)) as wav:
            seconds = wav.getnframes() / float(wav.getframerate() or 1)
            return (
                wav.getnchannels() == 1
                and wav.getsampwidth() == 2
                and wav.getframerate() in (8000, 16000, 22050, 24000, 44100, 48000)
                and 0 < seconds <= SEGMENT_SECONDS
            )
    except (wave.Error, EOFError):
        return False


def _convert_to_wav_segments(data: bytes, filename: str) -> list[bytes]:
    """Convert any supported input to 16 kHz mono WAV segments of ~25 s."""
    if _usable_wav_as_is(data):
        return [data]  # the cat's own recordings: no ffmpeg needed
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise GnaniError(
            "ffmpeg is not installed, so the recording can't be prepared for "
            "transcription. Install it (macOS: brew install ffmpeg)."
        )

    suffix = Path(filename or "voice-note.webm").suffix.lower() or ".webm"
    with tempfile.TemporaryDirectory(prefix="gnani-stt-") as tmp:
        tmp_dir = Path(tmp)
        source = tmp_dir / f"input{suffix}"
        source.write_bytes(data)
        pattern = str(tmp_dir / "seg_%03d.wav")
        command = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "segment",
            "-segment_time",
            str(SEGMENT_SECONDS),
            "-reset_timestamps",
            "1",
            pattern,
        ]
        try:
            subprocess.run(
                command, check=True, capture_output=True, timeout=120
            )
        except subprocess.CalledProcessError as exc:
            logger.error(
                "ffmpeg failed: %s",
                (exc.stderr or b"").decode("utf-8", "replace")[-500:],
            )
            raise GnaniError("Could not read this audio file.") from exc
        except subprocess.TimeoutExpired as exc:
            raise GnaniError("Preparing the audio took too long.") from exc

        segments = sorted(tmp_dir.glob("seg_*.wav"))
        if not segments:
            raise GnaniError("No audio found in this recording.")
        if len(segments) > MAX_SEGMENTS:
            raise GnaniError(
                "This recording is too long for voice transcription "
                f"(limit about {MAX_SEGMENTS * SEGMENT_SECONDS // 60} minutes)."
            )
        return [path.read_bytes() for path in segments]


def _error_message(response: requests.Response) -> str:
    """Turn a Gnani error response into a short, user-safe message."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    code = ""
    if isinstance(detail, dict):
        code = str(detail.get("error_code") or "")
    elif isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = str(body["error"].get("type") or "")

    if status in (401, 403):
        return "Gnani rejected the API key (check GNANI_API_KEY and its scopes)."
    if status == 402:
        return "Gnani credits are exhausted."
    if status == 429:
        return "Gnani is rate limiting requests. Try again in a moment."
    return f"Gnani transcription failed (HTTP {status}{', ' + code if code else ''})."


def _transcribe_segment(
    segment: bytes,
    *,
    api_key: str,
    model: str,
    previous_text: str,
) -> str:
    form = {
        "model": model,
        "language_code": _language_code(model),
        "format": os.getenv("GNANI_FORMAT", "").strip() or "transcribe",
    }
    topic = os.getenv("GNANI_TOPIC", "").strip()
    if model == "prisma-v3.0":
        if topic:
            form["topic"] = topic[:1600]
        if previous_text:
            form["previous_text"] = previous_text[-600:]

    last_error = "Gnani transcription failed."
    for attempt in range(2):
        try:
            response = requests.post(
                GNANI_STT_URL,
                headers={"X-API-Key-ID": api_key},
                files={"audio_file": ("segment.wav", segment, "audio/wav")},
                data=form,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("Gnani request error: %s", type(exc).__name__)
            raise GnaniError("Could not reach Gnani. Check your connection.") from exc

        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError as exc:
                raise GnaniError("Gnani returned an unreadable response.") from exc
            return str(body.get("transcript") or "").strip()

        last_error = _error_message(response)
        # Retry once on rate limit / transient upstream errors.
        if response.status_code in (429, 500, 503, 504) and attempt == 0:
            time.sleep(1.5)
            continue
        break
    raise GnaniError(last_error)


def transcribe_with_gnani(data: bytes, *, original_filename: str) -> str:
    """Transcribe audio bytes with Gnani; returns text ('' if no speech)."""
    api_key = os.getenv("GNANI_API_KEY", "").strip()
    if not api_key:
        raise GnaniError("GNANI_API_KEY is not set.")

    model = _model()
    segments = _convert_to_wav_segments(data, original_filename)
    parts: list[str] = []
    previous = ""
    for segment in segments:
        text = _transcribe_segment(
            segment, api_key=api_key, model=model, previous_text=previous
        )
        if text:
            parts.append(text)
            previous = text
    return " ".join(parts).strip()
