from __future__ import annotations

from pydantic import Field, field_validator

from app.alerts.channels.discord import is_valid_discord_webhook
from app.profit.calculator import CostProfile
from app.schemas.common import Money, Ratio, Schema
from app.scoring.flip import DEFAULT_WEIGHTS, LEGACY_ALIASES


class PreferencesIO(Schema):
    preferred_brands: list[str] = Field(default_factory=list, max_length=100)
    preferred_categories: list[str] = Field(default_factory=list, max_length=100)
    sizes: list[str] = Field(default_factory=list, max_length=50)
    min_profit: Money = Field(ge=0, le=10_000)
    min_roi: Ratio = Field(ge=0, le=20)
    max_purchase_price: Money | None = Field(default=None, gt=0, le=100_000)
    min_flip_score: int | None = Field(default=None, ge=0, le=100)
    max_risk_score: int | None = Field(default=None, ge=0, le=100)
    min_confidence: int | None = Field(default=None, ge=0, le=100)
    cost_profile: CostProfile = Field(default_factory=CostProfile)
    score_weights: dict[str, float] | None = None
    personalization_enabled: bool = True

    @field_validator("score_weights")
    @classmethod
    def _weights(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        unknown = set(v) - set(DEFAULT_WEIGHTS) - set(LEGACY_ALIASES)
        if unknown or any(x < 0 or x > 100 for x in v.values()) or sum(v.values()) <= 0:
            raise ValueError("pesi non validi")
        return v


class NotificationSettingsIO(Schema):
    in_app_enabled: bool = True
    web_push_enabled: bool = False
    email_enabled: bool = False
    email_address: str | None = Field(default=None, max_length=320)
    telegram_enabled: bool = False
    telegram_chat_id: str | None = Field(
        default=None, max_length=64, pattern=r"^-?\d{3,20}$|^@[A-Za-z0-9_]{5,32}$"
    )
    discord_enabled: bool = False
    discord_webhook_url: str | None = Field(
        default=None, max_length=500, description="Write-only; returned masked"
    )
    new_opportunity_alerts: bool = True
    ultra_deal_alerts: bool = True
    price_drop_alerts: bool = True
    watchlist_alerts: bool = True
    alert_min_flip_score: int = Field(default=85, ge=0, le=100)
    alert_min_roi: Ratio = Field(default=0.5, ge=0, le=20)
    alert_min_profit: Money = Field(default=15, ge=0, le=10_000)
    alert_min_confidence: int = Field(default=70, ge=0, le=100)
    alert_max_risk_score: int | None = Field(default=60, ge=0, le=100)

    @field_validator("discord_webhook_url")
    @classmethod
    def _discord(cls, v: str | None) -> str | None:
        if v and not v.startswith("•") and not is_valid_discord_webhook(v):
            raise ValueError("URL webhook Discord non valido")
        return v or None


class ChannelStatus(Schema):
    in_app: bool
    web_push: bool
    email: bool
    telegram: bool
    discord: bool


class NotificationSettingsOut(NotificationSettingsIO):
    available_channels: ChannelStatus
    vapid_public_key: str | None = None


class TestNotificationIn(Schema):
    channel: str = Field(pattern="^(web_push|email|telegram|discord)$")


def mask_secret_url(url: str | None) -> str | None:
    if not url:
        return None
    return "••••••" + url[-6:]
