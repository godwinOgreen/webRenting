"""
Application configuration.

Reads from .env file using pydantic-settings.
Every config value has a type, a default (where safe), and validation.

Usage:
    from app.core.config import settings

    print(settings.DATABASE_URL)
    print(settings.STORAGE_BACKEND)
"""

from pathlib import Path

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ─── Application ─────────────────────────────────────────────────────────
    APP_NAME: str = "Real Estate Platform"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"  # development | staging | production
    API_V1_PREFIX: str = "/api/v1"

    # ─── Database ────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL connection string",
    )

    # ─── Redis (optional until Phase 4+) ─────────────────────────────────────
    REDIS_URL: str | None = Field(
        default=None,
        description="Redis URL. None = skip Redis features (cache, celery, websocket pub/sub)",
    )

    # ─── JWT ─────────────────────────────────────────────────────────────────
    SECRET_KEY: str = Field(
        ...,
        description="JWT signing key. Generate with: python -c 'import secrets; print(secrets.token_urlsafe(64))'",
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ─── OAuth Providers ─────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str | None = Field(
        default=None,
        description="Google OAuth 2.0 client ID. From Google Cloud Console → Credentials.",
    )
    APPLE_CLIENT_ID: str | None = Field(
        default=None,
        description="Apple Services ID. From Apple Developer → Identifiers → Services IDs.",
    )
    FACEBOOK_APP_ID: str | None = Field(
        default=None,
        description="Facebook App ID. From Facebook Developer Portal → Settings → Basic.",
    )
    FACEBOOK_APP_SECRET: str | None = Field(
        default=None,
        description="Facebook App Secret. Required to verify Facebook tokens server-side.",
    )

    # ─── CORS ────────────────────────────────────────────────────────────────
    BACKEND_CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000"],
        description="Allowed frontend origins for CORS.",
    )

    # ─── Storage ─────────────────────────────────────────────────────────────
    STORAGE_BACKEND: str = Field(
        default="local",
        description="Storage backend: local | s3",
    )
    LOCAL_STORAGE_PATH: Path = Path("./uploads")
    LOCAL_STORAGE_URL: str = "http://localhost:8000/files"

    # S3 (only needed when STORAGE_BACKEND=s3)
    S3_BUCKET: str | None = None
    S3_REGION: str | None = None
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_KEY: str | None = None
    S3_CDN_URL: str | None = None

    # ─── Paystack ────────────────────────────────────────────────────────────
    PAYSTACK_SECRET_KEY: str | None = None
    PAYSTACK_PUBLIC_KEY: str | None = None
    PAYSTACK_WEBHOOK_SECRET: str | None = None

    # ─── KYC Provider ────────────────────────────────────────────────────────
    KYC_PROVIDER: str | None = Field(
        default=None,
        description="KYC provider: smile_identity | youverify",
    )
    KYC_API_KEY: str | None = None
    KYC_WEBHOOK_SECRET: str | None = None

    # ─── Google Maps ─────────────────────────────────────────────────────────
    GOOGLE_MAPS_API_KEY: str | None = None

    # ─── Email ───────────────────────────────────────────────────────────────
    EMAIL_PROVIDER: str | None = None
    EMAIL_API_KEY: str | None = None
    EMAIL_FROM: str = "noreply@realestate.ng"

    # ─── SMS ─────────────────────────────────────────────────────────────────
    SMS_PROVIDER: str | None = None
    SMS_API_KEY: str | None = None

    # ─── Push Notifications ──────────────────────────────────────────────────
    FCM_SERVER_KEY: str | None = None

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }


settings = Settings()