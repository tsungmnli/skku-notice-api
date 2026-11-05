"""Housekeeping for the account tables, run at the end of each crawler CronJob.

The API process is rarely restarted on Kubernetes, so cleaning up at its startup would leave
expired rows piling up; the CronJob runs on a schedule anyway.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.freshness import past_window, window_start
from app.models import Article, EmailToken, UserArticleRead, UserSession


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


def purge_stale_reads(db: Session, *, now: datetime) -> int:
    """Delete read marks of notices that are past their NEW/UPDATED window.

    Once a notice is past it (posted and last edited more than FRESH_DAYS ago) no badge can
    appear for it, so the mark has nothing left to say. The rule is about the *notice*, not the
    age of the mark: deleting "7 days after reading" would drop the mark of something read on
    day 1 and edited on day 5, and on day 8 it would wrongly come back as UPDATED. The caller
    commits.
    """
    past = select(Article.id).where(past_window(window_start(now)))
    return db.execute(delete(UserArticleRead).where(UserArticleRead.article_id.in_(past))).rowcount
