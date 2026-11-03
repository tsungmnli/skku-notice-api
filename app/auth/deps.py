"""FastAPI dependencies for authentication."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.clientip import client_ip
from app.auth.errors import not_authenticated
from app.auth.limiter import RateLimiter
from app.auth.mail import Mailer
from app.auth.sessions import resolve_session
from app.config import Settings
from app.db import get_session
from app.models import User, UserSession
from app.timeutil import get_now

SESSION_COOKIE = "sid"
COOKIE_PATH = "/api"
_BEARER = "bearer "


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.limiter


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_client_ip(request: Request) -> str:
    return client_ip(request, request.app.state.settings.trusted_proxy_hops or 0)


@dataclass(frozen=True)
class Credentials:
    token: str
    # Cookies are sent by the browser on its own, so only cookie-authenticated requests need
    # the CSRF (Origin) check; a Bearer header has to be added by the caller on purpose.
    via_cookie: bool


def read_credentials(request: Request) -> Credentials | None:
    """`Authorization: Bearer <token>` wins over the `sid` cookie."""
    header = request.headers.get("authorization", "")
    if header[: len(_BEARER)].lower() == _BEARER:
        token = header[len(_BEARER) :].strip()
        return Credentials(token, via_cookie=False) if token else None
    cookie = request.cookies.get(SESSION_COOKIE)
    return Credentials(cookie, via_cookie=True) if cookie else None


@dataclass(frozen=True)
class Auth:
    user: User
    session: UserSession
    via_cookie: bool


def optional_auth(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    now: Annotated[datetime, Depends(get_now)],
) -> Auth | None:
    creds = read_credentials(request)
    if creds is None:
        return None
    found = resolve_session(db, creds.token, now=now, ttl_days=settings.session_ttl_days)
    if found is None:
        return None
    db.commit()  # persists the sliding expiry, if it moved
    user, sess = found
    return Auth(user, sess, creds.via_cookie)


def current_auth(auth: Annotated[Auth | None, Depends(optional_auth)]) -> Auth:
    if auth is None:
        raise not_authenticated()
    return auth


def current_user(auth: Annotated[Auth, Depends(current_auth)]) -> User:
    return auth.user
