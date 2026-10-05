from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field, field_validator

from app.schemas.common import Schema


class RegisterRequest(Schema):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str | None = Field(default=None, max_length=80)

    @field_validator("password")
    @classmethod
    def _strong_enough(cls, v: str) -> str:
        if v.isdigit() or v.isalpha() or len(set(v)) < 5:
            raise ValueError("La password deve contenere lettere e numeri o simboli.")
        return v

    @field_validator("display_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class LoginRequest(Schema):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AuthConfigOut(Schema):
    registration_enabled: bool
    demo_login_enabled: bool
    demo_user_email: str | None


class UserOut(Schema):
    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime


class SessionOut(Schema):
    user: UserOut
    csrf_token: str
    access_token: str | None = Field(
        default=None, description="Returned only when requested for API clients (Bearer usage)."
    )
    expires_in: int
