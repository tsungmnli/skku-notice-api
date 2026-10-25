"""Pull an application deadline out of free text (a title now, the body once it is fetched).

Conservative on purpose: a date counts as a deadline only when the text marks it as one —
the end of a `A~B` range, an open `~B`, `B까지`, or `마감: B`. A bare event date such as
`2026.11.18.(수) 11:30~13:00` is not a deadline.

When no time is written the result is 23:59 KST, which the API treats as "date only".
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.timeutil import KST

DATE_ONLY_TIME = time(23, 59)

_WEEKDAY = r"(?:\s*\([월화수목금토일]\)|\s*[월화수목금토일](?:요일)?)?"
_DATE = (
    r"(?<!\d)(?:(?P<y>\d{4})\s*(?:[./-]|년)\s*)?"
    r"(?P<m>\d{1,2})\s*(?:[./-]|월)\s*(?P<d>\d{1,2})(?!\d)\s*일?"
)
_DATE_RE = re.compile(_DATE)
_TILDE = "~∼～"
_TIME_RE = re.compile(
    r"^(?:\s*\([월화수목금토일]\)|\s*[월화수목금토일](?:요일)?)?\s*"
    r"(?:(?P<ampm>오전|오후)\s*(?P<h1>\d{1,2})\s*시(?:\s*(?P<min1>\d{1,2})\s*분)?"
    r"|(?P<h2>\d{1,2})\s*:\s*(?P<min2>\d{2}))"
)
_AFTER_RE = re.compile(rf"^{_WEEKDAY}(?:\s*(?:오전|오후)?\s*[\d:시분\s]*)?\s*(?:까지|마감)")
_BEFORE_MARK_RE = re.compile(r"(?:마감(?:일|기한)?\s*[:：]?\s*|[" + _TILDE + r"]\s*)$")


@dataclass(frozen=True)
class _Hit:
    start: int
    end: int
    value: datetime


def _build_date(m: re.Match[str], posted_on: date, not_before: date | None) -> date | None:
    month, day = int(m["m"]), int(m["d"])
    try:
        if m["y"]:
            return date(int(m["y"]), month, day)
        year = posted_on.year
        found = date(year, month, day)
        # No year written: pick the year that puts it near the posting date, never in the past.
        floor = not_before or (posted_on - timedelta(days=60))
        if found < floor:
            found = date(year + 1, month, day)
        return found
    except ValueError:
        return None


def _time_after(text: str, end: int) -> time:
    m = _TIME_RE.match(text[end : end + 24])
    if not m:
        return DATE_ONLY_TIME
    if m["ampm"]:
        hour, minute = int(m["h1"]) % 12, int(m["min1"] or 0)
        if m["ampm"] == "오후":
            hour += 12
    else:
        hour, minute = int(m["h2"]), int(m["min2"])
    if hour > 23 or minute > 59:
        return DATE_ONLY_TIME
    return time(hour, minute)


def extract_deadline(text: str, posted_on: date) -> datetime | None:
    """The latest marked deadline in `text` as an aware KST datetime, or None."""
    hits: list[_Hit] = []
    prev_date: date | None = None
    for m in _DATE_RE.finditer(text):
        before = text[max(0, m.start() - 12) : m.start()]
        after = text[m.end() : m.end() + 24]
        marked = bool(_BEFORE_MARK_RE.search(before)) or bool(_AFTER_RE.match(after))
        # `A~B`: B is parsed relative to A so `12/20~1/10` rolls into the next year.
        tilde = before.rstrip()[-1:] in tuple(_TILDE)
        d = _build_date(m, posted_on, prev_date if marked and tilde else None)
        if d is not None and marked:
            at = datetime.combine(d, _time_after(text, m.end()), KST)
            hits.append(_Hit(m.start(), m.end(), at))
        if d is not None:
            prev_date = d
    if not hits:
        return None
    return max(h.value for h in hits)
