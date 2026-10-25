"""Tiny image bytes for automated tests (generated in-memory)."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw


def make_png_bytes(
    *,
    text: str = "Life Inbox",
    size: tuple[int, int] = (240, 80),
) -> bytes:
    image = Image.new("RGB", size, color=(245, 248, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, size[0] - 8, size[1] - 8), outline=(11, 114, 133), width=2)
    draw.text((16, 28), text, fill=(17, 17, 17))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def make_jpeg_bytes() -> bytes:
    image = Image.new("RGB", (64, 48), color=(30, 90, 140))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()
