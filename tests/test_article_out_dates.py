"""`event_at` goes out as a bare date when its time is unknown; `deadline_at` keeps 23:59."""

from datetime import date, datetime

from app.schemas import ArticleOut
from app.timeutil import KST


def out(**dates) -> dict:
    values = {
        "id": 1,
        "article_no": 1,
        "source": "skku",
        "department": "skku",
        "title": "t",
        "writer": "w",
        "posted_on": date(2026, 11, 3),
        "display_no": None,
        "is_pinned": False,
        "view_count": 0,
        "url": "https://example.test/1",
        "categories": [],
        "deadline_at": None,
        "event_at": None,
        "deadline_source": None,
        "content_changed_at": None,
    }
    return ArticleOut(**{**values, **dates}).model_dump(mode="json")


def test_event_without_a_time_is_a_bare_date():
    got = out(event_at=datetime(2026, 11, 20, 23, 59, tzinfo=KST))
    assert got["event_at"] == "2026-11-20"


def test_event_with_a_time_keeps_it():
    got = out(event_at=datetime(2026, 11, 20, 10, 30, tzinfo=KST))
    assert got["event_at"] == "2026-11-20T10:30:00+09:00"


def test_deadline_without_a_time_keeps_its_23_59():
    got = out(deadline_at=datetime(2026, 11, 12, 23, 59, tzinfo=KST), deadline_source="title")
    assert got["deadline_at"] == "2026-11-12T23:59:00+09:00"


def test_no_dates_stay_null():
    got = out()
    assert got["event_at"] is None and got["deadline_at"] is None
