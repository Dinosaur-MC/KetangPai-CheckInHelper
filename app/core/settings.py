"""Centralized configuration via Pydantic Settings.

All environment variables are read and validated at startup.
Usage::

    from app.core.settings import settings

    db_url = settings.database_url
"""

from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──
    database_url: str = ""
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600
    db_auto_migrate: bool = True
    db_backup_dir: str = "./backups"
    db_backup_retention_days: int = 30

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── JWT ──
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24
    jwt_refresh_expire_days: int = 30

    # ── Credential encryption ──
    credential_key: str = ""

    # ── CORS ──
    allowed_origins: str = ""

    # ── Server ──
    port: int = 8765
    debug: bool = False


settings = Settings()
