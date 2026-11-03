"""Housekeeping for the account tables, run at the end of each crawler CronJob.

The API process is rarely restarted on Kubernetes, so cleaning up at its startup would leave
expired rows piling up; the CronJob runs on a schedule anyway.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app.models import EmailToken, UserSession


@dataclass(frozen=True)
class PurgeResult:
    sessions: int
    email_tokens: int


def purge_expired(db: Session, *, now: datetime) -> PurgeResult:
    """Delete expired sessions and e-mail tokens that are expired or already used.

    A used token is dead either way (`consume` also requires it unused), so its row is only
    clutter. The caller commits.
    """
    sessions = db.execute(delete(UserSession).where(UserSession.expires_at <= now)).rowcount
    tokens = db.execute(
        delete(EmailToken).where(or_(EmailToken.expires_at <= now, EmailToken.used_at.is_not(None)))
    ).rowcount
    return PurgeResult(sessions, tokens)
