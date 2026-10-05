"""Application settings loaded from environment variables.

Every secret (JWT secret, API keys, bot tokens, SMTP password, VAPID key) comes from the
environment. Nothing sensitive is hardcoded; defaults are only safe development values.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General -------------------------------------------------------------------------
    app_name: str = "Vinted FlipFinder"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/api/v1"
    public_app_url: str = "http://localhost:3000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Infrastructure ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # --- Security ------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12
    cookie_secure: bool = False
    cookie_domain: str | None = None
    rate_limit_per_minute: int = 240
    trust_proxy_headers: bool = True
    auth_rate_limit_per_minute: int = 10
    allow_registration: bool = True

    # --- Marketplace providers -----------------------------------------------------------
    marketplace_provider: Literal["mock", "feed"] = "mock"
    mock_seed: int = 1337
    mock_history_days: int = 60
    mock_history_minutes_per_listing: float = 8.0
    mock_live_seconds_per_listing: float = 20.0
    feed_url: str | None = None
    feed_api_key: SecretStr | None = None
    feed_requests_per_minute: int = 30

    # --- Scanner / pipeline --------------------------------------------------------------
    scan_interval_seconds: int = 30
    scan_batch_size: int = 500
    analysis_high_priority_prescore: float = 0.35
    market_stats_window_days: int = 90
    comparables_window_days: int = 120
    lifecycle_stale_hours: int = 6
    algorithm_version: str = "2026.10-1"

    # --- Default economics (Vinted Italy style; overridable per user) ---------------------
    default_buyer_protection_fixed: Decimal = Decimal("0.70")
    default_buyer_protection_pct: Decimal = Decimal("0.05")
    default_shipping_in: Decimal = Decimal("3.49")
    default_min_profit: Decimal = Decimal("10")
    default_min_roi: Decimal = Decimal("0.40")
    default_currency: str = "EUR"

    # --- AI ------------------------------------------------------------------------------
    ai_api_key: SecretStr | None = None
    ai_model: str = "claude-sonnet-5-5"
    ai_vision_enabled: bool = True
    ai_auto_analyze_min_flip_score: float = 80.0
    ai_timeout_seconds: float = 30.0

    # --- Notifications -------------------------------------------------------------------
    telegram_bot_token: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str = "FlipFinder <alerts@flipfinder.local>"
    smtp_starttls: bool = True
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:admin@flipfinder.local"

    # --- Demo ----------------------------------------------------------------------------
    seed_demo_user: bool = True
    demo_user_email: str = "demo@flipfinder.app"
    demo_user_password: SecretStr = SecretStr("demo-password-123")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_for_production(self) -> None:
        """Fail fast when production runs with development secrets."""
        if not self.is_production:
            return
        secret = self.jwt_secret.get_secret_value()
        if secret.startswith("dev-only") or len(secret) < 32:
            raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters")
        if not self.cookie_secure:
            raise RuntimeError("COOKIE_SECURE must be true in production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
