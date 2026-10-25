"""The ICS generator, without a database."""

from datetime import UTC, datetime, timedelta

import pytest

from app import ics
from app.models import Article
from app.timeutil import KST
from tests.ics_util import parse_events, unfold

STAMP = datetime(2026, 10, 1, 3, 4, 5, tzinfo=UTC)


def article(id_: int = 7, deadline: datetime | None = None, **kw) -> Article:
    return Article(
        id=id_,
        article_no=1000 + id_,
        title=kw.pop("title", "장학금 신청 안내"),
        writer=kw.pop("writer", "장학팀"),
        posted_on=datetime(2026, 9, 1).date(),
        url=kw.pop("url", f"https://example.test/a?article_no={id_}"),
        deadline_at=deadline,
        updated_at=STAMP,
        **kw,
    )


# ---- text escaping and folding -------------------------------------------------------------


def test_escape_text():
    assert ics.escape_text("a,b;c\\d") == "a\\,b\\;c\\\\d"
    assert ics.escape_text("one\ntwo\r\nthree\rfour") == "one\\ntwo\\nthree\\nfour"


def test_short_lines_are_not_folded():
    assert ics.fold("A" * 75) == "A" * 75


def test_a_76_octet_line_is_folded_into_75_plus_one():
    assert ics.fold("A" * 76) == "A" * 75 + "\r\n A"


def test_continuation_lines_hold_74_octets_of_content():
    # Each continuation starts with a space, which counts toward the 75.
    first, second, third = ics.fold("A" * (75 + 74 + 1)).split("\r\n")
    assert (len(first), len(second), len(third)) == (75, 75, 2)


def test_long_ascii_line_is_folded_at_75_octets_and_unfolds_back():
    line = "X-LONG:" + "a" * 200
    folded = ics.fold(line)
    physical = folded.split("\r\n")
    assert all(len(p.encode()) <= 75 for p in physical)
    assert all(p.startswith(" ") for p in physical[1:])
    assert unfold(folded + "\r\n")[0] == line


def test_folding_never_splits_a_multibyte_character():
    line = "SUMMARY:" + "마감안내" * 40  # 3 bytes per character
    folded = ics.fold(line)
    for physical in folded.split("\r\n"):
        assert len(physical.encode()) <= 75
        physical.encode().decode("utf-8")  # raises if a character was cut in half
    assert unfold(folded + "\r\n")[0] == line


# ---- events --------------------------------------------------------------------------------


def test_23_59_in_seoul_is_an_all_day_event():
    a = article(deadline=datetime(2026, 11, 12, 23, 59, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["DTSTART;VALUE=DATE"] == "20261112"
    assert e["DTEND;VALUE=DATE"] == "20261113"  # DTEND of an all-day event is exclusive


def test_all_day_event_rolls_over_month_and_year_ends():
    a = article(deadline=datetime(2026, 12, 31, 23, 59, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    assert (e["DTSTART;VALUE=DATE"], e["DTEND;VALUE=DATE"]) == ("20261231", "20270101")


def test_a_deadline_with_a_time_is_a_timed_event_in_utc():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["DTSTART"] == "20261006T080000Z"
    assert e["DTEND"] == "20261006T083000Z"
    assert "DTSTART;VALUE=DATE" not in e


@pytest.mark.parametrize(
    ("deadline", "all_day"),
    [
        (datetime(2026, 10, 6, 23, 59, tzinfo=KST), True),
        (datetime(2026, 10, 6, 14, 59, tzinfo=UTC), True),  # the same instant as 23:59 KST
        (datetime(2026, 10, 6, 23, 59, tzinfo=UTC), False),  # 08:59 KST the next day
        (datetime(2026, 10, 6, 23, 58, tzinfo=KST), False),
        (datetime(2026, 10, 6, 0, 0, tzinfo=KST), False),
    ],
)
def test_date_only_is_decided_by_the_seoul_wall_clock(deadline, all_day):
    assert ics.is_date_only(deadline) is all_day


def test_a_utc_timestamp_late_in_the_day_lands_on_the_right_seoul_date():
    # 15:30 UTC on Oct 6 is 00:30 KST on Oct 7: a timed event, not mistaken for the 6th.
    a = article(deadline=datetime(2026, 10, 6, 15, 30, tzinfo=UTC))
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["DTSTART"] == "20261006T153000Z"


def test_uid_is_stable_and_derived_from_the_article_id_only():
    first = article(7, datetime(2026, 10, 6, 17, 0, tzinfo=KST), title="before")
    moved = article(7, datetime(2026, 11, 1, 9, 0, tzinfo=KST), title="after")
    (e1,), (e2,) = (parse_events(ics.build_calendar([a])) for a in (first, moved))
    assert e1["UID"] == e2["UID"] == "article-7@skku-notice"
    assert article(8, first.deadline_at).id != 7


def test_output_is_deterministic():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))
    assert ics.build_calendar([a]) == ics.build_calendar([a])


def test_dtstamp_follows_the_row_update_time():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["DTSTAMP"] == e["LAST-MODIFIED"] == "20261001T030405Z"


def test_special_characters_in_title_and_writer_are_escaped():
    a = article(
        deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST),
        title="신청, 접수; 안내\\공지\n둘째 줄",
        writer="A,B팀",
    )
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["SUMMARY"] == "[마감] 신청\\, 접수\\; 안내\\\\공지\\n둘째 줄"
    assert e["DESCRIPTION"].startswith("작성: A\\,B팀\\n")
    assert e["URL"] == a.url  # URI values are not TEXT: no escaping


def test_a_title_cannot_inject_ics_properties():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST), title="x\r\nATTENDEE:evil")
    text = ics.build_calendar([a])
    assert "\r\nATTENDEE" not in text
    (e,) = parse_events(text)
    assert "ATTENDEE" not in e


def test_events_are_transparent():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    assert e["TRANSP"] == "TRANSPARENT"


# ---- the calendar as a whole ----------------------------------------------------------------


def test_framing_and_line_endings():
    text = ics.build_calendar([article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))])
    assert text.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n")
    assert text.endswith("END:VEVENT\r\nEND:VCALENDAR\r\n")
    assert "\n" not in text.replace("\r\n", "")  # no bare LF anywhere
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    assert text.count("BEGIN:VEVENT") == text.count("END:VEVENT") == 1


def test_articles_without_a_deadline_are_skipped():
    text = ics.build_calendar([article(1), article(2, datetime(2026, 10, 6, 17, 0, tzinfo=KST))])
    assert [e["UID"] for e in parse_events(text)] == ["article-2@skku-notice"]


def test_an_empty_feed_is_still_a_valid_calendar():
    text = ics.build_calendar([])
    assert text.startswith("BEGIN:VCALENDAR\r\n")
    assert text.endswith("END:VCALENDAR\r\n")
    assert parse_events(text) == []


def test_timed_event_length_constant_is_what_the_feed_uses():
    a = article(deadline=datetime(2026, 10, 6, 17, 0, tzinfo=KST))
    (e,) = parse_events(ics.build_calendar([a]))
    start = datetime.strptime(e["DTSTART"], "%Y%m%dT%H%M%SZ")
    end = datetime.strptime(e["DTEND"], "%Y%m%dT%H%M%SZ")
    assert end - start == ics.TIMED_EVENT_LENGTH == timedelta(minutes=30)
