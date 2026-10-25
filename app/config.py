"""Startup configuration validation for local and deployable beta modes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    upload_dir: str
    session_secret: str
    auth_mode: str  # dev | google_stub
    environment: str  # local | beta | production
    max_upload_mb: int
    allow_dev_login: bool
    timezone: str

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


def _truthy(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


def load_config(*, strict: Optional[bool] = None) -> AppConfig:
    environment = os.getenv("LIFE_INBOX_ENV", "local").strip() or "local"
    database_url = os.getenv(
        "DATABASE_URL",
        "sqlite:///./data/life_inbox.db",
    ).strip()
    upload_dir = os.getenv("LIFE_INBOX_UPLOAD_DIR", "./data/uploads").strip()
    session_secret = os.getenv("LIFE_INBOX_SESSION_SECRET", "").strip()
    auth_mode = os.getenv("LIFE_INBOX_AUTH_MODE", "dev").strip() or "dev"
    timezone = os.getenv("LIFE_INBOX_TIMEZONE", "UTC").strip() or "UTC"
    max_upload_mb = int(os.getenv("LIFE_INBOX_MAX_UPLOAD_MB", "5") or "5")
    allow_dev_login = _truthy("LIFE_INBOX_ALLOW_DEV_LOGIN", "1")

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
    if auth_mode not in {"dev", "google_stub"}:
        errors.append("LIFE_INBOX_AUTH_MODE must be 'dev' or 'google_stub'.")
    if environment in {"beta", "production"} and allow_dev_login and auth_mode == "dev":
        errors.append(
            "Dev login must be disabled in beta/production "
            "(set LIFE_INBOX_ALLOW_DEV_LOGIN=0)."
        )
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
    )


def config_warnings(config: AppConfig) -> list[str]:
    warnings: list[str] = []
    if config.session_secret.startswith("local-dev-only"):
        warnings.append("Using default local session secret.")
    if config.allow_dev_login:
        warnings.append("Dev login is enabled.")
    if config.is_sqlite and config.environment != "local":
        warnings.append("SQLite in use outside local environment.")
    return warnings
