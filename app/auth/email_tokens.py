"""One-time links sent by mail (e-mail verification, password reset)."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.tokens import hash_token, new_token
from app.models import EmailToken

TTL = {"verify": timedelta(hours=24), "reset": timedelta(hours=1)}


def issue_email_token(db: Session, user_id: UUID, purpose: str, *, now: datetime) -> str:
    """A fresh token for (user, purpose). Any earlier unused one is retired first, so asking
    for a new link kills the old one. Returns the raw token; the caller commits."""
    db.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user_id,
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now)
    )
    token = new_token()
    db.add(
        EmailToken(
            token_hash=hash_token(token),
            user_id=user_id,
            purpose=purpose,
            created_at=now,
            expires_at=now + TTL[purpose],
        )
    )
    db.flush()
    return token


def consume_email_token(db: Session, token: str, purpose: str, *, now: datetime) -> UUID | None:
    """Use a token once. Returns its user id, or None if it is unknown, used, expired or for a
    different purpose. A single UPDATE decides, so two concurrent uses cannot both win."""
    return db.scalar(
        update(EmailToken)
        .where(
            EmailToken.token_hash == hash_token(token),
            EmailToken.purpose == purpose,
            EmailToken.used_at.is_(None),
            EmailToken.expires_at > now,
        )
        .values(used_at=now)
        .returning(EmailToken.user_id)
    )


def has_live_token(db: Session, user_id: UUID, purpose: str, *, now: datetime) -> bool:
    return (
        db.scalar(
            select(EmailToken.token_hash).where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
                EmailToken.expires_at > now,
            )
        )
        is not None
    )
