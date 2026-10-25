"""Tiny WAV bytes for automated voice-note tests."""

from __future__ import annotations

import struct
import wave
from io import BytesIO


def make_wav_bytes(*, duration_ms: int = 100, sample_rate: int = 8000) -> bytes:
    """Generate a short silent mono WAV."""
    n_frames = max(1, (sample_rate * duration_ms) // 1000)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        silence = struct.pack("<h", 0) * n_frames
        handle.writeframes(silence)
    return buffer.getvalue()
