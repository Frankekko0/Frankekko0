"""FastAPI dependencies: database session, authentication, CSRF and user settings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER, csrf_tokens_match, decode_access_token
from app.db.models import User, UserPreferences
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


Economics = Annotated[UserEconomics, Depends(get_user_economics)]
