"""Profile photos: the Google account picture and uploaded photos.

Google is never contacted: its HTTP replies are faked, as in
test_auth_isolation.py.
"""

import importlib
import io
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from PIL import Image

MODULES = (
    "app.main",
    "app.database",
    "app.auth",
    "app.config",
    "app.google_oauth",
    "app.profile_photos",
    "app.integrations.calendar",
)

GOOGLE_PICTURE = "https://lh3.googleusercontent.com/a/photo-1=s96-c"


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'photos.db'}")
    monkeypatch.setenv("LIFE_INBOX_AUTO_CREATE_TABLES", "1")
    monkeypatch.setenv("LIFE_INBOX_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("LIFE_INBOX_AUTH_MODE", "google")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("LIFE_INBOX_PUBLIC_URL", "http://testserver")
    for name in (
        "LIFE_INBOX_ENV",
        "LIFE_INBOX_ALLOWED_EMAILS",
        "LIFE_INBOX_ALLOWED_EMAIL_DOMAINS",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        sys.modules.pop(name, None)
    main = importlib.import_module("app.main")
    database = importlib.import_module("app.database")
    google_oauth = importlib.import_module("app.google_oauth")
    with TestClient(main.app) as client:
        yield SimpleNamespace(
            client=client,
            database=database,
            google_oauth=google_oauth,
            uploads=tmp_path / "uploads",
        )
    database.engine.dispose()
    for name in MODULES:
        sys.modules.pop(name, None)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def make_user(database, email):
    user = database.ensure_user(
        email=email, display_name=email.split("@")[0], auth_provider="google"
    )
    token = database.create_auth_session(user["id"])
    return user, {"Cookie": f"life_inbox_session={token}"}


def google_sign_in(ctx, monkeypatch, picture=GOOGLE_PICTURE):
    start = ctx.client.get("/api/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    info = {
        "sub": "g-1",
        "email": "priya@example.com",
        "email_verified": True,
        "name": "Priya Sharma",
        "picture": picture,
    }
    monkeypatch.setattr(
        ctx.google_oauth.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"access_token": "tok"}),
    )
    monkeypatch.setattr(
        ctx.google_oauth.requests, "get", lambda *a, **k: FakeResponse(200, info)
    )
    done = ctx.client.get(
        "/api/auth/google/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert done.status_code == 302 and done.headers["location"] == "/"
    return ctx.client.get("/api/auth/me").json()


def image_bytes(size=(600, 300), mode="RGB", fmt="PNG", **save_args):
    color = (200, 40, 40, 128) if mode == "RGBA" else (200, 40, 40)
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt, **save_args)
    return out.getvalue()


def upload(ctx, data, headers=None, name="me.png", content_type="image/png"):
    return ctx.client.post(
        "/api/me/avatar",
        files={"file": (name, data, content_type)},
        headers=headers or {},
    )


def stored_photos(ctx):
    folder = ctx.uploads / "avatars"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- the Google picture ------------------------------------------------------


def test_google_picture_is_used_after_sign_in(ctx, monkeypatch):
    me = google_sign_in(ctx, monkeypatch)
    assert me["avatar_url"] == GOOGLE_PICTURE
    assert me["has_custom_avatar"] is False


def test_google_picture_follows_changes_at_next_sign_in(ctx, monkeypatch):
    google_sign_in(ctx, monkeypatch)
    newer = "https://lh3.googleusercontent.com/a/photo-2=s96-c"
    assert google_sign_in(ctx, monkeypatch, picture=newer)["avatar_url"] == newer
    assert google_sign_in(ctx, monkeypatch, picture=None)["avatar_url"] is None


@pytest.mark.parametrize(
    "picture",
    [
        "http://lh3.googleusercontent.com/a/photo",  # not https
        "https://evil.example.com/photo.png",  # not Google's image host
        "https://googleusercontent.com.evil.example/photo.png",
        "javascript:alert(1)",
        "https://lh3.googleusercontent.com/" + "a" * 3000,  # absurdly long
        12345,
    ],
)
def test_unsafe_picture_links_are_dropped(ctx, monkeypatch, picture):
    assert google_sign_in(ctx, monkeypatch, picture=picture)["avatar_url"] is None


# --- uploaded photos ---------------------------------------------------------


def test_upload_becomes_a_small_square_jpeg_without_hidden_details(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    exif = Image.Exif()
    exif[0x010F] = "Phone maker"  # stands in for camera / location details
    source = image_bytes(size=(900, 400), fmt="JPEG", exif=exif.tobytes())

    response = upload(ctx, source, a, name="me.jpg", content_type="image/jpeg")
    assert response.status_code == 200
    user = response.json()
    assert user["has_custom_avatar"] is True
    assert user["avatar_url"].startswith("/api/me/avatar?v=")

    photo = ctx.client.get(user["avatar_url"], headers=a)
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"
    assert "private" in photo.headers["cache-control"]
    with Image.open(io.BytesIO(photo.content)) as image:
        assert image.format == "JPEG"
        assert image.size == (256, 256)
        assert len(image.getexif()) == 0


def test_transparent_png_upload_works(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    assert upload(ctx, image_bytes(mode="RGBA"), a).status_code == 200


@pytest.mark.parametrize(
    "data, name, content_type",
    [
        (b"just some text", "notes.txt", "text/plain"),
        (b"\x89PNG\r\n\x1a\n" + b"not really a png", "broken.png", "image/png"),
        (b"", "empty.png", "image/png"),
        (b"\xff\xd8\xff" + b"0" * (6 * 1024 * 1024), "huge.jpg", "image/jpeg"),
    ],
)
def test_bad_uploads_are_refused(ctx, data, name, content_type):
    _, a = make_user(ctx.database, "alice@example.com")
    response = upload(ctx, data, a, name=name, content_type=content_type)
    assert response.status_code == 400
    assert response.json()["detail"]
    assert ctx.client.get("/api/auth/me", headers=a).json()["avatar_url"] is None
    assert stored_photos(ctx) == []


def test_new_photo_replaces_the_old_file_and_remove_brings_back_google(
    ctx, monkeypatch
):
    google_sign_in(ctx, monkeypatch)
    first = upload(ctx, image_bytes()).json()
    [first_file] = stored_photos(ctx)
    second = upload(ctx, image_bytes(size=(300, 300))).json()
    assert second["avatar_url"] != first["avatar_url"]  # new URL, no stale cache
    assert stored_photos(ctx) != [first_file] and len(stored_photos(ctx)) == 1

    removed = ctx.client.delete("/api/me/avatar")
    assert removed.status_code == 200
    assert removed.json()["avatar_url"] == GOOGLE_PICTURE
    assert removed.json()["has_custom_avatar"] is False
    assert stored_photos(ctx) == []
    assert ctx.client.get("/api/me/avatar").status_code == 404

    # Signing in with Google again does not undo an uploaded photo.
    upload(ctx, image_bytes())
    assert google_sign_in(ctx, monkeypatch)["has_custom_avatar"] is True


def test_photos_are_private_to_their_owner(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    _, b = make_user(ctx.database, "bob@example.com")
    assert upload(ctx, image_bytes(), a).status_code == 200

    assert ctx.client.get("/api/me/avatar", headers=b).status_code == 404
    assert ctx.client.get("/api/auth/me", headers=b).json()["avatar_url"] is None
    assert ctx.client.delete("/api/me/avatar", headers=b).status_code == 200
    assert ctx.client.get("/api/me/avatar", headers=a).status_code == 200

    assert ctx.client.get("/api/me/avatar").status_code == 401
    assert upload(ctx, image_bytes()).status_code == 401


def test_other_sites_cannot_change_your_photo(ctx):
    _, a = make_user(ctx.database, "alice@example.com")
    response = ctx.client.post(
        "/api/me/avatar",
        files={"file": ("me.png", image_bytes(), "image/png")},
        headers={**a, "Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert stored_photos(ctx) == []
