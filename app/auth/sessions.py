"""Server-side sessions. The browser (or app) holds a random token; the database holds its hash.

Expiry is sliding: using a session pushes `expires_at` out again, but the write happens at most
once a day so a busy client does not turn every request into an UPDATE.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.tokens import hash_token, new_token
from app.models import User, UserSession

CLIENTS = ("web", "android", "ios")
TOUCH_INTERVAL = timedelta(days=1)
_UA_MAX = 200


def summarize_user_agent(user_agent: str | None) -> str | None:
    """A short, printable form for the device list (not the raw header)."""
    if not user_agent:
        return None
    cleaned = "".join(ch for ch in user_agent if ch.isprintable()).strip()
    return cleaned[:_UA_MAX] or None


def create_session(
    db: Session,
    user_id,
    *,
    now: datetime,
    ttl_days: int,
    client: str = "web",
    user_agent: str | None = None,
) -> str:
    """Store a new session and return the raw token (the only time it exists in the clear).
    The caller commits."""
    if client not in CLIENTS:
        raise ValueError(f"unknown client {client!r}")
    token = new_token()
    db.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user_id,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=ttl_days),
            client=client,
            user_agent=summarize_user_agent(user_agent),
        )
    )
    db.flush()
    return token


def resolve_session(
    db: Session, token: str, *, now: datetime, ttl_days: int
) -> tuple[User, UserSession] | None:
    """The user and session behind `token`, or None if it is unknown, expired, or its account
    has no verified e-mail. Slides the expiry (at most once a day); the caller commits."""
    row = db.execute(
        select(User, UserSession)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == hash_token(token), UserSession.expires_at > now)
    ).first()
    if row is None:
        return None
    user, sess = row
    if user.email_verified_at is None:
        return None
    if now - sess.last_used_at >= TOUCH_INTERVAL:
        sess.last_used_at = now
        sess.expires_at = now + timedelta(days=ttl_days)
        db.flush()
    return user, sess


def revoke_session(db: Session, token_hash: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == token_hash))


def revoke_user_sessions(db: Session, user_id, *, except_hash: str | None = None) -> int:
    """Sign a user out everywhere (optionally keeping the current session). Returns the count."""
    stmt = delete(UserSession).where(UserSession.user_id == user_id)
    if except_hash is not None:
        stmt = stmt.where(UserSession.token_hash != except_hash)
    return db.execute(stmt).rowcount


def delete_expired_sessions(db: Session, *, now: datetime) -> int:
    return db.execute(delete(UserSession).where(UserSession.expires_at <= now)).rowcount
