"""Uploaded profile photos.

Every upload is re-drawn as a fresh 256x256 JPEG. That crops it to a square,
turns phone photos the right way up, and drops everything else in the file:
camera details, GPS location, and anything that is not plain pixels.
"""

from __future__ import annotations

import io
import secrets

AVATAR_SIZE = 256
AVATAR_SUBDIR = "avatars"
MAX_AVATAR_PIXELS = 40_000_000  # refuse huge images before decoding them
BACKGROUND = (24, 24, 27)  # fills transparent areas; matches the dark UI

UNREADABLE = "That image could not be read. Try a JPEG or PNG photo."


def make_avatar_bytes(data: bytes) -> bytes:
    """Return a square JPEG profile photo, or raise ValueError."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_AVATAR_PIXELS:
                raise ValueError("That image is too large. Use a smaller photo.")
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                image = image.convert("RGBA")
                flat = Image.new("RGB", image.size, BACKGROUND)
                flat.paste(image, mask=image.getchannel("A"))
                image = flat
            else:
                image = image.convert("RGB")
            square = ImageOps.fit(
                image, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS
            )
            out = io.BytesIO()
            square.save(out, format="JPEG", quality=88)
            return out.getvalue()
    except ValueError:
        raise
    except Exception as exc:  # corrupt files, decompression bombs, etc.
        raise ValueError(UNREADABLE) from exc


def new_avatar_path(user_id: int) -> str:
    """Random per upload, so a new photo always gets a new URL."""
    return f"{AVATAR_SUBDIR}/u{user_id}-{secrets.token_hex(8)}.jpg"
