import hashlib
import json
import os
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_current_owner_id: ContextVar[Optional[int]] = ContextVar(
    "life_inbox_owner_id", default=None
)

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    event,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker

from app.capture.pipeline import (
    ANALYZABLE_STATUSES,
    ANALYZABLE_TYPES,
    STATUS_CAPTURED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_READY,
    IMAGE_ITEM_TYPE,
    PDF_ITEM_TYPE,
    URL_ITEM_TYPE,
    VOICE_ITEM_TYPE,
)
from app.capture.storage import delete_stored_file
from app.schemas import AnalysisResult
from app.similarity import find_item_relations


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "life_inbox.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH}"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
AUTO_CREATE_TABLES = os.getenv(
    "LIFE_INBOX_AUTO_CREATE_TABLES", ""
).strip().lower() in {"1", "true", "yes"}


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        # `timeout` makes concurrent writers wait instead of failing at once.
        return {"check_same_thread": False, "timeout": 15}
    return {}


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {}
    return {
        "pool_pre_ping": True,
        "pool_size": int(os.getenv("LIFE_INBOX_DB_POOL_SIZE", "10") or "10"),
        "max_overflow": int(os.getenv("LIFE_INBOX_DB_MAX_OVERFLOW", "20") or "20"),
    }


engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args(DATABASE_URL),
    **_engine_kwargs(DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite only enforces CASCADE when foreign_keys pragma is on."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        try:
            # WAL lets readers and one writer work at the same time.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except Exception:  # pragma: no cover - e.g. read-only media
            pass
        cursor.close()


TEXT_ITEM_TYPE = "text"
READY_STATUS = STATUS_READY
ANALYSIS_RUNNING = "running"
ANALYSIS_SUCCEEDED = "succeeded"
ANALYSIS_FAILED = "failed"
TASK_OPEN = "open"
TASK_COMPLETED = "completed"
TASK_DISMISSED = "dismissed"
TASK_STATUSES = {TASK_OPEN, TASK_COMPLETED, TASK_DISMISSED}
DEFAULT_OWNER_EMAIL = "local@life-inbox.local"


class UserRecord(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_google_sub", "google_sub", unique=True),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    auth_provider = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    google_sub = Column(Text, nullable=True)
    last_login_at = Column(Text, nullable=True)
    # Profile photo: an uploaded one wins over the Google account picture.
    google_picture_url = Column(Text, nullable=True)
    avatar_path = Column(Text, nullable=True)  # relative upload-storage path
    date_of_birth = Column(Text, nullable=True)  # ISO date, e.g. 1999-08-21
    settings_json = Column(Text, nullable=True)  # see schemas.UserSettings
    password_hash = Column(Text, nullable=True)  # local accounts only (app.auth)


class AuthSessionRecord(Base):
    """Server-side login session. Only a hash of the cookie token is stored."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_token_hash", "token_hash", unique=True),
        Index("ix_auth_sessions_user_id", "user_id"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(Text, nullable=False)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)
    last_seen_at = Column(Text, nullable=False)
    revoked_at = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)


class InboxItemRecord(Base):
    __tablename__ = "inbox_items"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_type = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=True)
    source_url = Column(Text, nullable=True)
    original_filename = Column(Text, nullable=True)
    mime_type = Column(Text, nullable=True)
    processing_status = Column(Text, nullable=False)
    error_message = Column(Text, nullable=True)
    storage_path = Column(Text, nullable=True)
    extracted_text = Column(Text, nullable=True)
    ai_description = Column(Text, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=True)
    # Privacy: sensitive items are never sent to the AI model.
    is_sensitive = Column(Boolean, nullable=False, default=False, server_default="0")
    # Smart templates: "recipe", "book", "contact", "event", "other" (or None
    # if not checked yet) and its fields stored as JSON text.
    kind = Column(Text, nullable=True)
    kind_fields = Column(Text, nullable=True)


class EntityRecord(Base):
    """A person, place or organization mentioned in the inbox (stored once)."""

    __tablename__ = "entities"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id", "entity_type", "name_key", name="uq_entities_owner_type_name"
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(Text, nullable=False)
    name_key = Column(Text, nullable=False)
    entity_type = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class ItemEntityRecord(Base):
    """Link table: which items mention which entities (many-to-many)."""

    __tablename__ = "item_entities"

    item_id = Column(
        Integer, ForeignKey("inbox_items.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id = Column(
        Integer, ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True
    )


class EmbeddingCacheRecord(Base):
    """Saved embedding vectors, keyed by a hash of the text they came from."""

    __tablename__ = "embedding_cache"

    text_hash = Column(Text, primary_key=True)
    model = Column(Text, primary_key=True)
    vector = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class FlashcardRecord(Base):
    """A question/answer card made from an item, scheduled with Leitner boxes."""

    __tablename__ = "flashcards"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    item_id = Column(
        Integer, ForeignKey("inbox_items.id", ondelete="CASCADE"), nullable=False
    )
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    box = Column(Integer, nullable=False, default=0, server_default="0")
    due_date = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    last_reviewed_at = Column(Text, nullable=True)


class ApiTokenRecord(Base):
    """A token a program uses instead of logging in. Only the hash is stored."""

    __tablename__ = "api_tokens"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(Text, nullable=False)
    token_hash = Column(Text, nullable=False, unique=True)
    created_at = Column(Text, nullable=False)
    last_used_at = Column(Text, nullable=True)


class WebhookRecord(Base):
    """A URL we POST to when an event (e.g. item.created) happens."""

    __tablename__ = "webhooks"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    url = Column(Text, nullable=False)
    event = Column(Text, nullable=False)
    secret = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class AiCallLogRecord(Base):
    """One row per AI call: what feature, which model, how much was sent.

    We deliberately do NOT store the prompt text itself.
    """

    __tablename__ = "ai_call_log"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, nullable=False)
    feature = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    chars_sent = Column(Integer, nullable=False)
    redactions = Column(Integer, nullable=False)


class AnalysisRunRecord(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(Text, nullable=False)
    completed_at = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    mode = Column(Text, nullable=False)
    result_json = Column(Text, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)


class TopicRecord(Base):
    __tablename__ = "topics"
    __table_args__ = (
        UniqueConstraint("name", "owner_user_id", name="uq_topics_name_owner"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)
    archived = Column(Integer, nullable=False, default=0)


class ItemInsightRecord(Base):
    __tablename__ = "item_insights"

    item_id = Column(
        Integer,
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    analysis_run_id = Column(
        Integer,
        ForeignKey("analysis_runs.id"),
        nullable=False,
    )
    category = Column(Text, nullable=False)
    concise_title = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    tags_json = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    user_title = Column(Text, nullable=True)
    user_topic_id = Column(Integer, ForeignKey("topics.id"), nullable=True)
    user_priority = Column(Text, nullable=True)
    user_tags_json = Column(Text, nullable=True)
    needs_review = Column(Integer, nullable=False, default=0)
    updated_at = Column(Text, nullable=False)


class ItemRelationRecord(Base):
    __tablename__ = "item_relations"
    __table_args__ = (
        UniqueConstraint(
            "item_id_a",
            "item_id_b",
            "relation_type",
            name="uq_item_relations_pair_type",
        ),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id_a = Column(
        Integer,
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    item_id_b = Column(
        Integer,
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type = Column(Text, nullable=False)
    score = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    dismissed = Column(Integer, nullable=False, default=0)


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "priority IN ('High', 'Medium', 'Low')",
            name="ck_tasks_priority",
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'dismissed')",
            name="ck_tasks_status",
        ),
        UniqueConstraint("dedup_key"),
        {"sqlite_autoincrement": True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    priority = Column(Text, nullable=False)
    timing_text = Column(Text, nullable=True)
    ai_timing_text = Column(Text, nullable=True)
    timing_confirmed = Column(Integer, nullable=False, default=0)
    due_on = Column(Text, nullable=True)
    due_confidence = Column(Text, nullable=True)
    snoozed_until = Column(Text, nullable=True)
    reminder_at = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    dedup_key = Column(Text, nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)


class CalendarActionReceiptRecord(Base):
    __tablename__ = "calendar_action_receipts"
    __table_args__ = {"sqlite_autoincrement": True}

    id = Column(Integer, primary_key=True, autoincrement=True)
    draft_id = Column(Text, nullable=False)
    task_id = Column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)


class TaskSourceItemRecord(Base):
    __tablename__ = "task_source_items"

    task_id = Column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    item_id = Column(
        Integer,
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        primary_key=True,
    )


class CalendarConnectionRecord(Base):
    """One person's Google Calendar connection. Tokens are stored encrypted."""

    __tablename__ = "calendar_connections"
    __table_args__ = (
        Index("ix_calendar_connections_user_id", "user_id", unique=True),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    google_email = Column(Text, nullable=True)
    scopes = Column(Text, nullable=False)
    refresh_token_enc = Column(Text, nullable=False)
    access_token_enc = Column(Text, nullable=True)
    access_token_expires_at = Column(Text, nullable=True)
    calendar_id = Column(Text, nullable=True)  # the "Life Inbox" calendar
    status = Column(Text, nullable=False)  # connected | needs_reconnect
    last_synced_at = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    connected_at = Column(Text, nullable=False)
    updated_at = Column(Text, nullable=False)


class CalendarTaskEventRecord(Base):
    """Which Google Calendar event shows which task.

    task_id is deliberately not a foreign key: when a task is deleted, this row
    must survive until the next sync has removed the event from the calendar.
    """

    __tablename__ = "calendar_task_events"
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", name="uq_calendar_task_events_task"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id = Column(Integer, nullable=False)
    event_id = Column(Text, nullable=False)
    fingerprint = Column(Text, nullable=False)  # what the event last looked like
    synced_at = Column(Text, nullable=False)


DEFAULT_TOPIC_NAMES = (
    "Work",
    "Money & Admin",
    "Learning",
    "Shopping & Errands",
    "Health",
    "Travel",
    "Ideas",
    "People",
    "Personal / Reference",
)


def set_current_owner_id(owner_user_id: Optional[int]) -> None:
    _current_owner_id.set(owner_user_id)


def get_current_owner_id() -> Optional[int]:
    return _current_owner_id.get()


def _avatar_url(user: UserRecord) -> Optional[str]:
    if user.avatar_path:
        # The stored file name is random per upload, so it doubles as a
        # cache-busting version: a new photo always gets a new URL.
        version = Path(user.avatar_path).stem.rsplit("-", 1)[-1]
        return f"/api/me/avatar?v={version}"
    return user.google_picture_url or None


def _settings_for(user: UserRecord) -> dict:
    """Saved settings merged over the defaults; bad values are ignored."""
    from pydantic import ValidationError

    from app.schemas import UserSettings

    saved = {}
    if user.settings_json:
        try:
            saved = json.loads(user.settings_json)
        except ValueError:
            saved = {}
    if not isinstance(saved, dict):
        saved = {}
    settings = UserSettings().model_dump()
    for key, value in saved.items():
        if key not in settings:
            continue
        try:
            settings[key] = getattr(UserSettings(**{key: value}), key)
        except ValidationError:
            pass  # keep the default for a value that is no longer valid
    return settings


def _user_to_dict(user: UserRecord) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "auth_provider": user.auth_provider,
        "created_at": user.created_at,
        "avatar_url": _avatar_url(user),
        "has_custom_avatar": bool(user.avatar_path),
        "date_of_birth": user.date_of_birth,
        "google_connected": bool(user.google_sub),
        "last_login_at": user.last_login_at,
        "settings": _settings_for(user),
    }


def get_user_by_id(user_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        return _user_to_dict(user) if user else None


def get_user_by_email(email: str) -> Optional[dict]:
    with SessionLocal() as session:
        user = session.scalar(
            select(UserRecord).where(UserRecord.email == email.strip().lower())
        )
        return _user_to_dict(user) if user else None


def ensure_user(
    *,
    email: str,
    display_name: str,
    auth_provider: str,
) -> dict:
    cleaned = email.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(
            select(UserRecord).where(UserRecord.email == cleaned)
        )
        if user is None:
            user = UserRecord(
                email=cleaned,
                display_name=display_name.strip() or cleaned,
                auth_provider=auth_provider,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(user)
            session.commit()
        return _user_to_dict(user)


def get_or_create_default_owner_id() -> int:
    user = ensure_user(
        email=DEFAULT_OWNER_EMAIL,
        display_name="Local Owner",
        auth_provider="local",
    )
    return user["id"]


class AuthRequiredError(Exception):
    """Raised when data is accessed without a signed-in user."""


class AnalysisAlreadyRunningError(Exception):
    """Raised when this user already has an analysis in progress."""


def require_owner_id() -> int:
    owner = get_current_owner_id()
    if owner is not None:
        return owner
    from app.config import load_config

    if load_config(strict=False).local_anonymous_allowed:
        return get_or_create_default_owner_id()
    raise AuthRequiredError("Authentication required.")


def _get_owned_item(session, item_id: int):
    """Fetch an inbox item only if the current user owns it."""
    item = session.get(InboxItemRecord, item_id)
    if item is None or item.owner_user_id != require_owner_id():
        return None
    return item


def task_is_owned(task_id: int) -> bool:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        return task is not None and task.owner_user_id == owner_id


SESSION_TTL = timedelta(days=14)
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_auth_session(user_id: int, user_agent: Optional[str] = None) -> str:
    """Create a login session and return the raw cookie token."""
    raw_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.execute(
            delete(AuthSessionRecord).where(
                AuthSessionRecord.expires_at < now.isoformat()
            )
        )
        session.add(
            AuthSessionRecord(
                token_hash=_hash_token(raw_token),
                user_id=user_id,
                created_at=now.isoformat(),
                expires_at=(now + SESSION_TTL).isoformat(),
                last_seen_at=now.isoformat(),
                user_agent=(user_agent or "")[:300] or None,
            )
        )
        session.commit()
    return raw_token


def get_user_for_session_token(raw_token: Optional[str]) -> Optional[dict]:
    """Return the user for a valid, unexpired, unrevoked session token."""
    if not raw_token or len(raw_token) > 200:
        return None
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        row = session.scalar(
            select(AuthSessionRecord).where(
                AuthSessionRecord.token_hash == _hash_token(raw_token)
            )
        )
        if row is None or row.revoked_at is not None:
            return None
        if datetime.fromisoformat(row.expires_at) <= now:
            return None
        user = session.get(UserRecord, row.user_id)
        if user is None:
            return None
        if now - datetime.fromisoformat(row.last_seen_at) > SESSION_TOUCH_INTERVAL:
            row.last_seen_at = now.isoformat()
            session.commit()
        return _user_to_dict(user)


def revoke_auth_session(raw_token: Optional[str]) -> bool:
    if not raw_token:
        return False
    with SessionLocal() as session:
        row = session.scalar(
            select(AuthSessionRecord).where(
                AuthSessionRecord.token_hash == _hash_token(raw_token)
            )
        )
        if row is None or row.revoked_at is not None:
            return False
        row.revoked_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return True


def revoke_all_auth_sessions(user_id: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        rows = session.scalars(
            select(AuthSessionRecord).where(
                AuthSessionRecord.user_id == user_id,
                AuthSessionRecord.revoked_at.is_(None),
            )
        ).all()
        for row in rows:
            row.revoked_at = now
        session.commit()
        return len(rows)


def upsert_google_user(
    *, sub: str, email: str, name: Optional[str], picture: Optional[str] = None
) -> dict:
    """Find or create the user for a verified Google identity.

    Accounts are matched on Google's stable `sub`. An existing account with
    the same (verified) email is linked on first Google login.
    """
    cleaned = email.strip().lower()
    if not sub or not cleaned or cleaned == DEFAULT_OWNER_EMAIL:
        raise ValueError("Invalid Google identity.")
    for attempt in range(2):
        now = datetime.now(timezone.utc).isoformat()
        with SessionLocal() as session:
            try:
                user = session.scalar(
                    select(UserRecord).where(UserRecord.google_sub == sub)
                )
                if user is None:
                    user = session.scalar(
                        select(UserRecord).where(UserRecord.email == cleaned)
                    )
                    if user is not None:
                        if user.google_sub and user.google_sub != sub:
                            raise ValueError(
                                "This email belongs to a different Google account."
                            )
                        user.google_sub = sub
                        user.auth_provider = "google"
                if user is None:
                    user = UserRecord(
                        email=cleaned,
                        display_name=(name or "").strip() or cleaned.split("@")[0],
                        auth_provider="google",
                        created_at=now,
                        google_sub=sub,
                        last_login_at=now,
                        google_picture_url=picture,
                    )
                    session.add(user)
                else:
                    if user.email != cleaned and session.scalar(
                        select(UserRecord.id).where(UserRecord.email == cleaned)
                    ) is None:
                        user.email = cleaned
                    user.last_login_at = now
                    user.google_picture_url = picture  # follow Google changes
                session.flush()
                ensure_default_topics(session, owner_user_id=user.id)
                session.commit()
                return _user_to_dict(user)
            except IntegrityError:
                session.rollback()
                if attempt == 1:
                    raise
    raise RuntimeError("unreachable")  # pragma: no cover


_UNSET = object()


def update_user_profile(
    user_id: int, *, display_name=_UNSET, date_of_birth=_UNSET
) -> dict:
    """Change the name and/or date of birth (already validated)."""
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        if user is None:
            raise AuthRequiredError("Authentication required.")
        if display_name is not _UNSET:
            user.display_name = display_name
        if date_of_birth is not _UNSET:
            user.date_of_birth = date_of_birth
        session.commit()
        return _user_to_dict(user)


def get_password_account(email: str) -> Optional[dict]:
    """The local account for an email, with its stored password hash."""
    with SessionLocal() as session:
        user = session.scalar(
            select(UserRecord).where(UserRecord.email == email.strip().lower())
        )
        if user is None:
            return None
        return {**_user_to_dict(user), "password_hash": user.password_hash}


def create_password_account(email: str, display_name: str, password_hash: str) -> dict:
    """Create a local account, or give an old passwordless local account its
    first password. Raises ValueError when the email already has a password
    or belongs to a Google account."""
    cleaned = email.strip().lower()
    with SessionLocal() as session:
        user = session.scalar(select(UserRecord).where(UserRecord.email == cleaned))
        if user is not None:
            if user.password_hash or user.auth_provider not in {"dev", "password"}:
                raise ValueError("An account with this email already exists.")
            user.password_hash = password_hash
            user.auth_provider = "password"
            if display_name:
                user.display_name = display_name
        else:
            user = UserRecord(
                email=cleaned,
                display_name=display_name or cleaned.split("@", 1)[0],
                auth_provider="password",
                created_at=datetime.now(timezone.utc).isoformat(),
                password_hash=password_hash,
            )
            session.add(user)
        session.commit()
        return _user_to_dict(user)


def get_user_settings(user_id: int) -> dict:
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        if user is None:
            raise AuthRequiredError("Authentication required.")
        return _settings_for(user)


def update_user_settings(user_id: int, changes: dict) -> dict:
    """Merge validated changes into the saved settings; return all settings."""
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        if user is None:
            raise AuthRequiredError("Authentication required.")
        settings = _settings_for(user)
        settings.update(changes)
        user.settings_json = json.dumps(settings, sort_keys=True)
        session.commit()
        return settings


def get_user_avatar_path(user_id: int) -> Optional[str]:
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        return user.avatar_path if user else None


def set_user_avatar_path(
    user_id: int, avatar_path: Optional[str]
) -> tuple[dict, Optional[str]]:
    """Set (or clear, with None) the uploaded photo.

    Returns the updated user and the previous file path, which the caller
    deletes from storage once the database points at the new one.
    """
    with SessionLocal() as session:
        user = session.get(UserRecord, user_id)
        if user is None:
            raise AuthRequiredError("Authentication required.")
        previous = user.avatar_path
        user.avatar_path = avatar_path
        session.commit()
        return _user_to_dict(user), previous


_OWNED_MODELS = (
    ("inbox_items", InboxItemRecord),
    ("tasks", TaskRecord),
    ("analysis_runs", AnalysisRunRecord),
    ("topics", TopicRecord),
)


def count_owned_data(user_id: int) -> dict:
    """How many rows of each owned kind belong to this user."""
    with SessionLocal() as session:
        return {
            name: session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.owner_user_id == user_id)
            )
            or 0
            for name, model in _OWNED_MODELS
        }


def transfer_owned_data(from_user_id: int, to_user_id: int) -> dict:
    """Move everything one user owns to another user, in one transaction.

    Used to claim the anonymous Local Owner's inbox after switching to Google
    sign-in. Topics with the same name are merged (insights are re-pointed),
    because topic names are unique per owner. Uploaded files need no move:
    storage paths are not per-user.
    """
    if from_user_id == to_user_id:
        raise ValueError("Source and target user are the same.")
    with SessionLocal() as session:
        if session.get(UserRecord, from_user_id) is None:
            raise ValueError(f"Unknown source user {from_user_id}.")
        if session.get(UserRecord, to_user_id) is None:
            raise ValueError(f"Unknown target user {to_user_id}.")

        target_topics = {
            topic.name: topic
            for topic in session.scalars(
                select(TopicRecord).where(TopicRecord.owner_user_id == to_user_id)
            ).all()
        }
        topics_moved = 0
        topics_merged = 0
        source_topics = session.scalars(
            select(TopicRecord).where(TopicRecord.owner_user_id == from_user_id)
        ).all()
        for topic in source_topics:
            existing = target_topics.get(topic.name)
            if existing is None:
                topic.owner_user_id = to_user_id
                target_topics[topic.name] = topic
                topics_moved += 1
                continue
            session.execute(
                update(ItemInsightRecord)
                .where(ItemInsightRecord.topic_id == topic.id)
                .values(topic_id=existing.id)
            )
            session.execute(
                update(ItemInsightRecord)
                .where(ItemInsightRecord.user_topic_id == topic.id)
                .values(user_topic_id=existing.id)
            )
            session.delete(topic)
            topics_merged += 1

        moved = {"topics_moved": topics_moved, "topics_merged": topics_merged}
        for name, model in _OWNED_MODELS:
            if model is TopicRecord:
                continue
            result = session.execute(
                update(model)
                .where(model.owner_user_id == from_user_id)
                .values(owner_user_id=to_user_id)
            )
            moved[name] = result.rowcount or 0
        session.commit()
        return moved


def ensure_default_topics(session, owner_user_id: Optional[int] = None) -> None:
    owner = owner_user_id
    statement = select(func.count()).select_from(TopicRecord)
    if owner is not None:
        statement = statement.where(TopicRecord.owner_user_id == owner)
    count = session.scalar(statement) or 0
    if count:
        return
    now = datetime.now(timezone.utc).isoformat()
    for name in DEFAULT_TOPIC_NAMES:
        session.add(
            TopicRecord(
                name=name,
                description=None,
                owner_user_id=owner,
                created_at=now,
                updated_at=now,
                archived=0,
            )
        )
    session.flush()


def init_db():
    """Prepare the DB connection.

    Alembic owns schema evolution. Table auto-creation is opt-in for isolated
    automated tests via LIFE_INBOX_AUTO_CREATE_TABLES=1.
    """
    if AUTO_CREATE_TABLES:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as session:
            owner = ensure_user(
                email=DEFAULT_OWNER_EMAIL,
                display_name="Local Owner",
                auth_provider="local",
            )
            ensure_default_topics(session, owner_user_id=owner["id"])
            session.commit()


def _item_to_dict(item: InboxItemRecord) -> dict:
    return {
        "id": item.id,
        "item_type": item.item_type,
        "title": item.title,
        "raw_text": item.raw_text,
        "source_url": item.source_url,
        "original_filename": item.original_filename,
        "mime_type": item.mime_type,
        "processing_status": item.processing_status,
        "error_message": item.error_message,
        "storage_path": item.storage_path,
        "extracted_text": item.extracted_text,
        "ai_description": item.ai_description,
        "owner_user_id": item.owner_user_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "saved_label": _saved_label(item.created_at),
        "is_sensitive": bool(item.is_sensitive),
        "kind": item.kind,
        "kind_fields": json.loads(item.kind_fields) if item.kind_fields else None,
    }


def _saved_label(created_at: Optional[str]) -> Optional[str]:
    from app.attention.dates import format_timestamp

    return format_timestamp(created_at)


def _task_due(task: "TaskRecord"):
    """(due date, time of day or None) for a task, read the same way as the bell."""
    from datetime import date as date_cls

    from app.attention.dates import parse_timing_phrase

    parsed = parse_timing_phrase(task.timing_text)
    due = parsed.due_date
    if task.due_on:
        try:
            due = date_cls.fromisoformat(task.due_on)
        except ValueError:
            pass
    return due, (parsed.due_time if due is not None else None)


def _add_task_details(session, items: list[dict], owner_id: int) -> None:
    """Give each note its priority and the deadline of its soonest open task."""
    from datetime import date as date_cls

    from app.attention.dates import (
        END_OF_DAY,
        classify_attention_bucket,
        format_deadline,
        now_in_configured_tz,
    )

    if not items:
        return
    ids = [item["id"] for item in items]
    insights = {
        row.item_id: row
        for row in session.scalars(
            select(ItemInsightRecord).where(ItemInsightRecord.item_id.in_(ids))
        )
    }
    tasks_by_item: dict[int, list[TaskRecord]] = {}
    for item_id, task in session.execute(
        select(TaskSourceItemRecord.item_id, TaskRecord)
        .join(TaskRecord, TaskRecord.id == TaskSourceItemRecord.task_id)
        .where(
            TaskSourceItemRecord.item_id.in_(ids),
            TaskRecord.owner_user_id == owner_id,
        )
    ).all():
        tasks_by_item.setdefault(item_id, []).append(task)

    def rank(priority: Optional[str]) -> int:
        return PRIORITY_ORDER.index(priority) if priority in PRIORITY_ORDER else 99

    now = now_in_configured_tz()
    for item in items:
        tasks = tasks_by_item.get(item["id"], [])
        open_tasks = [task for task in tasks if task.status == TASK_OPEN]
        insight = insights.get(item["id"])
        if open_tasks:
            priority = min((task.priority for task in open_tasks), key=rank)
        elif insight is not None:
            priority = insight.user_priority or insight.priority
        else:
            priority = None  # not organized yet

        dated = []
        for task in open_tasks:
            due, due_time = _task_due(task)
            if due is not None:
                dated.append((due, due_time or END_OF_DAY, due_time, task))
        deadline = None
        if dated:
            due, _, due_time, task = min(dated, key=lambda entry: entry[:2])
            snoozed = None
            if task.snoozed_until:
                try:
                    snoozed = date_cls.fromisoformat(task.snoozed_until)
                except ValueError:
                    snoozed = None
            deadline = {
                "date": due.isoformat(),
                "time": (due_time or END_OF_DAY).strftime("%H:%M"),
                "all_day": due_time is None,
                "label": format_deadline(due, due_time),
                "status": classify_attention_bucket(
                    due,
                    reference=now.date(),
                    snoozed_until=snoozed,
                    due_time=due_time,
                    now=now,
                ),
                "task": task.text,
            }

        item["priority"] = priority
        item["deadline"] = deadline
        if open_tasks:
            item["task_status"] = "open"
        elif any(task.status == TASK_COMPLETED for task in tasks):
            item["task_status"] = "done"
        else:
            item["task_status"] = None


def add_item_to_db(raw_text: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = InboxItemRecord(
            item_type=TEXT_ITEM_TYPE,
            raw_text=raw_text,
            processing_status=READY_STATUS,
            owner_user_id=owner_id,
            created_at=now,
        )
        session.add(item)
        session.commit()
        return _item_to_dict(item)


def list_items_from_db(
    search: Optional[str] = None,
    item_type: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    processing_status: Optional[str] = None,
    limit: Optional[int] = 100,
    offset: int = 0,
    sort: str = "oldest",
) -> list[dict]:
    """List inbox items; ``sort`` is "oldest" (default) or "newest" first."""
    owner_id = require_owner_id()
    with SessionLocal() as session:
        statement = select(InboxItemRecord).where(
            InboxItemRecord.owner_user_id == owner_id
        )

        if category or priority:
            statement = statement.join(
                ItemInsightRecord,
                ItemInsightRecord.item_id == InboxItemRecord.id,
            )

        search_text = (search or "").strip()
        if search_text:
            statement = statement.where(
                or_(
                    InboxItemRecord.raw_text.icontains(
                        search_text,
                        autoescape=True,
                    ),
                    InboxItemRecord.title.icontains(
                        search_text,
                        autoescape=True,
                    ),
                    InboxItemRecord.extracted_text.icontains(
                        search_text,
                        autoescape=True,
                    ),
                    InboxItemRecord.original_filename.icontains(
                        search_text,
                        autoescape=True,
                    ),
                )
            )
        if item_type:
            statement = statement.where(
                InboxItemRecord.item_type == item_type
            )
        if category:
            statement = statement.where(
                ItemInsightRecord.category == category
            )
        if priority:
            statement = statement.where(
                ItemInsightRecord.priority == priority
            )
        if processing_status:
            statement = statement.where(
                InboxItemRecord.processing_status == processing_status
            )

        ordering = (
            InboxItemRecord.id.desc()
            if sort == "newest"
            else InboxItemRecord.id.asc()
        )
        statement = statement.order_by(ordering).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        items = session.scalars(statement).all()
        results = [_item_to_dict(item) for item in items]
        _add_task_details(session, results, owner_id)
        return results


def compute_inbox_fingerprint(session=None) -> str:
    """Stable hash of current inbox content used to detect stale analysis."""
    owner_id = require_owner_id()

    def _fingerprint(active_session) -> str:
        items = active_session.scalars(
            select(InboxItemRecord)
            .where(InboxItemRecord.owner_user_id == owner_id)
            .order_by(InboxItemRecord.id.asc())
        ).all()
        payload = [
            [
                item.id,
                item.item_type,
                item.raw_text or "",
                item.title or "",
                item.source_url or "",
                item.processing_status,
                item.updated_at or item.created_at,
            ]
            for item in items
        ]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    if session is not None:
        return _fingerprint(session)
    with SessionLocal() as owned_session:
        return _fingerprint(owned_session)


def update_item_in_db(item_id: int, raw_text: str) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if (
            item is None
            or item.item_type != TEXT_ITEM_TYPE
            or item.owner_user_id != owner_id
        ):
            return None

        item.raw_text = raw_text
        item.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _item_to_dict(item)


def _remove_orphan_tasks(session, item_ids: list[int]) -> None:
    """Drop source links for deleted items and remove tasks with no sources."""
    if not item_ids:
        return

    affected_task_ids = session.scalars(
        select(TaskSourceItemRecord.task_id).where(
            TaskSourceItemRecord.item_id.in_(item_ids)
        )
    ).all()
    for link in session.scalars(
        select(TaskSourceItemRecord).where(
            TaskSourceItemRecord.item_id.in_(item_ids)
        )
    ).all():
        session.delete(link)
    session.flush()

    for task_id in sorted(set(affected_task_ids)):
        remaining = session.scalars(
            select(TaskSourceItemRecord.item_id).where(
                TaskSourceItemRecord.task_id == task_id
            )
        ).all()
        if remaining:
            task = session.get(TaskRecord, task_id)
            if task is not None:
                task.dedup_key = _task_dedup_key(list(remaining), task.text)
            continue
        task = session.get(TaskRecord, task_id)
        if task is not None:
            session.delete(task)


def delete_item_from_db(item_id: int) -> bool:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return False

        insight = session.get(ItemInsightRecord, item_id)
        if insight is not None:
            session.delete(insight)
        _remove_orphan_tasks(session, [item_id])
        storage_path = item.storage_path
        session.delete(item)
        session.commit()
        delete_stored_file(storage_path)
        return True


def _item_to_note(item: dict) -> dict:
    return {
        "id": item["id"],
        "text": item["raw_text"],
        "created_at": item["created_at"],
    }


def add_note_to_db(text: str) -> dict:
    return _item_to_note(add_item_to_db(text))


def list_notes_from_db() -> list[dict]:
    """Analyzable ready items exposed in the legacy note shape for the organizer."""
    notes = []
    for item in list_items_from_db(limit=None, offset=0):
        if item["item_type"] not in ANALYZABLE_TYPES:
            continue
        if item["processing_status"] not in ANALYZABLE_STATUSES:
            continue
        text = item.get("extracted_text") or item.get("raw_text")
        if not text:
            continue
        notes.append(
            {
                "id": item["id"],
                "text": text,
                "created_at": item["created_at"],
                "is_sensitive": item["is_sensitive"],
            }
        )
    return notes


def list_pending_notes_from_db() -> list[dict]:
    """Analyzable notes that still need AI analysis.

    A note is pending when it has never been analyzed, or when its content
    was edited after its latest analysis.
    """
    notes = list_notes_from_db()
    if not notes:
        return []
    note_ids = [note["id"] for note in notes]
    with SessionLocal() as session:
        analyzed_at = {
            insight.item_id: insight.updated_at
            for insight in session.scalars(
                select(ItemInsightRecord).where(
                    ItemInsightRecord.item_id.in_(note_ids)
                )
            ).all()
        }
        edited_at = {
            item.id: item.updated_at
            for item in session.scalars(
                select(InboxItemRecord).where(InboxItemRecord.id.in_(note_ids))
            ).all()
        }
    pending = []
    for note in notes:
        insight_time = analyzed_at.get(note["id"])
        if insight_time is None:
            pending.append(note)
            continue
        edit_time = edited_at.get(note["id"])
        if edit_time and edit_time > insight_time:
            pending.append(note)
    return pending


def merge_analysis_results(
    previous: Optional[dict],
    new: dict,
    existing_item_ids: set[int],
) -> dict:
    """Combine a new-notes-only analysis with the previously saved view.

    Previous entries survive unless their item was re-analyzed in ``new`` or
    no longer exists. ToDos survive only when every source item survives.
    """
    if previous is None:
        return new

    new_ids = {
        note["id"] for bucket in new["buckets"] for note in bucket["notes"]
    }
    keep_ids = {
        note["id"]
        for bucket in previous["buckets"]
        for note in bucket["notes"]
        if note["id"] in existing_item_ids and note["id"] not in new_ids
    }

    buckets: dict[str, list[dict]] = {}
    for bucket in previous["buckets"]:
        for note in bucket["notes"]:
            if note["id"] in keep_ids:
                buckets.setdefault(bucket["name"], []).append(note)
    for bucket in new["buckets"]:
        buckets.setdefault(bucket["name"], []).extend(bucket["notes"])

    todos = [
        todo
        for todo in previous["todos"]
        if set(todo["source_note_ids"]) <= keep_ids
    ] + list(new["todos"])

    priority_view = {
        level: [
            entry
            for entry in previous["priority_view"][level]
            if entry["id"] in keep_ids
        ]
        + list(new["priority_view"][level])
        for level in ("High", "Medium", "Low")
    }

    merged = {
        "buckets": [
            {"name": name, "notes": notes} for name, notes in buckets.items()
        ],
        "todos": todos,
        "priority_view": priority_view,
        "mode": new["mode"] if new["buckets"] else previous["mode"],
    }
    return AnalysisResult.model_validate(merged).model_dump()


def update_note_in_db(note_id: int, text: str) -> Optional[dict]:
    item = update_item_in_db(note_id, text)
    return _item_to_note(item) if item is not None else None


def delete_note_from_db(note_id: int) -> bool:
    return delete_item_from_db(note_id)


def start_analysis_run() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    owner_id = require_owner_id()
    with SessionLocal() as session:
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=10)
        running = session.scalars(
            select(AnalysisRunRecord).where(
                AnalysisRunRecord.owner_user_id == owner_id,
                AnalysisRunRecord.status == ANALYSIS_RUNNING,
            )
        ).all()
        for existing in running:
            if datetime.fromisoformat(existing.started_at) < stale_before:
                # A crashed run must not block the user forever.
                existing.status = ANALYSIS_FAILED
                existing.mode = "error"
                existing.completed_at = started_at
            else:
                raise AnalysisAlreadyRunningError(
                    "An analysis is already running."
                )
        run = AnalysisRunRecord(
            started_at=started_at,
            status=ANALYSIS_RUNNING,
            mode="pending",
            owner_user_id=owner_id,
        )
        session.add(run)
        session.commit()
        return run.id


def _topic_id_by_name(session, name: str) -> Optional[int]:
    owner_id = require_owner_id()
    ensure_default_topics(session, owner_user_id=owner_id)
    topic = session.scalars(
        select(TopicRecord).where(
            TopicRecord.name == name,
            TopicRecord.owner_user_id == owner_id,
        )
    ).first()
    if topic is not None:
        return topic.id
    now = datetime.now(timezone.utc).isoformat()
    topic = TopicRecord(
        name=name,
        description="Created from AI organization",
        owner_user_id=owner_id,
        created_at=now,
        updated_at=now,
        archived=0,
    )
    session.add(topic)
    session.flush()
    return topic.id


def _normalize_task_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def complete_analysis_run(
    run_id: int,
    result: dict,
    previous: Optional[dict] = None,
) -> dict:
    """Persist a run's insights/tasks and store the displayed analysis.

    ``result`` covers only the items analyzed in this run. When ``previous``
    (the last saved analysis) is given, the stored/returned view merges the
    new results into it so earlier items stay visible.
    """
    completed_at = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        run = session.get(AnalysisRunRecord, run_id)
        owner_id = require_owner_id()
        if (
            run is None
            or run.status != ANALYSIS_RUNNING
            or run.owner_user_id != owner_id
        ):
            raise ValueError("Analysis run is not available for completion")

        ensure_default_topics(session, owner_user_id=owner_id)

        for bucket in result["buckets"]:
            topic_id = _topic_id_by_name(session, bucket["name"])
            for analyzed_item in bucket["notes"]:
                item_id = analyzed_item["id"]
                if _get_owned_item(session, item_id) is None:
                    raise ValueError(
                        f"Analysis references missing item {item_id}"
                    )
                insight = session.get(ItemInsightRecord, item_id)
                if insight is None:
                    insight = ItemInsightRecord(
                        item_id=item_id,
                        needs_review=0,
                    )
                    session.add(insight)
                insight.analysis_run_id = run_id
                # AI fields always refresh; user overrides are preserved.
                insight.category = bucket["name"]
                insight.concise_title = analyzed_item["title"]
                insight.priority = analyzed_item["priority"]
                insight.topic_id = topic_id
                tags = analyzed_item.get("tags") or []
                insight.tags_json = json.dumps(tags, ensure_ascii=False)
                insight.rationale = analyzed_item.get("rationale") or (
                    f"Grouped under “{bucket['name']}” based on the note content."
                )
                insight.needs_review = 0
                insight.updated_at = completed_at
                item = _get_owned_item(session, item_id)
                if item is not None and insight.user_title is None:
                    item.title = analyzed_item["title"]

        for todo in result["todos"]:
            source_item_ids = sorted(set(todo["source_note_ids"]))
            if not source_item_ids:
                continue
            for item_id in source_item_ids:
                if _get_owned_item(session, item_id) is None:
                    raise ValueError(
                        f"Task references missing item {item_id}"
                    )

            dedup_key = _task_dedup_key(source_item_ids, todo["task"])
            task = session.scalars(
                select(TaskRecord).where(TaskRecord.dedup_key == dedup_key)
            ).first()

            if task is None:
                # Durable match: same sources + similar normalized wording.
                candidates = session.scalars(
                    select(TaskRecord)
                    .join(TaskSourceItemRecord)
                    .where(TaskSourceItemRecord.item_id.in_(source_item_ids))
                    .distinct()
                    .order_by(TaskRecord.id.asc())
                ).all()
                target_norm = _normalize_task_text(todo["task"])
                for candidate in candidates:
                    candidate_sources = set(
                        session.scalars(
                            select(TaskSourceItemRecord.item_id).where(
                                TaskSourceItemRecord.task_id == candidate.id
                            )
                        ).all()
                    )
                    if candidate_sources != set(source_item_ids):
                        continue
                    if _normalize_task_text(candidate.text) == target_norm:
                        task = candidate
                        break
                    # Soft wording drift still maps to the same durable task.
                    left = set(target_norm.split())
                    right = set(_normalize_task_text(candidate.text).split())
                    if left and right and len(left & right) / len(left | right) >= 0.6:
                        task = candidate
                        break

            if task is None:
                from app.attention.dates import parse_timing_phrase

                parsed_timing = parse_timing_phrase(todo.get("when"))
                task = TaskRecord(
                    text=todo["task"],
                    priority=todo["priority"],
                    timing_text=todo["when"],
                    ai_timing_text=todo["when"],
                    timing_confirmed=0,
                    due_on=parsed_timing.normalized_iso,
                    due_confidence=parsed_timing.confidence,
                    status=TASK_OPEN,
                    dedup_key=dedup_key,
                    owner_user_id=require_owner_id(),
                    created_at=completed_at,
                    updated_at=completed_at,
                )
                session.add(task)
                session.flush()
                for item_id in source_item_ids:
                    session.add(
                        TaskSourceItemRecord(
                            task_id=task.id,
                            item_id=item_id,
                        )
                    )
            else:
                from app.attention.dates import parse_timing_phrase

                task.text = todo["task"]
                task.priority = todo["priority"]
                task.ai_timing_text = todo["when"]
                if not task.timing_confirmed:
                    task.timing_text = todo["when"]
                    parsed_timing = parse_timing_phrase(todo.get("when"))
                    task.due_on = parsed_timing.normalized_iso
                    task.due_confidence = parsed_timing.confidence
                task.dedup_key = dedup_key
                task.updated_at = completed_at
                linked = set(
                    session.scalars(
                        select(TaskSourceItemRecord.item_id).where(
                            TaskSourceItemRecord.task_id == task.id
                        )
                    ).all()
                )
                for item_id in source_item_ids:
                    if item_id not in linked:
                        session.add(
                            TaskSourceItemRecord(
                                task_id=task.id,
                                item_id=item_id,
                            )
                        )

        # Refresh deterministic relations for current inbox text.
        items = [
            _item_to_dict(item)
            for item in session.scalars(select(InboxItemRecord)).all()
        ]
        for relation in session.scalars(select(ItemRelationRecord)).all():
            if not relation.dismissed:
                session.delete(relation)
        session.flush()
        for relation in find_item_relations(items):
            existing = session.scalars(
                select(ItemRelationRecord).where(
                    ItemRelationRecord.item_id_a == relation["item_id_a"],
                    ItemRelationRecord.item_id_b == relation["item_id_b"],
                    ItemRelationRecord.relation_type
                    == relation["relation_type"],
                )
            ).first()
            if existing is not None:
                if existing.dismissed:
                    continue
                existing.score = relation["score"]
                continue
            session.add(
                ItemRelationRecord(
                    item_id_a=relation["item_id_a"],
                    item_id_b=relation["item_id_b"],
                    relation_type=relation["relation_type"],
                    score=relation["score"],
                    created_at=completed_at,
                    dismissed=0,
                )
            )

        existing_ids = set(
            session.scalars(
                select(InboxItemRecord.id).where(
                    InboxItemRecord.owner_user_id == require_owner_id()
                )
            ).all()
        )
        displayed = merge_analysis_results(previous, result, existing_ids)
        analyzed_item_ids = sorted(
            note["id"]
            for bucket in result["buckets"]
            for note in bucket["notes"]
        )

        fingerprint = compute_inbox_fingerprint(session)
        persisted = {
            **displayed,
            "analyzed_item_ids": analyzed_item_ids,
            "inbox_fingerprint": fingerprint,
        }
        run.completed_at = completed_at
        run.status = ANALYSIS_SUCCEEDED
        run.mode = result["mode"]
        run.result_json = json.dumps(persisted, ensure_ascii=False)
        session.commit()
        return {
            **displayed,
            "analyzed_item_ids": analyzed_item_ids,
            "analysis_run_id": run_id,
            "generated_at": completed_at,
            "is_stale": False,
            "inbox_fingerprint": fingerprint,
        }


def fail_analysis_run(run_id: int) -> None:
    completed_at = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        run = session.get(AnalysisRunRecord, run_id)
        if (
            run is None
            or run.status != ANALYSIS_RUNNING
            or run.owner_user_id != require_owner_id()
        ):
            return
        run.completed_at = completed_at
        run.status = ANALYSIS_FAILED
        run.mode = "error"
        run.result_json = None
        session.commit()


def get_latest_successful_analysis() -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        run = session.scalars(
            select(AnalysisRunRecord)
            .where(
                AnalysisRunRecord.status == ANALYSIS_SUCCEEDED,
                AnalysisRunRecord.owner_user_id == owner_id,
            )
            .order_by(
                AnalysisRunRecord.completed_at.desc(),
                AnalysisRunRecord.id.desc(),
            )
            .limit(1)
        ).first()
        if run is None:
            return None
        stored = json.loads(run.result_json)
        fingerprint = stored.pop("inbox_fingerprint", None)
        current = compute_inbox_fingerprint(session)
        return {
            **stored,
            "analysis_run_id": run.id,
            "generated_at": run.completed_at,
            "is_stale": fingerprint != current,
            "inbox_fingerprint": fingerprint,
        }


def _task_dedup_key(source_item_ids: list[int], task_text: str = "") -> str:
    fingerprint = json.dumps(
        {
            "sources": sorted(set(source_item_ids)),
            "task": _normalize_task_text(task_text),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _ensure_task_due_fields(task: TaskRecord) -> None:
    """Normalize due_on from timing_text when missing."""
    from app.attention.dates import parse_timing_phrase

    if task.due_on and task.due_confidence:
        return
    parsed = parse_timing_phrase(task.timing_text)
    task.due_on = parsed.normalized_iso
    task.due_confidence = parsed.confidence


def _task_to_dict(session, task: TaskRecord) -> dict:
    _ensure_task_due_fields(task)
    source_item_ids = session.scalars(
        select(TaskSourceItemRecord.item_id)
        .where(TaskSourceItemRecord.task_id == task.id)
        .order_by(TaskSourceItemRecord.item_id.asc())
    ).all()
    return {
        "id": task.id,
        "text": task.text,
        "priority": task.priority,
        "timing_text": task.timing_text,
        "ai_timing_text": task.ai_timing_text,
        "timing_confirmed": bool(task.timing_confirmed),
        "due_on": task.due_on,
        "due_confidence": task.due_confidence,
        "snoozed_until": task.snoozed_until,
        "reminder_at": task.reminder_at,
        "status": task.status,
        "source_item_ids": list(source_item_ids),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def list_tasks_from_db(status_filter: str = TASK_OPEN) -> list[dict]:
    if status_filter not in TASK_STATUSES | {"all"}:
        raise ValueError("Unsupported task status")

    owner_id = require_owner_id()
    with SessionLocal() as session:
        statement = (
            select(TaskRecord)
            .where(TaskRecord.owner_user_id == owner_id)
            .order_by(TaskRecord.id.asc())
        )
        if status_filter != "all":
            statement = statement.where(TaskRecord.status == status_filter)
        tasks = session.scalars(statement).all()
        return [_task_to_dict(session, task) for task in tasks]


def get_task_by_id(task_id: int) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None or task.owner_user_id != owner_id:
            return None
        return _task_to_dict(session, task)


def set_task_status(task_id: int, task_status: str) -> Optional[dict]:
    if task_status not in TASK_STATUSES:
        raise ValueError("Unsupported task status")

    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None or task.owner_user_id != owner_id:
            return None
        if task.status != task_status:
            task.status = task_status
            task.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
        return _task_to_dict(session, task)


def add_file_item_to_db(
    item_type: str,
    original_filename: str,
    storage_path: str,
    mime_type: str,
) -> dict:
    """Persist uploaded file item metadata before background extraction."""
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        item = InboxItemRecord(
            item_type=item_type,
            original_filename=original_filename,
            storage_path=storage_path,
            mime_type=mime_type,
            processing_status=STATUS_CAPTURED,
            owner_user_id=require_owner_id(),
            created_at=now,
        )
        session.add(item)
        session.commit()
        return _item_to_dict(item)


def add_pdf_item_to_db(original_filename: str, storage_path: str, mime_type: str) -> dict:
    return add_file_item_to_db(PDF_ITEM_TYPE, original_filename, storage_path, mime_type)


def add_image_item_to_db(original_filename: str, storage_path: str, mime_type: str) -> dict:
    return add_file_item_to_db(IMAGE_ITEM_TYPE, original_filename, storage_path, mime_type)


def add_voice_item_to_db(original_filename: str, storage_path: str, mime_type: str) -> dict:
    return add_file_item_to_db(VOICE_ITEM_TYPE, original_filename, storage_path, mime_type)


def complete_item_processing(
    item_id: int,
    *,
    title: str,
    raw_text: str,
    extracted_text: str,
    mime_type: Optional[str] = None,
    ai_description: Optional[str] = None,
    final_url: Optional[str] = None,
) -> Optional[dict]:
    with SessionLocal() as session:
        item = _get_owned_item(session, item_id)
        if item is None:
            return None
        item.title = title
        item.raw_text = raw_text
        item.extracted_text = extracted_text
        if mime_type is not None:
            item.mime_type = mime_type
        if ai_description is not None:
            item.ai_description = ai_description
        if final_url is not None:
            item.source_url = final_url
        item.processing_status = STATUS_READY
        item.error_message = None
        item.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _item_to_dict(item)


def complete_pdf_item_processing(
    item_id: int,
    *,
    title: str,
    raw_text: str,
    extracted_text: str,
    mime_type: Optional[str],
) -> Optional[dict]:
    return complete_item_processing(
        item_id,
        title=title,
        raw_text=raw_text,
        extracted_text=extracted_text,
        mime_type=mime_type,
    )


def add_image_item_to_db(
    original_filename: str,
    storage_path: str,
    mime_type: str,
) -> dict:
    return add_file_item_to_db(IMAGE_ITEM_TYPE, original_filename, storage_path, mime_type)


def complete_image_item_processing(
    item_id: int,
    *,
    title: str,
    raw_text: str,
    extracted_text: str,
    ai_description: str,
    mime_type: Optional[str],
) -> Optional[dict]:
    return complete_item_processing(
        item_id,
        title=title,
        raw_text=raw_text,
        extracted_text=extracted_text,
        ai_description=ai_description,
        mime_type=mime_type,
    )


def add_voice_item_to_db(
    original_filename: str,
    storage_path: str,
    mime_type: str,
) -> dict:
    return add_file_item_to_db(VOICE_ITEM_TYPE, original_filename, storage_path, mime_type)


def complete_voice_item_processing(
    item_id: int,
    *,
    title: str,
    raw_text: str,
    extracted_text: str,
    mime_type: Optional[str],
) -> Optional[dict]:
    return complete_item_processing(
        item_id,
        title=title,
        raw_text=raw_text,
        extracted_text=extracted_text,
        mime_type=mime_type,
    )


def update_item_extracted_text(
    item_id: int,
    extracted_text: str,
) -> Optional[dict]:
    """User edit of extracted/OCR/transcript text (facts), preserving AI description."""
    cleaned = extracted_text.strip()
    with SessionLocal() as session:
        item = _get_owned_item(session, item_id)
        if item is None:
            return None
        if item.item_type not in {IMAGE_ITEM_TYPE, VOICE_ITEM_TYPE, PDF_ITEM_TYPE}:
            return None
        item.extracted_text = cleaned
        if item.item_type == IMAGE_ITEM_TYPE and item.ai_description:
            item.raw_text = (
                f"{cleaned}\n\n[AI description — inferred]\n{item.ai_description}"
                if cleaned
                else f"[AI description — inferred]\n{item.ai_description}"
            )
        else:
            item.raw_text = cleaned
        item.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _item_to_dict(item)


def add_url_item_to_db(url: str) -> dict:
    """Persist URL first (captured), then return the row for processing."""
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        item = InboxItemRecord(
            item_type=URL_ITEM_TYPE,
            source_url=url,
            processing_status=STATUS_CAPTURED,
            owner_user_id=require_owner_id(),
            created_at=now,
        )
        session.add(item)
        session.commit()
        return _item_to_dict(item)


def mark_item_processing(item_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        item = _get_owned_item(session, item_id)
        if item is None:
            return None
        item.processing_status = STATUS_PROCESSING
        item.error_message = None
        item.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _item_to_dict(item)


def complete_url_item_processing(
    item_id: int,
    *,
    title: str,
    raw_text: str,
    mime_type: Optional[str],
    final_url: Optional[str] = None,
) -> Optional[dict]:
    return complete_item_processing(
        item_id,
        title=title,
        raw_text=raw_text,
        extracted_text=raw_text,
        mime_type=mime_type,
        final_url=final_url,
    )


def fail_item_processing(item_id: int, error_message: str) -> Optional[dict]:
    with SessionLocal() as session:
        item = _get_owned_item(session, item_id)
        if item is None:
            return None
        item.processing_status = STATUS_FAILED
        item.error_message = error_message[:1000]
        item.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _item_to_dict(item)


def get_item_from_db(item_id: int) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return None
        return _item_to_dict(item)


def confirm_task_timing(
    task_id: int,
    timing_text: Optional[str] = None,
) -> Optional[dict]:
    from app.attention.dates import parse_timing_phrase

    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None or task.owner_user_id != owner_id:
            return None
        if timing_text is not None:
            task.timing_text = timing_text
        parsed = parse_timing_phrase(task.timing_text)
        task.due_on = parsed.normalized_iso
        task.due_confidence = "high" if parsed.due_date else parsed.confidence
        task.timing_confirmed = 1
        task.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _task_to_dict(session, task)


def snooze_task_in_db(task_id: int, until_iso_date: str) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None or task.owner_user_id != owner_id:
            return None
        task.snoozed_until = until_iso_date
        task.reminder_at = until_iso_date
        task.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _task_to_dict(session, task)


def set_task_reminder_in_db(task_id: int, reminder_iso_date: str) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        task = session.get(TaskRecord, task_id)
        if task is None or task.owner_user_id != owner_id:
            return None
        task.reminder_at = reminder_iso_date
        task.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _task_to_dict(session, task)


def get_attention_board() -> dict:
    from app.attention.dates import (
        classify_attention_bucket,
        now_in_configured_tz,
        parse_timing_phrase,
    )
    from datetime import date as date_cls

    now = now_in_configured_tz()
    today = now.date()
    board = {
        "today": [],
        "upcoming": [],
        "overdue": [],
        "unscheduled": [],
        "snoozed": [],
        "needs_confirmation": [],
        "reference_date": today.isoformat(),
    }
    for task in list_tasks_from_db(status_filter=TASK_OPEN):
        parsed = parse_timing_phrase(task.get("timing_text"))
        due = None
        if task.get("due_on"):
            try:
                due = date_cls.fromisoformat(task["due_on"])
            except ValueError:
                due = parsed.due_date
        else:
            due = parsed.due_date
        snoozed = None
        if task.get("snoozed_until"):
            try:
                snoozed = date_cls.fromisoformat(task["snoozed_until"])
            except ValueError:
                snoozed = None
        due_time = parsed.due_time if due is not None else None
        bucket = classify_attention_bucket(
            due,
            reference=today,
            snoozed_until=snoozed,
            due_time=due_time,
            now=now,
        )
        entry = {
            **task,
            "original_timing_phrase": task.get("timing_text"),
            "normalized_due_on": due.isoformat() if due else None,
            "due_time": due_time.strftime("%H:%M") if due_time else None,
            "due_confidence": task.get("due_confidence") or parsed.confidence,
            # Dates are taken as read when the note is saved; the user is
            # never asked to confirm them.
            "needs_confirmation": False,
            "attention_bucket": bucket,
            "parse_note": parsed.note,
        }
        board[bucket].append(entry)
    return board


PRIORITY_ORDER = ("High", "Medium", "Low")
URGENCY_ORDER = ("overdue", "today", "upcoming", "unscheduled", "snoozed")


def list_pending_tasks_by_priority(scope: str = "all") -> dict:
    """Open tasks for the bell: High first, then Medium, then Low.

    Within a priority, overdue tasks come first, then today's, upcoming
    (soonest first), undated, and snoozed ones. ``scope`` narrows the list:
    "due_soon" keeps overdue and due-today tasks, "high" keeps High priority.
    """
    board = get_attention_board()
    tasks = [task for bucket in URGENCY_ORDER for task in board[bucket]]
    if scope == "due_soon":
        tasks = [t for t in tasks if t["attention_bucket"] in ("overdue", "today")]
    elif scope == "high":
        tasks = [t for t in tasks if t.get("priority") == "High"]
    tasks.sort(
        key=lambda task: (
            PRIORITY_ORDER.index(task["priority"])
            if task.get("priority") in PRIORITY_ORDER
            else len(PRIORITY_ORDER),
            URGENCY_ORDER.index(task["attention_bucket"]),
            task.get("normalized_due_on") or "9999-12-31",
            task.get("due_time") or "99:99",
            task["id"],
        )
    )
    return {
        "reference_date": board["reference_date"],
        "scope": scope,
        "count": len(tasks),
        "overdue_count": sum(1 for t in tasks if t["attention_bucket"] == "overdue"),
        "tasks": [
            {
                "id": task["id"],
                "text": task["text"],
                "priority": task["priority"],
                "urgency": task["attention_bucket"],
                "due_on": task.get("normalized_due_on"),
                "due_time": task.get("due_time"),
                **_bell_deadline_labels(task),
                "timing_text": task.get("timing_text"),
                "snoozed_until": task.get("snoozed_until"),
            }
            for task in tasks
        ],
    }


def _bell_deadline_labels(task: dict) -> dict:
    """"Fri, 13 Nov 2026, 3:00 PM IST" and "3:00 PM IST" for a bell entry."""
    from datetime import date as date_cls
    from datetime import time as time_cls

    from app.attention.dates import END_OF_DAY, format_clock, format_deadline, timezone_label

    if not task.get("normalized_due_on"):
        return {"deadline_label": None, "time_label": None}
    due = date_cls.fromisoformat(task["normalized_due_on"])
    due_time = time_cls.fromisoformat(task["due_time"]) if task.get("due_time") else None
    return {
        "deadline_label": format_deadline(due, due_time),
        "time_label": f"{format_clock(due_time or END_OF_DAY)} {timezone_label()}",
    }


def get_daily_briefing() -> dict:
    board = get_attention_board()
    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    focus = sorted(
        board["overdue"] + board["today"],
        key=lambda item: (
            priority_rank.get(item.get("priority"), 9),
            item.get("normalized_due_on") or "9999",
            item.get("due_time") or "99:99",
        ),
    )
    suggestions = []
    for item in focus[:5]:
        reason = []
        if item["attention_bucket"] == "overdue":
            if (
                item.get("due_time")
                and item.get("normalized_due_on") == board["reference_date"]
            ):
                reason.append(f"overdue since {item['due_time']}")
            else:
                reason.append("overdue")
        if item["attention_bucket"] == "today":
            reason.append("due today")
        if item.get("priority") == "High":
            reason.append("high priority")
        suggestions.append(
            {
                "task_id": item["id"],
                "text": item["text"],
                "priority": item["priority"],
                "reason": ", ".join(reason) or "on your attention list",
            }
        )
    return {
        "reference_date": board["reference_date"],
        "counts": {
            "overdue": len(board["overdue"]),
            "today": len(board["today"]),
            "upcoming": len(board["upcoming"]),
            "unscheduled": len(board["unscheduled"]),
            "snoozed": len(board["snoozed"]),
            "needs_confirmation": len(board["needs_confirmation"]),
        },
        "focus": focus[:10],
        "priority_suggestions": suggestions,
        "summary": (
            f"{len(board['overdue'])} overdue, {len(board['today'])} due today, "
            f"{len(board['upcoming'])} upcoming."
        ),
    }


def save_calendar_receipt(
    *,
    draft_id: str,
    task_id: int,
    provider: str,
    status: str,
    payload: dict,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        receipt = CalendarActionReceiptRecord(
            draft_id=draft_id,
            task_id=task_id,
            provider=provider,
            status=status,
            payload_json=json.dumps(payload),
            created_at=now,
        )
        session.add(receipt)
        session.commit()
        return {
            "id": receipt.id,
            "draft_id": receipt.draft_id,
            "task_id": receipt.task_id,
            "provider": receipt.provider,
            "status": receipt.status,
            "payload": payload,
            "created_at": receipt.created_at,
        }


# --- Google Calendar connection ------------------------------------------------

CALENDAR_CONNECTED = "connected"
CALENDAR_NEEDS_RECONNECT = "needs_reconnect"
_CALENDAR_UPDATABLE = {
    "access_token_enc",
    "access_token_expires_at",
    "status",
    "last_synced_at",
    "last_error",
}


def _calendar_connection_to_dict(record: CalendarConnectionRecord) -> dict:
    return {
        "user_id": record.user_id,
        "google_email": record.google_email,
        "scopes": record.scopes,
        "refresh_token_enc": record.refresh_token_enc,
        "access_token_enc": record.access_token_enc,
        "access_token_expires_at": record.access_token_expires_at,
        "calendar_id": record.calendar_id,
        "status": record.status,
        "last_synced_at": record.last_synced_at,
        "last_error": record.last_error,
        "connected_at": record.connected_at,
    }


def _calendar_record(session, user_id: int) -> Optional[CalendarConnectionRecord]:
    return session.scalar(
        select(CalendarConnectionRecord).where(
            CalendarConnectionRecord.user_id == user_id
        )
    )


def get_calendar_connection(user_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        record = _calendar_record(session, user_id)
        return _calendar_connection_to_dict(record) if record else None


def calendar_sync_wanted(user_id: int) -> bool:
    """True when this person's tasks should be kept on their calendar."""
    with SessionLocal() as session:
        record = _calendar_record(session, user_id)
        return record is not None and record.status == CALENDAR_CONNECTED


def save_calendar_connection(
    user_id: int,
    *,
    google_email: Optional[str],
    scopes: str,
    refresh_token_enc: str,
    access_token_enc: str,
    access_token_expires_at: str,
) -> dict:
    """Store a new (or renewed) connection.

    Reconnecting the same Google account keeps its Life Inbox calendar and
    events. Connecting a different account starts afresh, because the old
    calendar belongs to the other account.
    """
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        record = _calendar_record(session, user_id)
        if record is None or (record.google_email or "") != (google_email or ""):
            session.execute(
                delete(CalendarTaskEventRecord).where(
                    CalendarTaskEventRecord.user_id == user_id
                )
            )
        if record is None:
            record = CalendarConnectionRecord(user_id=user_id, connected_at=now)
            session.add(record)
        elif (record.google_email or "") != (google_email or ""):
            record.calendar_id = None
            record.last_synced_at = None
            record.connected_at = now
        record.google_email = google_email
        record.scopes = scopes
        record.refresh_token_enc = refresh_token_enc
        record.access_token_enc = access_token_enc
        record.access_token_expires_at = access_token_expires_at
        record.status = CALENDAR_CONNECTED
        record.last_error = None
        record.updated_at = now
        session.commit()
        return _calendar_connection_to_dict(record)


def update_calendar_connection(user_id: int, **fields) -> None:
    unknown = set(fields) - _CALENDAR_UPDATABLE
    if unknown:
        raise ValueError(f"Cannot update {sorted(unknown)}")
    with SessionLocal() as session:
        record = _calendar_record(session, user_id)
        if record is None:
            return
        for name, value in fields.items():
            setattr(record, name, value)
        record.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()


def replace_calendar_id(user_id: int, calendar_id: str) -> None:
    """Point at a (new) Life Inbox calendar; events on the old one are forgotten."""
    with SessionLocal() as session:
        record = _calendar_record(session, user_id)
        if record is None:
            return
        record.calendar_id = calendar_id
        record.updated_at = datetime.now(timezone.utc).isoformat()
        session.execute(
            delete(CalendarTaskEventRecord).where(
                CalendarTaskEventRecord.user_id == user_id
            )
        )
        session.commit()


def delete_calendar_connection(user_id: int) -> bool:
    with SessionLocal() as session:
        session.execute(
            delete(CalendarTaskEventRecord).where(
                CalendarTaskEventRecord.user_id == user_id
            )
        )
        result = session.execute(
            delete(CalendarConnectionRecord).where(
                CalendarConnectionRecord.user_id == user_id
            )
        )
        session.commit()
        return bool(result.rowcount)


def list_calendar_task_events(user_id: int) -> dict[int, dict]:
    """{task_id: {"event_id", "fingerprint"}} for every event Life Inbox made."""
    with SessionLocal() as session:
        rows = session.scalars(
            select(CalendarTaskEventRecord).where(
                CalendarTaskEventRecord.user_id == user_id
            )
        ).all()
        return {
            row.task_id: {"event_id": row.event_id, "fingerprint": row.fingerprint}
            for row in rows
        }


def count_calendar_task_events(user_id: int) -> int:
    with SessionLocal() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(CalendarTaskEventRecord)
                .where(CalendarTaskEventRecord.user_id == user_id)
            )
            or 0
        )


def save_calendar_task_event(
    user_id: int, task_id: int, event_id: str, fingerprint: str
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        row = session.scalar(
            select(CalendarTaskEventRecord).where(
                CalendarTaskEventRecord.user_id == user_id,
                CalendarTaskEventRecord.task_id == task_id,
            )
        )
        if row is None:
            row = CalendarTaskEventRecord(user_id=user_id, task_id=task_id)
            session.add(row)
        row.event_id = event_id
        row.fingerprint = fingerprint
        row.synced_at = now
        session.commit()


def delete_calendar_task_event(user_id: int, task_id: int) -> None:
    with SessionLocal() as session:
        session.execute(
            delete(CalendarTaskEventRecord).where(
                CalendarTaskEventRecord.user_id == user_id,
                CalendarTaskEventRecord.task_id == task_id,
            )
        )
        session.commit()


def _topic_to_dict(topic: TopicRecord, item_count: int = 0) -> dict:
    return {
        "id": topic.id,
        "name": topic.name,
        "description": topic.description,
        "archived": bool(topic.archived),
        "item_count": item_count,
        "created_at": topic.created_at,
        "updated_at": topic.updated_at,
    }


def list_topics_from_db(include_archived: bool = False) -> list[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        ensure_default_topics(session, owner_user_id=owner_id)
        session.commit()
        statement = (
            select(TopicRecord)
            .where(TopicRecord.owner_user_id == owner_id)
            .order_by(TopicRecord.name.asc())
        )
        if not include_archived:
            statement = statement.where(TopicRecord.archived == 0)
        topics = session.scalars(statement).all()
        counts = {}
        for insight in session.scalars(select(ItemInsightRecord)).all():
            effective_topic = (
                insight.user_topic_id
                if insight.user_topic_id is not None
                else insight.topic_id
            )
            if effective_topic is None:
                continue
            counts[effective_topic] = counts.get(effective_topic, 0) + 1
        return [
            _topic_to_dict(topic, counts.get(topic.id, 0)) for topic in topics
        ]


def create_topic_in_db(name: str, description: Optional[str] = None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    owner_id = require_owner_id()
    with SessionLocal() as session:
        ensure_default_topics(session, owner_user_id=owner_id)
        existing = session.scalars(
            select(TopicRecord).where(
                TopicRecord.name == name,
                TopicRecord.owner_user_id == owner_id,
            )
        ).first()
        if existing is not None:
            if existing.archived:
                existing.archived = 0
                existing.description = description
                existing.updated_at = now
                session.commit()
                return _topic_to_dict(existing)
            raise ValueError("Topic already exists")
        topic = TopicRecord(
            name=name,
            description=description,
            owner_user_id=owner_id,
            created_at=now,
            updated_at=now,
            archived=0,
        )
        session.add(topic)
        session.commit()
        return _topic_to_dict(topic)


def update_topic_in_db(
    topic_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    archived: Optional[bool] = None,
) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        topic = session.get(TopicRecord, topic_id)
        if topic is not None and topic.owner_user_id != owner_id:
            return None
        if topic is None:
            return None
        if name is not None:
            topic.name = name
        if description is not None:
            topic.description = description
        if archived is not None:
            topic.archived = 1 if archived else 0
        topic.updated_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _topic_to_dict(topic)


def _parse_tags(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(tag) for tag in value]


def _insight_to_dict(insight: ItemInsightRecord) -> dict:
    effective_title = insight.user_title or insight.concise_title
    effective_priority = insight.user_priority or insight.priority
    effective_topic_id = (
        insight.user_topic_id
        if insight.user_topic_id is not None
        else insight.topic_id
    )
    effective_tags = (
        _parse_tags(insight.user_tags_json)
        if insight.user_tags_json is not None
        else _parse_tags(insight.tags_json)
    )
    return {
        "item_id": insight.item_id,
        "analysis_run_id": insight.analysis_run_id,
        "category": insight.category,
        "concise_title": insight.concise_title,
        "priority": insight.priority,
        "topic_id": insight.topic_id,
        "tags": _parse_tags(insight.tags_json),
        "rationale": insight.rationale,
        "user_title": insight.user_title,
        "user_topic_id": insight.user_topic_id,
        "user_priority": insight.user_priority,
        "user_tags": (
            _parse_tags(insight.user_tags_json)
            if insight.user_tags_json is not None
            else None
        ),
        "effective_title": effective_title,
        "effective_priority": effective_priority,
        "effective_topic_id": effective_topic_id,
        "effective_tags": effective_tags,
        "needs_review": bool(insight.needs_review),
        "updated_at": insight.updated_at,
        "has_user_overrides": any(
            [
                insight.user_title is not None,
                insight.user_topic_id is not None,
                insight.user_priority is not None,
                insight.user_tags_json is not None,
            ]
        ),
    }


def get_item_insight(item_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        if _get_owned_item(session, item_id) is None:
            return None
        insight = session.get(ItemInsightRecord, item_id)
        if insight is None:
            return None
        return _insight_to_dict(insight)


def update_item_insight_overrides(
    item_id: int,
    user_title: Optional[str] = None,
    user_topic_id: Optional[int] = None,
    user_priority: Optional[str] = None,
    user_tags: Optional[list[str]] = None,
    clear_overrides: bool = False,
    needs_review: Optional[bool] = None,
) -> Optional[dict]:
    with SessionLocal() as session:
        if _get_owned_item(session, item_id) is None:
            return None
        insight = session.get(ItemInsightRecord, item_id)
        if insight is None:
            return None
        if clear_overrides:
            insight.user_title = None
            insight.user_topic_id = None
            insight.user_priority = None
            insight.user_tags_json = None
        else:
            if user_title is not None:
                insight.user_title = user_title
            if user_topic_id is not None:
                topic = session.get(TopicRecord, user_topic_id)
                if topic is None or topic.owner_user_id != require_owner_id():
                    raise ValueError("Topic not found")
                insight.user_topic_id = user_topic_id
            if user_priority is not None:
                insight.user_priority = user_priority
            if user_tags is not None:
                insight.user_tags_json = json.dumps(
                    user_tags, ensure_ascii=False
                )
        if needs_review is not None:
            insight.needs_review = 1 if needs_review else 0
        insight.updated_at = datetime.now(timezone.utc).isoformat()
        item = _get_owned_item(session, item_id)
        if item is not None and insight.user_title:
            item.title = insight.user_title
        session.commit()
        return _insight_to_dict(insight)


def list_relations_from_db(
    include_dismissed: bool = False,
) -> list[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        owned_ids = select(InboxItemRecord.id).where(
            InboxItemRecord.owner_user_id == owner_id
        )
        statement = (
            select(ItemRelationRecord)
            .where(
                ItemRelationRecord.item_id_a.in_(owned_ids),
                ItemRelationRecord.item_id_b.in_(owned_ids),
            )
            .order_by(ItemRelationRecord.id.asc())
        )
        if not include_dismissed:
            statement = statement.where(ItemRelationRecord.dismissed == 0)
        rows = session.scalars(statement).all()
        return [
            {
                "id": row.id,
                "item_id_a": row.item_id_a,
                "item_id_b": row.item_id_b,
                "relation_type": row.relation_type,
                "score": float(row.score),
                "dismissed": bool(row.dismissed),
                "created_at": row.created_at,
            }
            for row in rows
        ]


def dismiss_relation_in_db(relation_id: int) -> Optional[dict]:
    with SessionLocal() as session:
        row = session.get(ItemRelationRecord, relation_id)
        if row is None:
            return None
        if (
            _get_owned_item(session, row.item_id_a) is None
            or _get_owned_item(session, row.item_id_b) is None
        ):
            return None
        row.dismissed = 1
        session.commit()
        return {
            "id": row.id,
            "item_id_a": row.item_id_a,
            "item_id_b": row.item_id_b,
            "relation_type": row.relation_type,
            "score": float(row.score),
            "dismissed": True,
            "created_at": row.created_at,
        }


def get_organization_dashboard() -> dict:
    with SessionLocal() as session:
        owner_id = require_owner_id()
        ensure_default_topics(session, owner_user_id=owner_id)
        session.commit()
        topics = list_topics_from_db()
        insights = {
            insight.item_id: _insight_to_dict(insight)
            for insight in session.scalars(
                select(ItemInsightRecord)
                .join(
                    InboxItemRecord,
                    InboxItemRecord.id == ItemInsightRecord.item_id,
                )
                .where(InboxItemRecord.owner_user_id == owner_id)
            ).all()
        }
        items = [
            _item_to_dict(item)
            for item in session.scalars(
                select(InboxItemRecord)
                .where(InboxItemRecord.owner_user_id == owner_id)
                .order_by(InboxItemRecord.id.desc())
            ).all()
        ]
        topic_names = {topic["id"]: topic["name"] for topic in topics}
        pending_ids = {note["id"] for note in list_pending_notes_from_db()}
        high_priority = []
        needs_review = []
        titles: dict[int, str] = {}
        for item in items:
            insight = insights.get(item["id"])
            titles[item["id"]] = (
                (insight["effective_title"] if insight else None)
                or item["title"]
                or (item["raw_text"] or "").strip()[:80]
                or f"Item {item['id']}"
            )
            if insight is None:
                # Say what is actually going on with the note, in plain words.
                if item["id"] in pending_ids:
                    reason = "Waiting to be organized"
                elif item["processing_status"] == "failed":
                    reason = "Could not be processed. Retry or delete it"
                else:
                    reason = "Has no text to organize yet"
                needs_review.append({**item, "reason": reason})
                continue
            card = {
                **item,
                "effective_title": insight["effective_title"],
                "effective_priority": insight["effective_priority"],
                "effective_topic_id": insight["effective_topic_id"],
                "topic_name": topic_names.get(insight["effective_topic_id"]),
                "rationale": insight["rationale"],
                "has_user_overrides": insight["has_user_overrides"],
            }
            if insight["effective_priority"] == "High":
                high_priority.append(card)
            if insight["needs_review"]:
                needs_review.append({**card, "reason": "Marked for review"})
            elif item["id"] in pending_ids:
                needs_review.append(
                    {**card, "reason": "Edited, being organized again"}
                )

        latest = get_latest_successful_analysis()
        open_tasks = list_tasks_from_db(TASK_OPEN)
        # Strongest links first, and never a link to a note that was deleted.
        relations = [
            {
                **relation,
                "title_a": titles[relation["item_id_a"]],
                "title_b": titles[relation["item_id_b"]],
            }
            for relation in sorted(
                list_relations_from_db(),
                key=lambda relation: relation["score"],
                reverse=True,
            )
            if relation["item_id_a"] in titles
            and relation["item_id_b"] in titles
        ]
        return {
            "topics": topics,
            "item_count": len(items),
            "high_priority": high_priority[:20],
            "open_tasks": open_tasks[:20],
            "recently_captured": [
                {**item, "effective_title": titles[item["id"]]}
                for item in items[:10]
            ],
            "needs_review": needs_review[:20],
            "related_items": relations[:20],
            "analysis": latest,
            "pending_analysis_count": len(pending_ids),
        }


# ---------------------------------------------------------------------------
# Privacy: sensitive items and the AI call log
# ---------------------------------------------------------------------------


def set_item_sensitive(item_id: int, sensitive: bool) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return None
        item.is_sensitive = sensitive
        session.commit()
        return _item_to_dict(item)


def get_sensitive_item_ids() -> set:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        rows = session.execute(
            select(InboxItemRecord.id).where(
                InboxItemRecord.owner_user_id == owner_id,
                InboxItemRecord.is_sensitive.is_(True),
            )
        )
        return {row[0] for row in rows}


def add_ai_call_log(feature, model, status, chars_sent, redactions) -> None:
    with SessionLocal() as session:
        session.add(
            AiCallLogRecord(
                owner_user_id=get_current_owner_id(),
                created_at=datetime.now(timezone.utc).isoformat(),
                feature=feature,
                model=model,
                status=status,
                chars_sent=chars_sent,
                redactions=redactions,
            )
        )
        session.commit()


def list_ai_call_log(limit: int = 50) -> list:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        rows = session.scalars(
            select(AiCallLogRecord)
            .where(AiCallLogRecord.owner_user_id == owner_id)
            .order_by(AiCallLogRecord.id.desc())
            .limit(limit)
        )
        return [
            {
                "id": row.id,
                "created_at": row.created_at,
                "feature": row.feature,
                "model": row.model,
                "status": row.status,
                "chars_sent": row.chars_sent,
                "redactions": row.redactions,
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Smart templates: item kind + structured fields
# ---------------------------------------------------------------------------


def save_item_card(item_id: int, kind: str, fields: dict) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return None
        item.kind = kind
        item.kind_fields = json.dumps(fields, ensure_ascii=False)
        session.commit()
        return _item_to_dict(item)


def list_items_by_kind(kind: Optional[str] = None) -> list:
    """Items that already have a card, newest first (optionally one kind)."""
    owner_id = require_owner_id()
    with SessionLocal() as session:
        query = select(InboxItemRecord).where(
            InboxItemRecord.owner_user_id == owner_id,
            InboxItemRecord.kind.is_not(None),
        )
        if kind:
            query = query.where(InboxItemRecord.kind == kind)
        query = query.order_by(InboxItemRecord.id.desc())
        return [_item_to_dict(item) for item in session.scalars(query)]


# ---------------------------------------------------------------------------
# People & entities
# ---------------------------------------------------------------------------


def _entity_to_dict(entity: EntityRecord, item_count: Optional[int] = None) -> dict:
    data = {"id": entity.id, "name": entity.name, "type": entity.entity_type}
    if item_count is not None:
        data["item_count"] = item_count
    return data


def save_item_entities(item_id: int, entities: list) -> Optional[list]:
    """Replace the entities linked to one item. Reuses existing entity rows."""
    owner_id = require_owner_id()
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return None

        # Start fresh for this item, so re-running never leaves stale links.
        session.execute(delete(ItemEntityRecord).where(ItemEntityRecord.item_id == item_id))

        saved = []
        for found in entities:
            entity = session.scalars(
                select(EntityRecord).where(
                    EntityRecord.owner_user_id == owner_id,
                    EntityRecord.entity_type == found["type"],
                    EntityRecord.name_key == found["name"].lower(),
                )
            ).first()
            if entity is None:
                entity = EntityRecord(
                    owner_user_id=owner_id,
                    name=found["name"],
                    name_key=found["name"].lower(),
                    entity_type=found["type"],
                    created_at=now,
                )
                session.add(entity)
                session.flush()
            session.add(ItemEntityRecord(item_id=item_id, entity_id=entity.id))
            saved.append(_entity_to_dict(entity))
        session.commit()
        return saved


def list_entities(
    entity_type: Optional[str] = None,
    days: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list:
    """Entities that appear in at least one item, most-mentioned first.
    
    If days, start_date, or end_date is given, only counts items created within that timeframe.
    """
    owner_id = require_owner_id()
    with SessionLocal() as session:
        item_count = func.count(ItemEntityRecord.item_id)
        query = (
            select(EntityRecord, item_count)
            .join(ItemEntityRecord, ItemEntityRecord.entity_id == EntityRecord.id)
            .where(EntityRecord.owner_user_id == owner_id)
        )
        needs_item_join = False
        item_filters = []
        if days is not None:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            item_filters.append(InboxItemRecord.created_at >= cutoff)
            needs_item_join = True
        if start_date:
            start_iso = start_date if "T" in start_date else f"{start_date}T00:00:00"
            item_filters.append(InboxItemRecord.created_at >= start_iso)
            needs_item_join = True
        if end_date:
            end_iso = end_date if "T" in end_date else f"{end_date}T23:59:59"
            item_filters.append(InboxItemRecord.created_at <= end_iso)
            needs_item_join = True

        if needs_item_join:
            query = query.join(
                InboxItemRecord, ItemEntityRecord.item_id == InboxItemRecord.id
            ).where(*item_filters)

        query = query.group_by(EntityRecord.id).order_by(item_count.desc(), EntityRecord.name)
        if entity_type:
            query = query.where(EntityRecord.entity_type == entity_type)
        return [_entity_to_dict(e, count) for e, count in session.execute(query)]


def get_entity_page(
    entity_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[dict]:
    """One entity plus every item that mentions it, newest first (optionally filtered by date range)."""
    owner_id = require_owner_id()
    with SessionLocal() as session:
        entity = session.get(EntityRecord, entity_id)
        if entity is None or entity.owner_user_id != owner_id:
            return None
        query = (
            select(InboxItemRecord)
            .join(ItemEntityRecord, ItemEntityRecord.item_id == InboxItemRecord.id)
            .where(ItemEntityRecord.entity_id == entity_id)
        )
        if start_date:
            start_iso = start_date if "T" in start_date else f"{start_date}T00:00:00"
            query = query.where(InboxItemRecord.created_at >= start_iso)
        if end_date:
            end_iso = end_date if "T" in end_date else f"{end_date}T23:59:59"
            query = query.where(InboxItemRecord.created_at <= end_iso)

        items = session.scalars(query.order_by(InboxItemRecord.id.desc()))
        page = _entity_to_dict(entity)
        page["items"] = [_item_to_dict(item) for item in items]
        page["filtered_count"] = len(page["items"])
        return page


# ---------------------------------------------------------------------------
# Semantic search: embedding cache
# ---------------------------------------------------------------------------


def load_cached_vectors(hashes: list, model: str) -> dict:
    """{text_hash: vector} for the hashes that are already in the cache."""
    if not hashes:
        return {}
    with SessionLocal() as session:
        rows = session.scalars(
            select(EmbeddingCacheRecord).where(
                EmbeddingCacheRecord.model == model,
                EmbeddingCacheRecord.text_hash.in_(set(hashes)),
            )
        )
        return {row.text_hash: json.loads(row.vector) for row in rows}


def save_cached_vectors(vectors: dict, model: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        for text_hash, vector in vectors.items():
            session.merge(
                EmbeddingCacheRecord(
                    text_hash=text_hash,
                    model=model,
                    vector=json.dumps([round(x, 6) for x in vector]),
                    created_at=now,
                )
            )
        session.commit()


# ---------------------------------------------------------------------------
# Flashcards
# ---------------------------------------------------------------------------


def _flashcard_to_dict(card: FlashcardRecord) -> dict:
    return {
        "id": card.id,
        "item_id": card.item_id,
        "question": card.question,
        "answer": card.answer,
        "box": card.box,
        "due_date": card.due_date,
        "last_reviewed_at": card.last_reviewed_at,
    }


def add_flashcards(item_id: int, cards: list, due_date: str) -> Optional[list]:
    owner_id = require_owner_id()
    now = datetime.now(timezone.utc).isoformat()
    with SessionLocal() as session:
        item = session.get(InboxItemRecord, item_id)
        if item is None or item.owner_user_id != owner_id:
            return None
        records = [
            FlashcardRecord(
                owner_user_id=owner_id,
                item_id=item_id,
                question=card["question"],
                answer=card["answer"],
                box=0,
                due_date=due_date,
                created_at=now,
            )
            for card in cards
        ]
        session.add_all(records)
        session.commit()
        return [_flashcard_to_dict(r) for r in records]


def list_due_flashcards(today: str) -> list:
    """Cards due today or earlier, most overdue first."""
    owner_id = require_owner_id()
    with SessionLocal() as session:
        cards = session.scalars(
            select(FlashcardRecord)
            .where(
                FlashcardRecord.owner_user_id == owner_id,
                FlashcardRecord.due_date <= today,
            )
            .order_by(FlashcardRecord.due_date, FlashcardRecord.id)
        )
        return [_flashcard_to_dict(c) for c in cards]


def save_flashcard_review(card_id: int, box: int, due_date: str) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        card = session.get(FlashcardRecord, card_id)
        if card is None or card.owner_user_id != owner_id:
            return None
        card.box = box
        card.due_date = due_date
        card.last_reviewed_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _flashcard_to_dict(card)


def get_flashcard(card_id: int) -> Optional[dict]:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        card = session.get(FlashcardRecord, card_id)
        if card is None or card.owner_user_id != owner_id:
            return None
        return _flashcard_to_dict(card)


# ---------------------------------------------------------------------------
# Public API: tokens and webhooks
# ---------------------------------------------------------------------------


def _token_to_dict(token: ApiTokenRecord) -> dict:
    # Never return token_hash -- not even the hash leaves the server.
    return {
        "id": token.id,
        "name": token.name,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
    }


def add_api_token(name: str, token_hash: str) -> dict:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        token = ApiTokenRecord(
            owner_user_id=owner_id,
            name=name,
            token_hash=token_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(token)
        session.commit()
        return _token_to_dict(token)


def list_api_tokens() -> list:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        tokens = session.scalars(
            select(ApiTokenRecord).where(ApiTokenRecord.owner_user_id == owner_id)
        )
        return [_token_to_dict(t) for t in tokens]


def delete_api_token(token_id: int) -> bool:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        token = session.get(ApiTokenRecord, token_id)
        if token is None or token.owner_user_id != owner_id:
            return False
        session.delete(token)
        session.commit()
        return True


def owner_for_token_hash(token_hash: str) -> Optional[int]:
    """Which user a token belongs to (None if unknown). Records last use."""
    with SessionLocal() as session:
        token = session.scalars(
            select(ApiTokenRecord).where(ApiTokenRecord.token_hash == token_hash)
        ).first()
        if token is None:
            return None
        token.last_used_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return token.owner_user_id


def _webhook_to_dict(hook: WebhookRecord) -> dict:
    return {"id": hook.id, "url": hook.url, "event": hook.event, "created_at": hook.created_at}


def add_webhook(url: str, event: str, secret: str) -> dict:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        hook = WebhookRecord(
            owner_user_id=owner_id,
            url=url,
            event=event,
            secret=secret,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(hook)
        session.commit()
        return _webhook_to_dict(hook)


def list_webhooks() -> list:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        hooks = session.scalars(
            select(WebhookRecord).where(WebhookRecord.owner_user_id == owner_id)
        )
        return [_webhook_to_dict(h) for h in hooks]


def webhooks_for_event(event: str) -> list:
    """Webhooks (with their secrets, for signing) for the current user."""
    owner_id = get_current_owner_id()
    if owner_id is None:
        return []
    with SessionLocal() as session:
        hooks = session.scalars(
            select(WebhookRecord).where(
                WebhookRecord.owner_user_id == owner_id, WebhookRecord.event == event
            )
        )
        return [{"url": h.url, "secret": h.secret} for h in hooks]


def delete_webhook(webhook_id: int) -> bool:
    owner_id = require_owner_id()
    with SessionLocal() as session:
        hook = session.get(WebhookRecord, webhook_id)
        if hook is None or hook.owner_user_id != owner_id:
            return False
        session.delete(hook)
        session.commit()
        return True
