"""iCalendar (RFC 5545) feed of article deadlines. Pure functions, no HTTP and no database.

Written by hand: the subset we emit is small, and it avoids a dependency. The details that
calendar clients are picky about are covered by tests/test_ics.py:
  - CRLF line endings, content lines folded at 75 octets (never inside a UTF-8 character),
  - TEXT escaping (backslash, semicolon, comma, newline),
  - a stable UID per article so re-subscribing or refreshing updates events instead of duplicating.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.models import Article
from app.timeutil import KST

CRLF = "\r\n"
PRODID = "-//skku-notice//KO"
CALENDAR_NAME = "성균관대 공지 마감"
# Clients poll on their own schedule (Google: roughly daily); this is only a hint.
REFRESH = "PT6H"
# A timed deadline is shown as a short block starting at the deadline.
TIMED_EVENT_LENGTH = timedelta(minutes=30)


def escape_text(value: str) -> str:
    """Escape a TEXT value (RFC 5545 §3.3.11)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """Fold a content line to at most 75 octets per physical line (§3.1)."""
    if len(line.encode()) <= 75:
        return line
    parts: list[str] = []
    current: list[str] = []
    size = 0
    limit = 75  # the first line; continuation lines carry a leading space, so 74 of content
    for ch in line:
        n = len(ch.encode())
        if size + n > limit:
            parts.append("".join(current))
            current, size, limit = [], 0, 74
        current.append(ch)
        size += n
    parts.append("".join(current))
    return (CRLF + " ").join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def is_date_only(deadline: datetime) -> bool:
    """23:59 Seoul time is how the data marks "a date, no time of day" (see api-contract.md)."""
    local = deadline.astimezone(KST)
    return (local.hour, local.minute) == (23, 59)


def event_lines(a: Article) -> list[str]:
    assert a.deadline_at is not None
    deadline = a.deadline_at
    stamp = _utc(a.updated_at)
    lines = [
        "BEGIN:VEVENT",
        f"UID:article-{a.id}@skku-notice",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
    ]
    if is_date_only(deadline):
        day = deadline.astimezone(KST).date()
        lines += [
            f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
        ]
    else:
        lines += [f"DTSTART:{_utc(deadline)}", f"DTEND:{_utc(deadline + TIMED_EVENT_LENGTH)}"]
    description = "\n".join([f"작성: {a.writer}", a.url])
    lines += [
        f"SUMMARY:{escape_text(f'[마감] {a.title}')}",
        f"DESCRIPTION:{escape_text(description)}",
        f"URL:{a.url}",
        # A deadline is an obligation, not a block of busy time.
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]
    return lines


def build_calendar(articles: Iterable[Article]) -> str:
    """The full VCALENDAR text. Articles without a deadline are skipped."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(CALENDAR_NAME)}",
        "X-WR-TIMEZONE:Asia/Seoul",
        f"REFRESH-INTERVAL;VALUE=DURATION:{REFRESH}",
        f"X-PUBLISHED-TTL:{REFRESH}",
    ]
    for a in articles:
        if a.deadline_at is not None:
            lines += event_lines(a)
    lines.append("END:VCALENDAR")
    return CRLF.join(fold(line) for line in lines) + CRLF
