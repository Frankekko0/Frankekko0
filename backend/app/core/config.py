"""Application settings loaded from environment variables.

Every secret (JWT secret, API keys, bot tokens, SMTP password, VAPID key) comes from the
environment. Nothing sensitive is hardcoded; defaults are only safe development values.
"""

from __future__ import annotations

import json
from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # Empty values (e.g. `JWT_SECRET=` copied from .env.example) mean "not set", not "".
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    # --- General -------------------------------------------------------------------------
    app_name: str = "Vinted FlipFinder"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    log_json: bool = True
    # Directory for the error log (warnings and errors, rotated); empty = stdout only.
    error_log_dir: str | None = None
    api_prefix: str = "/api/v1"
    public_app_url: str = "http://localhost:3000"
    # Comma-separated in the environment (NoDecode: parsed by the validator, not as JSON)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Infrastructure ------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://flipfinder:flipfinder@localhost:5432/flipfinder"
    redis_url: str = "redis://localhost:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Worker: processes (0 = one per CPU core, at most 4) and a small DB pool per process,
    # so API + workers stay well below PostgreSQL's default 100 connections.
    worker_processes: int = Field(default=0, ge=0, le=32)
    worker_db_pool_size: int = 6
    worker_db_max_overflow: int = 4

    # --- Security ------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    jwt_algorithm: str = "HS256"
    # Wraps the extension's seller hash (see app.core.seller_key). Unset: derived from JWT_SECRET.
    seller_key_secret: SecretStr | None = None
    access_token_ttl_minutes: int = 60 * 12
    cookie_secure: bool = False
    cookie_domain: str | None = None
    rate_limit_per_minute: int = 240
    trust_proxy_headers: bool = True
    trusted_proxy_hops: int = Field(default=1, ge=1, le=5)
    auth_rate_limit_per_minute: int = 10
    allow_registration: bool = True

    # --- Marketplace providers -----------------------------------------------------------
    # "feed": an authorized listing feed (FEED_URL); "none": only your own captures.
    marketplace_provider: Literal["none", "feed"] = "none"
    feed_url: str | None = None
    feed_api_key: SecretStr | None = None
    feed_requests_per_minute: int = 30

    # --- Scanner / pipeline --------------------------------------------------------------
    scan_interval_seconds: int = 30
    scan_batch_size: int = 500
    analysis_high_priority_prescore: float = 0.35
    market_stats_window_days: int = 90
    comparables_window_days: int = 120
    # Not read for this long, a listing claiming to be available becomes "to verify".
    stale_active_hours: int = Field(default=48, ge=1, le=720)
    stale_reserved_hours: int = Field(default=24, ge=1, le=720)
    # A listing seen again unchanged adds a history row only after this long (a "heartbeat").
    snapshot_heartbeat_hours: int = Field(default=6, ge=1, le=168)
    algorithm_version: str = "2026.10-3"

    # Local copies of listing photos (internal use only), uploaded by the browser extension: the
    # server never downloads from Vinted. ``image_archive_enabled`` false: uploads are refused.
    image_archive_enabled: bool = True
    media_dir: str = "var/media"
    image_archive_max_bytes: int = Field(default=10 * 1024 * 1024, ge=100_000, le=50 * 1024 * 1024)

    # Vinted selectors/labels/patterns (shared with the extension). Unset: the bundled file.
    # Point it to a mounted file to update the parser without rebuilding or republishing.
    parser_config_path: str | None = None

    # --- Acquisition modes beyond the extension (opt-in, see docs/ACQUISITION.md) ----------------
    # Vinted notification emails (favourite sold / price reduced): read-only IMAP mailbox.
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: SecretStr | None = None
    imap_folder: str = "INBOX"
    imap_poll_minutes: int = Field(default=15, ge=5, le=1440)

    @property
    def email_import_enabled(self) -> bool:
        return bool(self.imap_host and self.imap_user and self.imap_password)

    # --- External price references (see docs/EXTERNAL_PRICES.md) ------------------------------
    # Official search API (Serper.dev, Google results as JSON), never scraping. none = off. Never
    # searched while a page is analysed: a worker job refreshes a per-model cache within the
    # budget (queries counted per day and per month; 1 query = 1 credit = $0.001).
    external_search_provider: Literal["none", "serper"] = "none"
    serper_api_key: SecretStr | None = None
    external_search_monthly_budget: int = Field(default=900, ge=0, le=1_000_000)
    external_search_daily_max: int = Field(default=60, ge=0, le=100_000)
    external_refresh_days: int = Field(default=30, ge=1, le=365)
    external_search_country: str = "it"
    external_search_language: str = "it"
    # Optional 3rd query per model (concluded sales), only when the first two found the model.
    external_search_sold_query: bool = True

    @property
    def external_search_enabled(self) -> bool:
        return self.external_search_provider != "none" and bool(
            self.serper_api_key and self.serper_api_key.get_secret_value().strip()
        )

    # Opportunity/watchlist alerts only for listings published within this window
    alert_max_listing_age_hours: float = 72.0

    # --- Default economics (Vinted Italy style; overridable per user) ---------------------
    default_buyer_protection_fixed: Decimal = Decimal("0.70")
    default_buyer_protection_pct: Decimal = Decimal("0.05")
    default_shipping_in: Decimal = Decimal("3.49")
    default_min_profit: Decimal = Decimal("10")
    default_min_roi: Decimal = Decimal("0.40")
    default_currency: str = "EUR"

    # --- AI ------------------------------------------------------------------------------
    ai_api_key: SecretStr | None = None
    ai_model: str = "claude-opus-5-5"
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    ai_vision_enabled: bool = True
    # What a photo analysis is assumed to cost in euros for the value-of-information check (model call plus
    # the wait). An assumption, not a measurement: see docs/DEPENDENCIES.md.
    vision_voi_cost_eur: float = Field(default=0.5, ge=0, le=20)
    # Local OCR of labels (RapidOCR, optional dependency): free, offline, no key needed.
    ocr_enabled: bool = True
    ai_auto_analyze_min_flip_score: float = 80.0
    ai_timeout_seconds: float = 90.0
    # Model routing: the cheap tier for volume, the strong one (``ai_model``) where the stakes are.
    ai_model_cheap: str = "claude-haiku-5-5"
    # Spend caps in USD (UTC day and calendar month). A call that would cross a cap is not made:
    # the application falls back to its rules. ``ai_call_reserve_usd`` is held back for one call.
    ai_daily_budget_usd: Decimal = Field(default=Decimal("1.00"), ge=0)
    ai_monthly_budget_usd: Decimal = Field(default=Decimal("20.00"), ge=0)
    ai_call_reserve_usd: Decimal = Field(default=Decimal("0.05"), ge=0)
    # USD per million tokens. ASSUMED, not read from the provider: set them to your plan's prices.
    ai_price_strong_input_per_mtok: Decimal = Decimal("3")
    ai_price_strong_output_per_mtok: Decimal = Decimal("15")
    ai_price_cheap_input_per_mtok: Decimal = Decimal("1")
    ai_price_cheap_output_per_mtok: Decimal = Decimal("5")
    # Circuit breaker: after this many consecutive failures the provider is left alone for a while.
    ai_breaker_failures: int = Field(default=5, ge=1)
    ai_breaker_cooldown_seconds: float = Field(default=60.0, ge=1)
    agent_max_steps: int = Field(default=8, ge=1, le=30)
    agent_max_cost_usd: Decimal = Field(default=Decimal("0.10"), ge=0)
    agent_max_notifications_per_day: int = Field(default=5, ge=0, le=100)
    agent_review_limit: int = Field(default=12, ge=1, le=30)

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [v.strip() for v in text.split(",") if v.strip()]
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
        if ":flipfinder@" in self.database_url:
            raise RuntimeError("DATABASE_URL uses the development password; set POSTGRES_PASSWORD")

    def seller_key_secret_bytes(self) -> bytes:
        from app.core.seller_key import derived_secret

        if self.seller_key_secret is not None and self.seller_key_secret.get_secret_value():
            return self.seller_key_secret.get_secret_value().encode()
        return derived_secret(self.jwt_secret.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
