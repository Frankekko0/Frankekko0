"""Authentication: register, login, logout, current user.

Browser sessions use an httpOnly ``SameSite=Lax`` cookie holding a short JWT plus a readable
CSRF cookie (double-submit pattern). API clients may instead request the token in the response
body and send it as ``Authorization: Bearer``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.errors import AppError, AuthenticationError, ConflictError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimit
from app.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    create_access_token,
    hash_password,
    new_csrf_token,
    password_needs_rehash,
    verify_password,
)
from app.db.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, SessionOut, UserOut
from app.schemas.common import Message
from app.seed import ensure_user_defaults

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)
auth_limit = RateLimit("auth", per_minute=get_settings().auth_rate_limit_per_minute)
# Constant-time-ish defence against user enumeration: verify against a dummy hash when unknown.
_DUMMY_HASH = hash_password("not-the-password-dummy")


def _set_session(response: Response, user: User) -> SessionOut:
    settings = get_settings()
    token = create_access_token(user.id, user.token_version)
    csrf = new_csrf_token()
    max_age = settings.access_token_ttl_minutes * 60
    common = {
        "max_age": max_age,
        "secure": settings.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "domain": settings.cookie_domain,
    }
    response.set_cookie(ACCESS_COOKIE, token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)  # type: ignore[arg-type]
    return SessionOut(
        user=UserOut.model_validate(user), csrf_token=csrf, access_token=token, expires_in=max_age
    )


@router.post("/register", response_model=SessionOut, status_code=201, dependencies=[Depends(auth_limit)])
async def register(
    body: RegisterRequest, response: Response, db: DB, include_token: bool = Query(False)
) -> SessionOut:
    settings = get_settings()
    if not settings.allow_registration:
        raise AppError("Le registrazioni sono disabilitate.", code="registration_disabled")
    email = body.email.lower()
    exists = (
        await db.execute(select(func.count()).select_from(User).where(User.email == email))
    ).scalar_one()
    if exists:
        raise ConflictError("Esiste già un account con questa email.", code="email_taken")
    user = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    await db.flush()
    await ensure_user_defaults(db, user)
    await db.commit()
    await db.refresh(user)
    log.info("auth.registered", user_id=str(user.id))
    session = _set_session(response, user)
    if not include_token:
        session.access_token = None
    return session


@router.post("/login", response_model=SessionOut, dependencies=[Depends(auth_limit)])
async def login(
    body: LoginRequest, response: Response, db: DB, include_token: bool = Query(False)
) -> SessionOut:
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        raise AuthenticationError("Email o password non corretti.", code="invalid_credentials")
    if not verify_password(body.password, user.password_hash) or not user.is_active:
        log.info("auth.login_failed", user_id=str(user.id))
        raise AuthenticationError("Email o password non corretti.", code="invalid_credentials")
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user)
    session = _set_session(response, user)
    if not include_token:
        session.access_token = None
    return session


@router.post("/logout", response_model=Message)
async def logout(response: Response, user: CurrentUser, db: DB, everywhere: bool = Query(False)) -> Message:
    if everywhere:
        user.token_version += 1
        await db.commit()
    settings = get_settings()
    for name in (ACCESS_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", domain=settings.cookie_domain)
    return Message(message="Disconnesso.")


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
