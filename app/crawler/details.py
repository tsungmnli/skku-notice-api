"""Read the view page of notices whose title gave no deadline, and keep what the body says.

Runs after the list crawl, in the same process and with the same polite `Fetcher`. The body is
parsed and thrown away: only the deadline, the event date and the line they came from are stored.
Each notice is read once (`detail_checked_at`); a page that cannot be fetched or parsed stays
unchecked and is tried again on the next run. After the reading rules change, clearing
`detail_checked_at` (see README) reads the pages again: a deadline that came from the body is
replaced, or dropped when it no longer reads; one that came from a title is never touched.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.deadline import event_for, extract_from_body
from app.crawler.fetch import DetailSource, FetchError
from app.crawler.parser import ParseError, parse_detail
from app.models import Article
from app.timeutil import now_kst

logger = logging.getLogger(__name__)

# The school's server or its markup is the likelier culprit after this many failures in a row.
MAX_FAILURES_IN_A_ROW = 3


@dataclass(frozen=True)
class DetailResult:
    checked: int = 0  # pages read and marked as checked
    deadlines: int = 0  # of those, notices that got a deadline from the body
    events: int = 0  # of those, notices that got an event date
    failed: int = 0  # pages that could not be fetched or parsed (retried next run)
    gave_up: bool = False  # stopped early because of consecutive failures


def _body_may_decide():
    """A notice with no deadline, or with one that an earlier reading took from the body."""
    return or_(Article.deadline_at.is_(None), Article.deadline_source == "body")


def read_details(
    factory: sessionmaker[Session],
    source: DetailSource,
    *,
    limit: int,
    max_age_days: int = 60,
    today: date | None = None,
) -> DetailResult:
    if limit <= 0:
        return DetailResult()
    today = today or now_kst().date()
    with factory() as session:
        todo = session.execute(
            select(
                Article.id,
                Article.article_no,
                Article.title,
                Article.posted_on,
                Article.deadline_at,
            )
            .where(
                _body_may_decide(),
                Article.detail_checked_at.is_(None),
                Article.posted_on >= today - timedelta(days=max_age_days),
            )
            .order_by(Article.posted_on.desc(), Article.id.desc())
            .limit(limit)
        ).all()

    checked = deadlines = events = failed = streak = 0
    for art in todo:
        try:
            body = parse_detail(source.detail_page(art.article_no))
        except (FetchError, ParseError) as exc:
            failed += 1
            streak += 1
            logger.warning("detail %d: %s", art.article_no, exc)
            if streak >= MAX_FAILURES_IN_A_ROW:
                logger.error("%d detail pages failed in a row; stopping for this run", streak)
                return DetailResult(checked, deadlines, events, failed, gave_up=True)
            continue
        streak = 0

        found = extract_from_body(body, art.posted_on)
        # The body says more (a time of day) than the title, so it goes first; but a body that
        # named several days (`event_ambiguous`) is not overruled by one date in the title.
        event_at = event_for(found, art.title, art.posted_on)
        logger.debug("detail %d: deadline=%s event=%s", art.article_no, found.deadline_at, event_at)
        with factory() as session, session.begin():
            session.execute(
                update(Article)
                # A title deadline that arrived meanwhile wins; this row is then done anyway.
                .where(Article.id == art.id, _body_may_decide())
                .values(
                    deadline_at=found.deadline_at,
                    deadline_source="body" if found.deadline_at else None,
                    deadline_evidence=found.deadline_evidence,
                    event_at=event_at,
                    detail_checked_at=func.now(),
                    # A deadline that appeared, moved or vanished is a visible change (the calendar
                    # feed shows `updated_at`); bookkeeping and an unchanged reading leave it alone.
                    updated_at=(
                        func.now() if found.deadline_at != art.deadline_at else Article.updated_at
                    ),
                )
            )
        checked += 1
        deadlines += found.deadline_at is not None
        events += event_at is not None
    return DetailResult(checked, deadlines, events, failed)
