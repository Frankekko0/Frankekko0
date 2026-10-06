"""FastAPI dependencies: database session, authentication, CSRF and user settings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import (
    ACCESS_COOKIE,
    API_KEY_PREFIX,
    CSRF_COOKIE,
    CSRF_HEADER,
    csrf_tokens_match,
    decode_access_token,
    hash_api_key,
)
from app.db.models import ApiKey, User, UserPreferences
from app.db.session import get_db
from app.opportunities.engine import EconomicTargets
from app.profit.calculator import CostProfile

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DB = Annotated[AsyncSession, Depends(get_db)]


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


async def get_current_user(request: Request, db: DB) -> User:
    token = _bearer(request)
    via_cookie = False
    if token is None:
        token = request.cookies.get(ACCESS_COOKIE)
        via_cookie = token is not None
    if not token:
        raise AuthenticationError()
    payload = decode_access_token(token)
    if payload is None:
        raise AuthenticationError()
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError() from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != payload.get("ver"):
        raise AuthenticationError()
    # Cookie sessions must prove same-origin intent on state-changing requests (double submit).
    if (
        via_cookie
        and request.method not in SAFE_METHODS
        and not csrf_tokens_match(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER))
    ):
        raise PermissionDeniedError(
            "Token CSRF mancante o non valido. Ricarica la pagina e riprova.", code="csrf_failed"
        )
    request.state.user_id = str(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_capture_user(request: Request, db: DB) -> User:
    """The browser extension authenticates with its own revocable key (``Bearer ff_ext_…``);
    the web app's session works too. Vinted cookies or tokens are never involved."""
    token = _bearer(request)
    if not token or not token.startswith(API_KEY_PREFIX):
        return await get_current_user(request, db)
    key = (
        await db.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_api_key(token), ApiKey.revoked_at.is_(None))
        )
    ).scalar_one_or_none()
    user = await db.get(User, key.user_id) if key else None
    if key is None or user is None or not user.is_active:
        raise AuthenticationError(
            "Chiave dell'estensione non valida o revocata: generane una nuova in Impostazioni.",
            code="invalid_extension_key",
        )
    now = datetime.now(UTC)
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=5):
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
    request.state.user_id = str(user.id)
    return user


CaptureUser = Annotated[User, Depends(get_capture_user)]


@dataclass(frozen=True)
class UserEconomics:
    costs: CostProfile
    targets: EconomicTargets
    preferences: UserPreferences | None


def economics_for(prefs: UserPreferences | None) -> UserEconomics:
    if prefs is None:
        from app.opportunities.pipeline import default_cost_profile, default_targets

        return UserEconomics(default_cost_profile(), default_targets(), None)
    costs = CostProfile.model_validate(prefs.cost_profile or {})
    targets = EconomicTargets(min_profit=Decimal(prefs.min_profit), min_roi=Decimal(prefs.min_roi))
    return UserEconomics(costs, targets, prefs)


async def get_user_economics(user: CurrentUser) -> UserEconomics:
    return economics_for(user.preferences)


async def get_capture_economics(user: CaptureUser) -> UserEconomics:
    return economics_for(user.preferences)


Economics = Annotated[UserEconomics, Depends(get_user_economics)]
CaptureEconomics = Annotated[UserEconomics, Depends(get_capture_economics)]
