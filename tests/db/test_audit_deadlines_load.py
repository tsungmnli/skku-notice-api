from datetime import date, datetime

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import Article
from app.timeutil import KST
from scripts.audit_deadlines import load_missing

TODAY = date(2026, 11, 3)


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def add(factory, article_no, posted_on, deadline_at=None, **cols):
    with factory() as s, s.begin():
        s.add(
            Article(
                article_no=article_no,
                title=f"공지 {article_no}",
                writer="w",
                posted_on=posted_on,
                url="u",
                deadline_at=deadline_at,
                **cols,
            )
        )


def test_lists_recent_notices_without_a_deadline_newest_first(factory):
    add(factory, 1, date(2026, 11, 1))
    add(factory, 2, date(2026, 11, 2))
    add(factory, 3, date(2026, 11, 2), deadline_at=datetime(2026, 11, 9, 23, 59, tzinfo=KST))
    add(factory, 4, date(2026, 8, 1))  # older than --days
    # Checked ones are listed too: they are exactly the misses worth a second look.
    add(factory, 5, date(2026, 10, 20), detail_checked_at=datetime(2026, 10, 21, tzinfo=KST))

    got = load_missing(factory, days=60, limit=10, today=TODAY)

    assert [n.article_no for n in got] == [2, 1, 5]
    assert got[0].title == "공지 2" and got[0].posted_on == date(2026, 11, 2)


def test_limit_applies(factory):
    for no in range(1, 6):
        add(factory, no, date(2026, 11, 2))
    assert len(load_missing(factory, days=60, limit=3, today=TODAY)) == 3


def test_missing_selects_which_empty_value_picks_a_notice(factory):
    posted = date(2026, 11, 2)
    deadline = datetime(2026, 11, 9, 23, 59, tzinfo=KST)
    event = datetime(2026, 11, 20, 14, 0, tzinfo=KST)
    add(factory, 1, posted)  # neither
    add(factory, 2, posted, event_at=event)  # no deadline
    add(factory, 3, posted, deadline_at=deadline)  # no event date
    add(factory, 4, posted, deadline_at=deadline, event_at=event)  # both

    def picked(missing):
        got = load_missing(factory, days=60, limit=10, today=TODAY, missing=missing)
        return sorted(n.article_no for n in got)

    assert picked("deadline") == [1, 2]
    assert picked("event") == [1, 3]
    assert picked("either") == [1, 2, 3]


def test_the_default_still_means_no_deadline(factory):
    add(factory, 1, date(2026, 11, 2), event_at=datetime(2026, 11, 20, 14, 0, tzinfo=KST))
    add(factory, 2, date(2026, 11, 2), deadline_at=datetime(2026, 11, 9, 23, 59, tzinfo=KST))
    assert [n.article_no for n in load_missing(factory, days=60, limit=10, today=TODAY)] == [1]
