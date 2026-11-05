"""Read marks of signed-in users: which notices they opened (behind NEW / UPDATED).

Pure functions of (session, arguments); the endpoints are in routers/v1/me.py. Signed-out
visitors keep the same information in the browser and never reach this module.
"""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.freshness import in_window, window_start
from app.models import Article, UserArticleRead


def list_reads(
    session: Session, user_id: uuid.UUID, *, now: datetime
) -> list[tuple[int, datetime]]:
    """(article id, read_at) of every notice still inside its display window, newest first.
    The set is bounded by the window, so there is no paging."""
    stmt = (
        select(UserArticleRead.article_id, UserArticleRead.read_at)
        .join(Article, Article.id == UserArticleRead.article_id)
        .where(UserArticleRead.user_id == user_id, in_window(window_start(now)))
        .order_by(UserArticleRead.read_at.desc(), UserArticleRead.article_id.desc())
    )
    return [(article_id, read_at) for article_id, read_at in session.execute(stmt)]


def save_reads(
    session: Session,
    user_id: uuid.UUID,
    marks: list[tuple[int, datetime | None]],
    *,
    now: datetime,
) -> int:
    """Record that the user opened these notices. Returns how many ids were accepted.

    Idempotent, and safe to send twice or from two devices: a notice keeps the *latest* time it
    was opened, which is what UPDATED compares against an edit. Ids of notices that do not exist
    or are already past the display window are skipped, so the table cannot grow beyond the
    window however many ids a client sends. The caller commits.
    """
    latest: dict[int, datetime] = {}
    for article_id, read_at in marks:
        # A device clock that runs ahead must not hide an edit that has not happened yet.
        at = now if read_at is None else min(read_at, now)
        if article_id not in latest or at > latest[article_id]:
            latest[article_id] = at  # an id twice in one request would break the upsert

    if not latest:
        return 0
    accepted = session.scalars(
        select(Article.id).where(Article.id.in_(list(latest)), in_window(window_start(now)))
    ).all()
    if not accepted:
        return 0

    stmt = pg_insert(UserArticleRead).values(
        [{"user_id": user_id, "article_id": i, "read_at": latest[i]} for i in accepted]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[UserArticleRead.user_id, UserArticleRead.article_id],
            set_={"read_at": stmt.excluded.read_at},
            # Never move a mark backwards (an upload of older marks after a newer click).
            where=stmt.excluded.read_at > UserArticleRead.read_at,
        )
    )
    return len(accepted)
