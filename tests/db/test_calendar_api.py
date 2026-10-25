"""`GET /api/v1/calendar` — the ICS feed Google Calendar subscribes to."""

from datetime import datetime

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Article
from app.timeutil import KST
from tests.db.test_read_api import add_articles
from tests.ics_util import parse_events

URL = "/api/v1/calendar"


def feed(client, *ids: int):
    return client.get(URL, params=[("id", i) for i in ids])


# Seeded ids: 1 → deadline 2026-02-01 23:59 (passed, date only), 2 → 2026-10-06 17:00 (timed),
# 3 → 2026-11-12 23:59 (date only), 5 → no deadline, 6 → 2026-02-06 23:59.


def test_headers_and_content_type(api):
    res = feed(api, 2)
    assert res.status_code == 200
    assert res.headers["content-type"] == "text/calendar; charset=utf-8"
    assert res.headers["cache-control"] == "public, max-age=300"
    assert res.text.startswith("BEGIN:VCALENDAR\r\n")


def test_date_only_and_timed_deadlines(api):
    by_uid = {e["UID"]: e for e in parse_events(feed(api, 2, 3).text)}
    timed, all_day = by_uid["article-2@skku-notice"], by_uid["article-3@skku-notice"]
    assert timed["DTSTART"] == "20261006T080000Z"
    assert all_day["DTSTART;VALUE=DATE"] == "20261112"
    assert all_day["DTEND;VALUE=DATE"] == "20261113"
    assert timed["SUMMARY"] == "[마감] 입학설명회 개최 안내 (1001)"
    assert timed["URL"].endswith("article_no=1001")


def test_events_are_ordered_by_deadline_not_by_id_or_request_order(api):
    # Article 6's deadline (Feb 6) is earlier than article 3's (Nov 12) although its id is higher.
    uids = [e["UID"] for e in parse_events(feed(api, 3, 6, 2, 1).text)]
    assert uids == [f"article-{i}@skku-notice" for i in (1, 6, 2, 3)]


def test_passed_deadlines_are_still_exported(api):
    # The feed mirrors the user's favorites; hiding past events is the calendar app's job.
    assert [e["UID"] for e in parse_events(feed(api, 1).text)] == ["article-1@skku-notice"]


def test_articles_without_deadline_unknown_and_duplicate_ids_are_ignored(api):
    events = parse_events(feed(api, 2, 5, 99999, 2, 99999999999, 0, -1).text)
    assert [e["UID"] for e in events] == ["article-2@skku-notice"]


def test_nothing_to_export_is_an_empty_but_valid_calendar(api):
    res = feed(api, 5, 99999)
    assert res.status_code == 200
    assert parse_events(res.text) == []
    assert res.text.endswith("END:VCALENDAR\r\n")


@pytest.mark.parametrize("count", [0, 101])
def test_id_count_limits(api, count):
    res = feed(api, *range(1, count + 1))
    assert res.status_code == 422
    assert res.json() == {"detail": "조회할 ID는 1~100개까지 가능합니다."}


def test_exactly_100_ids_are_accepted(api):
    res = feed(api, *range(1, 101))
    assert res.status_code == 200
    assert len(parse_events(res.text)) == 29  # the seed's articles that have a deadline


def test_a_non_numeric_id_is_a_single_detail_string_422(api):
    res = api.get(URL, params={"id": "abc"})
    assert res.status_code == 422
    assert list(res.json()) == ["detail"]


def test_head_is_supported_for_calendar_probes(api):
    res = api.head(f"{URL}?id=2")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/calendar")
    assert res.content == b""


def test_web_client_url_shape(api):
    """`calendarFeedUrl()` in client.ts builds `…/calendar?id=1&id=2` — repeated, unencoded."""
    res = api.get(f"{URL}?id=2&id=3")
    assert len(parse_events(res.text)) == 2


def test_uid_survives_a_changed_deadline_so_clients_update_instead_of_duplicating(
    empty_api, engine
):
    add_articles(engine, {"deadline_at": datetime(2026, 10, 6, 17, 0, tzinfo=KST)})
    before = parse_events(empty_api.get(f"{URL}?id=1").text)[0]

    with Session(engine) as s, s.begin():
        s.execute(update(Article).values(deadline_at=datetime(2026, 12, 1, 23, 59, tzinfo=KST)))
    after = parse_events(empty_api.get(f"{URL}?id=1").text)[0]

    assert before["UID"] == after["UID"] == "article-1@skku-notice"
    assert after["DTSTART;VALUE=DATE"] == "20261201"
    assert after["LAST-MODIFIED"] >= before["LAST-MODIFIED"]
