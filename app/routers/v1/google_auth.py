"""Google sign-in for the web: /api/v1/auth/google and /api/v1/auth/google/callback.

The browser is sent to Google, Google sends it back to the callback, and the callback ends in
a redirect to the web app with the `sid` cookie set. Failures also end in a redirect
(`/login?error=<code>`), because the user is looking at a browser tab, not an API client.
"""

import re
from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import limits
from app.auth.deps import get_app_settings, get_client_ip, get_limiter
from app.auth.errors import AuthError
from app.auth.google import upsert_google_user
from app.auth.limiter import RateLimiter
from app.auth.sessions import create_session
from app.config import API_PREFIX, Settings
from app.db import get_session
from app.routers.v1.auth import set_session_cookie
from app.timeutil import get_now

router = APIRouter(prefix="/auth/google", tags=["auth"])

CALLBACK_PATH = f"{API_PREFIX}/v1/auth/google/callback"
_DEV_API_URL = "http://localhost:8000"
_DEV_WEB_URL = "http://localhost:3000"
START_PER_IP = (60, limits.HOUR)

# A path on our own site: one leading slash (not `//host`), then URL-safe characters only.
_SAFE_NEXT = re.compile(r"^/(?!/)[A-Za-z0-9\-._~/%?=&#:@+,]{0,199}$")


def safe_next(value: str | None) -> str:
    """Where to land after sign-in. Anything that is not a plain path on this site is dropped,
    so the login URL cannot be used as an open redirect."""
    return value if value and _SAFE_NEXT.fullmatch(value) else "/"


def _base(settings: Settings, default: str) -> str:
    return (settings.public_url or default).rstrip("/")


def _oauth(request: Request):
    """The Authlib Google client, or None when Google sign-in is not configured."""
    oauth = request.app.state.oauth
    return oauth.google if oauth is not None else None


def _failure(settings: Settings, code: str) -> RedirectResponse:
    url = f"{_base(settings, _DEV_WEB_URL)}/login?{urlencode({'error': code})}"
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


@router.get("")
async def google_start(
    request: Request,
    settings: Annotated[Settings, Depends(get_app_settings)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
    ip: Annotated[str, Depends(get_client_ip)],
    next_path: Annotated[str | None, Query(alias="next", max_length=300)] = None,
):
    client = _oauth(request)
    if client is None:
        # The browser is on this URL (window.location), so a JSON 404 would be a dead end.
        return _failure(settings, "google_disabled")
    limits.enforce(limiter, f"google:ip:{ip}", START_PER_IP)
    request.session["next"] = safe_next(next_path)
    redirect_uri = _base(settings, _DEV_API_URL) + CALLBACK_PATH
    return await client.authorize_redirect(request, redirect_uri)


def _finish(
    db: Session,
    settings: Settings,
    *,
    sub: str,
    email: str,
    name: str | None,
    now: datetime,
    user_agent: str | None,
) -> str:
    user = upsert_google_user(db, sub=sub, email=email, name=name, now=now)
    token = create_session(
        db,
        user.id,
        now=now,
        ttl_days=settings.session_ttl_days,
        client="web",
        user_agent=user_agent,
    )
    db.commit()
    return token


@router.get("/callback")
async def google_callback(
    request: Request,
    db: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    now: Annotated[datetime, Depends(get_now)],
):
    client = _oauth(request)
    if client is None:
        return _failure(settings, "google_disabled")
    target = safe_next(request.session.pop("next", None))
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:  # denied by the user, bad/expired state, failed code exchange
        return _failure(settings, "google_failed")

    info = token.get("userinfo") or {}
    sub, email = info.get("sub"), info.get("email")
    if not sub or not email:
        return _failure(settings, "google_failed")
    if info.get("email_verified") is not True:
        return _failure(settings, "google_unverified")

    try:
        session_token = await run_in_threadpool(
            _finish,
            db,
            settings,
            sub=str(sub),
            email=str(email),
            name=info.get("name"),
            now=now,
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        return _failure(settings, exc.code)

    response = RedirectResponse(
        _base(settings, _DEV_WEB_URL) + target,
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )
    set_session_cookie(response, settings, session_token)
    return response
