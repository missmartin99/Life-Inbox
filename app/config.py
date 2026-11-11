"""Startup configuration validation for local and deployable beta modes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    upload_dir: str
    session_secret: str
    auth_mode: str  # dev | google
    environment: str  # local | beta | production
    max_upload_mb: int
    allow_dev_login: bool
    timezone: str
    google_client_id: str = ""
    google_client_secret: str = ""
    public_url: str = "http://localhost:8000"
    allowed_emails: tuple[str, ...] = ()
    allowed_email_domains: tuple[str, ...] = ()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def google_enabled(self) -> bool:
        return bool(
            self.auth_mode == "google"
            and self.google_client_id
            and self.google_client_secret
        )

    @property
    def cookie_secure(self) -> bool:
        return (
            self.environment in {"beta", "production"}
            or self.public_url.startswith("https://")
        )

    @property
    def local_anonymous_allowed(self) -> bool:
        """Anonymous requests map to the Local Owner only in plain local dev."""
        return self.environment == "local" and self.auth_mode == "dev"


def _truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(
        sorted({part.strip().lower() for part in raw.split(",") if part.strip()})
    )


def load_config(*, strict: Optional[bool] = None) -> AppConfig:
    environment = os.getenv("LIFE_INBOX_ENV", "local").strip() or "local"
    database_url = os.getenv(
        "DATABASE_URL",
        "sqlite:///./data/life_inbox.db",
    ).strip()
    upload_dir = os.getenv("LIFE_INBOX_UPLOAD_DIR", "./data/uploads").strip()
    session_secret = os.getenv("LIFE_INBOX_SESSION_SECRET", "").strip()
    auth_mode = os.getenv("LIFE_INBOX_AUTH_MODE", "dev").strip() or "dev"
    if auth_mode == "google_stub":  # legacy value
        auth_mode = "dev"
    timezone = os.getenv("LIFE_INBOX_TIMEZONE", "UTC").strip() or "UTC"
    max_upload_mb = int(os.getenv("LIFE_INBOX_MAX_UPLOAD_MB", "5") or "5")
    allow_dev_login = _truthy(
        "LIFE_INBOX_ALLOW_DEV_LOGIN", "1" if auth_mode == "dev" else "0"
    )
    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    public_url = (
        os.getenv("LIFE_INBOX_PUBLIC_URL", "http://localhost:8000").strip()
        or "http://localhost:8000"
    ).rstrip("/")
    allowed_emails = _csv("LIFE_INBOX_ALLOWED_EMAILS")
    allowed_email_domains = _csv("LIFE_INBOX_ALLOWED_EMAIL_DOMAINS")

    if strict is None:
        strict = environment in {"beta", "production"}

    errors: list[str] = []
    if not database_url:
        errors.append("DATABASE_URL is required.")
    if environment in {"beta", "production"} and database_url.startswith("sqlite"):
        errors.append(
            "PostgreSQL DATABASE_URL is required for beta/production "
            "(SQLite is local/dev only)."
        )
    if not upload_dir:
        errors.append("LIFE_INBOX_UPLOAD_DIR is required.")
    if strict and len(session_secret) < 32:
        errors.append(
            "LIFE_INBOX_SESSION_SECRET must be at least 32 characters in "
            "beta/production."
        )
    if auth_mode not in {"dev", "google"}:
        errors.append("LIFE_INBOX_AUTH_MODE must be 'dev' or 'google'.")
    if environment in {"beta", "production"}:
        if allow_dev_login:
            errors.append(
                "Dev login must be disabled in beta/production "
                "(set LIFE_INBOX_ALLOW_DEV_LOGIN=0)."
            )
        if auth_mode != "google":
            errors.append(
                "LIFE_INBOX_AUTH_MODE must be 'google' in beta/production."
            )
        if not public_url.startswith("https://"):
            errors.append(
                "LIFE_INBOX_PUBLIC_URL must be an https:// URL in "
                "beta/production."
            )
    if auth_mode == "google" and not (google_client_id and google_client_secret):
        errors.append(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required when "
            "LIFE_INBOX_AUTH_MODE=google."
        )
    if urlparse(public_url).scheme not in {"http", "https"}:
        errors.append("LIFE_INBOX_PUBLIC_URL must start with http:// or https://.")
    if max_upload_mb < 1 or max_upload_mb > 50:
        errors.append("LIFE_INBOX_MAX_UPLOAD_MB must be between 1 and 50.")

    if errors and strict:
        raise ConfigError("; ".join(errors))

    if not session_secret:
        # Local/dev only fallback — never for strict environments.
        session_secret = "local-dev-only-session-secret-change-me"

    return AppConfig(
        database_url=database_url,
        upload_dir=upload_dir,
        session_secret=session_secret,
        auth_mode=auth_mode,
        environment=environment,
        max_upload_mb=max_upload_mb,
        allow_dev_login=allow_dev_login,
        timezone=timezone,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        public_url=public_url,
        allowed_emails=allowed_emails,
        allowed_email_domains=allowed_email_domains,
    )


def config_warnings(config: AppConfig) -> list[str]:
    warnings: list[str] = []
    if config.session_secret.startswith("local-dev-only"):
        warnings.append("Using default local session secret.")
    if config.allow_dev_login:
        warnings.append("Dev login is enabled.")
    if config.is_sqlite and config.environment != "local":
        warnings.append("SQLite in use outside local environment.")
    if (
        config.auth_mode == "google"
        and not config.allowed_emails
        and not config.allowed_email_domains
    ):
        warnings.append(
            "Google sign-in is open to any verified Google account "
            "(set LIFE_INBOX_ALLOWED_EMAILS or LIFE_INBOX_ALLOWED_EMAIL_DOMAINS "
            "to restrict)."
        )
    return warnings
