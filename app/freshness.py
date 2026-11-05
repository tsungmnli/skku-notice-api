"""The display window of the NEW / UPDATED badges, in one place.

A notice is badge-worthy for `FRESH_DAYS` Seoul days: the day it was posted or last edited and
the six days after it. The web computes the badges itself (the list response stays the same for
everybody, so it can be cached); the server only needs the window to decide which read marks are
still worth keeping. The number is a constant, not a setting, on purpose: the web has its own
copy, and a server that disagreed with it would delete marks the web still needs.

The badges, as the web computes them (docs/api-contract.md has the same table):

    NEW      posted inside the window and not opened by this user
    UPDATED  edited inside the window, not NEW, and the user has not opened it since the edit
             (never opened, or opened before `content_changed_at`)
"""

from datetime import date, datetime, timedelta

from sqlalchemy import ColumnElement, and_, or_

from app.models import Article
from app.timeutil import KST, kst_day_start

FRESH_DAYS = 7


def window_start(now: datetime) -> date:
    """First Seoul day inside the window: today and the six days before it."""
    return now.astimezone(KST).date() - timedelta(days=FRESH_DAYS - 1)


def in_window(start: date) -> ColumnElement[bool]:
    """Posted or edited on/after `start`. `content_changed_at` NULL falls back to `posted_on`."""
    return or_(Article.posted_on >= start, Article.content_changed_at >= kst_day_start(start))


def past_window(start: date) -> ColumnElement[bool]:
    """Exactly the rows `in_window` is not. Written out, not `NOT in_window`, because
    `NOT (x OR NULL)` is NULL for a post that was never edited, and a DELETE would skip it."""
    return and_(
        Article.posted_on < start,
        or_(
            Article.content_changed_at.is_(None), Article.content_changed_at < kst_day_start(start)
        ),
    )
