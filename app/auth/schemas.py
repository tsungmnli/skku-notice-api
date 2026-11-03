"""Request and response bodies of the auth endpoints."""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_serializer

from app.auth.passwords import MAX_LENGTH, MIN_LENGTH
from app.timeutil import KST

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value: str) -> str:
    """Trim and lowercase (the database stores and compares lowercase only)."""
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL.fullmatch(value):
        raise ValueError("올바른 이메일 주소가 아닙니다")
    return value


Email = Annotated[str, AfterValidator(normalize_email)]
Password = Annotated[str, Field(min_length=MIN_LENGTH, max_length=MAX_LENGTH)]
Token = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class RegisterIn(BaseModel):
    email: Email
    password: Password
    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    turnstile_token: str | None = Field(None, max_length=4096)


class EmailIn(BaseModel):
    email: Email


class TokenIn(BaseModel):
    token: Token


class LoginIn(BaseModel):
    email: Email
    # Not length-checked: a wrong-length password is just a wrong password.
    password: Annotated[str, Field(max_length=1024)]


class ResetIn(BaseModel):
    token: Token
    password: Password


class DeleteIn(BaseModel):
    password: Annotated[str, Field(max_length=1024)] | None = None


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    email_verified: bool
    has_password: bool
    google_linked: bool
    created_at: datetime

    @field_serializer("created_at")
    def _as_seoul_iso(self, value: datetime) -> str:
        # The contract says `+09:00` (as for articles); the DB hands back UTC.
        return value.astimezone(KST).isoformat(timespec="seconds")


class LoginOut(BaseModel):
    user: UserOut
    # Only for native apps (they have no cookie jar); the web gets the `sid` cookie instead.
    token: str | None = None
    expires_at: datetime

    @field_serializer("expires_at")
    def _as_seoul_iso(self, value: datetime) -> str:
        return value.astimezone(KST).isoformat(timespec="seconds")


class Detail(BaseModel):
    detail: str
