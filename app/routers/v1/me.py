"""The signed-in user: /api/v1/me."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response
from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app import reads
from app.auth import limits
from app.auth.deps import Auth, current_auth, get_app_settings, get_limiter
from app.auth.errors import AuthError
from app.auth.limiter import RateLimiter
from app.auth.passwords import verify_password
from app.auth.preferences import (
    Preferences,
    PreferencesPatch,
    apply_patch,
    load_preferences,
)
from app.auth.schemas import DeleteIn, UserOut
from app.config import Settings
from app.db import get_session
from app.models import User
from app.routers.v1.auth import clear_session_cookie, user_out
from app.schemas import ReadOut, ReadsIn, ReadsOut, ReadsSaved
from app.timeutil import get_now

router = APIRouter(prefix="/me", tags=["me"])

# A Google-only account has no password to ask for, so "signed in recently" stands in for it.
RECENT_LOGIN = timedelta(minutes=10)


@router.get("", response_model=UserOut)
def get_me(auth: Annotated[Auth, Depends(current_auth)]):
    return user_out(auth.user)


@router.patch("/preferences", response_model=Preferences)
def update_preferences(
    patch: PreferencesPatch,
    auth: Annotated[Auth, Depends(current_auth)],
    db: Annotated[Session, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
):
    """Save settings that follow the account (calendar filters). A key that is present replaces
    its section, `null` resets it, an absent key is left alone. Answers with the stored result."""
    limits.enforce(limiter, f"prefs:{auth.user.id}", limits.PREFERENCES_PER_USER)
    stored = apply_patch(auth.user.preferences, patch)
    db.execute(update(User).where(User.id == auth.user.id).values(preferences=stored))
    db.commit()
    return load_preferences(stored)


@router.get("/reads", response_model=ReadsOut)
def list_my_reads(
    auth: Annotated[Auth, Depends(current_auth)],
    db: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
):
    """The notices this account opened that are still inside the NEW/UPDATED window."""
    rows = reads.list_reads(db, auth.user.id, now=now)
    return ReadsOut(items=[ReadOut(article_id=i, read_at=at) for i, at in rows])


# POST, not PUT: the web flushes pending marks with `navigator.sendBeacon` when the tab closes,
# and a beacon can only POST. The call is still idempotent.
@router.post("/reads", response_model=ReadsSaved)
def mark_reads(
    body: ReadsIn,
    auth: Annotated[Auth, Depends(current_auth)],
    db: Annotated[Session, Depends(get_session)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
    now: Annotated[datetime, Depends(get_now)],
):
    """Mark notices as opened (up to 200 ids). Safe to repeat; see `reads.save_reads`."""
    limits.enforce(limiter, f"reads:{auth.user.id}", limits.READS_PER_USER)
    saved = reads.save_reads(
        db, auth.user.id, [(m.article_id, m.read_at) for m in body.items], now=now
    )
    db.commit()
    return ReadsSaved(saved=saved)


@router.delete("", status_code=204)
def delete_me(
    auth: Annotated[Auth, Depends(current_auth)],
    response: Response,
    db: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    limiter: Annotated[RateLimiter, Depends(get_limiter)],
    now: Annotated[datetime, Depends(get_now)],
    body: Annotated[DeleteIn | None, Body()] = None,
):
    """Delete the account and everything tied to it (sessions and tokens cascade).

    Re-authentication is required, so a stolen session alone cannot erase the account.
    """
    user = auth.user
    if user.password_hash is not None:
        key = f"delete:{user.id}"
        limits.enforce_not_blocked(limiter, key, limits.DELETE_FAIL_PER_USER)
        password = body.password if body and body.password is not None else ""
        if not verify_password(user.password_hash, password):
            limiter.record(key, limits.DELETE_FAIL_PER_USER[1])
            raise AuthError("비밀번호가 올바르지 않습니다.", "invalid_credentials", 403)
    elif now - auth.session.created_at > RECENT_LOGIN:
        raise AuthError("계정을 삭제하려면 다시 로그인해 주세요.", "reauth_required", 403)

    db.execute(delete(User).where(User.id == user.id))
    db.commit()
    clear_session_cookie(response, settings)
    response.status_code = 204
    return response
