"""Pull an application deadline out of free text: a notice title, or its body.

Conservative on purpose: a date counts as a deadline only when the text marks it as one —
the end of a `A~B` range, an open `~B`, `B까지`, or `마감: B`. A bare event date such as
`2026.11.18.(수) 11:30~13:00` is not a deadline.

When no time is written the result is 23:59 KST, which the API treats as "date only".

Body text is read differently (`extract_from_body`): a date counts only when it follows a clue word
from `deadline_keywords`, and event dates (일시, 일정, ...) are kept apart from deadlines.
A body with two different deadlines gives none: a wrong date costs more than a missing one.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.crawler.deadline_keywords import DEADLINE_CLUES, EVENT_CLUES, NOT_CLUES
from app.timeutil import KST

DATE_ONLY_TIME = time(23, 59)

_WEEKDAY = r"(?:\s*\([월화수목금토일]\)|\s*[월화수목금토일](?:요일)?)?"
_DATE = (
    # Year: `2026.` or two digits `26.` (only 20-39, so `11.12.` stays month.day).
    r"(?<!\d)(?:(?P<y>\d{4}|[23]\d)\s*(?:[./-]|년)\s*)?"
    r"(?P<m>\d{1,2})\s*(?:[./-]|월)\s*(?P<d>\d{1,2})(?!\d)\s*일?"
)
_DATE_RE = re.compile(_DATE)
_TILDE = "~∼～"
_TIME_RE = re.compile(
    r"^(?:\s*\([월화수목금토일]\)|\s*[월화수목금토일](?:요일)?)?\s*"
    r"(?:(?P<ampm>오전|오후)\s*(?P<h1>\d{1,2})\s*시(?:\s*(?P<min1>\d{1,2})\s*분)?"
    r"|(?P<h2>\d{1,2})\s*:\s*(?P<min2>\d{2})"
    r"|(?P<h3>\d{1,2})\s*시(?!간)(?:\s*(?P<min3>\d{1,2})\s*분)?"
    r"|(?P<noon>정오)|(?P<midnight>자정))"
)
_AFTER_RE = re.compile(
    rf"^{_WEEKDAY}(?:\s*(?:오전|오후)?\s*[\d:시분\s]*|\s*(?:정오|자정))?\s*(?:까지|마감)"
)
# Body only: a date directly followed by `마감` (`~ 11. 15(일) 자정 마감`).
# Not `마감일`, `마감되었`: those name or report a deadline instead of marking the date as one.
_BEFORE_MAEGAM_RE = re.compile(
    rf"^{_WEEKDAY}(?:\s*(?:오전|오후)?\s*[\d:시분\s]*|\s*(?:정오|자정))?"
    r"\s*마감(?!일|기한|되|됨|완료)"
)
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
            year = int(m["y"])
            return date(year + 2000 if year < 100 else year, month, day)
        year = posted_on.year
        found = date(year, month, day)
        # No year written: pick the year that puts it near the posting date, never in the past.
        floor = not_before or (posted_on - timedelta(days=60))
        if found < floor:
            found = date(year + 1, month, day)
        return found
    except ValueError:
        return None


def _skip_dot(text: str, end: int) -> int:
    """`2026.11.18.(수)`, `~11.12.(목)`: step over the dot that closes a date."""
    if text[end : end + 1] == "." and not text[end + 1 : end + 2].isdigit():
        return end + 1
    return end


def _time_after(text: str, end: int) -> time:
    end = _skip_dot(text, end)
    m = _TIME_RE.match(text[end : end + 24])
    if not m:
        return DATE_ONLY_TIME
    if m["noon"]:
        return time(12, 0)
    if m["midnight"]:
        # `11/15 자정까지` is the end of the 15th, which is what a bare date already means.
        return DATE_ONLY_TIME
    if m["ampm"]:
        hour, minute = int(m["h1"]) % 12, int(m["min1"] or 0)
        if m["ampm"] == "오후":
            hour += 12
    elif m["h2"]:
        hour, minute = int(m["h2"]), int(m["min2"])
    else:
        hour, minute = int(m["h3"]), int(m["min3"] or 0)
    if hour > 23 or minute > 59:
        return DATE_ONLY_TIME
    return time(hour, minute)


def extract_deadline(text: str, posted_on: date) -> datetime | None:
    """The latest marked deadline in `text` as an aware KST datetime, or None."""
    hits: list[_Hit] = []
    prev_date: date | None = None
    for m in _DATE_RE.finditer(text):
        before = text[max(0, m.start() - 12) : m.start()]
        after_at = _skip_dot(text, m.end())
        after = text[after_at : after_at + 24]
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


# ---------------------------------------------------------------------------------------------
# Event dates in a title: `(11/6 금)`, `11/20(금) 오후 1시` — a date nobody marked as a deadline.
# ---------------------------------------------------------------------------------------------

_WEEKDAY_NEXT_RE = re.compile(
    r"^(?:\s*\([월화수목금토일]\)|\s*[월화수목금토일](?:요일)?(?![가-힣]))"
)
_SPAN_BEFORE = timedelta(days=7)
_SPAN_AFTER = timedelta(days=366)


def _plausible(d: date, posted_on: date, grace: timedelta = _SPAN_BEFORE) -> bool:
    return posted_on - grace <= d <= posted_on + _SPAN_AFTER


def extract_event_from_title(title: str, posted_on: date) -> datetime | None:
    """When the event in a title happens, or None.

    Only a title without a deadline is read, and a bare `1.5` or `3.12` must not pass for a date,
    so the date needs a weekday or a time next to it, or the `11월 6일` spelling.
    """
    if extract_deadline(title, posted_on) is not None:
        return None
    for m in _DATE_RE.finditer(title):
        end = _skip_dot(title, m.end())
        after = title[end : end + 24]
        spelled_out = "월" in m.group(0)
        if not (spelled_out or _WEEKDAY_NEXT_RE.match(after) or _TIME_RE.match(after)):
            continue
        d = _build_date(m, posted_on, None)
        if d is not None and _plausible(d, posted_on):
            return datetime.combine(d, _time_after(title, end), KST)
    return None


# ---------------------------------------------------------------------------------------------
# Body text: only dates that follow a clue word.
# ---------------------------------------------------------------------------------------------

EVIDENCE_MAX = 100
_RANGE_CHARS = _TILDE + "-–—"


def _clue_regex(words: tuple[str, ...]) -> re.Pattern[str]:
    ordered = sorted(set(words), key=len, reverse=True)
    return re.compile("|".join(r"\s*".join(re.escape(ch) for ch in w) for w in ordered))


_DEADLINE_CLUE_RE = _clue_regex(DEADLINE_CLUES)
_EVENT_CLUE_RE = _clue_regex(EVENT_CLUES)


@dataclass(frozen=True)
class BodyDates:
    deadline_at: datetime | None = None
    event_at: datetime | None = None
    # The body line the deadline was read from (shown as a tooltip, used to debug clue words).
    deadline_evidence: str | None = None
    # The body names more than one deadline (or more than one `날짜+까지`), so none is given.
    deadline_ambiguous: bool = False
    # The body's event dates are a range or several days (`11/16~11/17`, four sessions), so none
    # is given, and the title must not stand in for them either.
    event_ambiguous: bool = False


def _tidy(line: str) -> str:
    line = line.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    line = " ".join(line.split())
    for word in NOT_CLUES:
        line = line.replace(word, " " * len(word))
    return line


def _find_clues(line: str) -> list[tuple[int, int, str]]:
    """(start, end, kind) of each clue word, left to right, never overlapping.

    `모집일정` is a deadline clue that contains the event clue `일정`; the one that starts first
    (and is longer) wins.
    """
    spans = [(m.start(), m.end(), "deadline") for m in _DEADLINE_CLUE_RE.finditer(line)]
    spans += [(m.start(), m.end(), "event") for m in _EVENT_CLUE_RE.finditer(line)]
    spans.sort(key=lambda s: (s[0], s[0] - s[1]))
    kept: list[tuple[int, int, str]] = []
    for span in spans:
        if kept and span[0] < kept[-1][1]:
            continue
        kept.append(span)
    return kept


def _segment_dates(line: str, start: int, end: int, posted_on: date) -> list[datetime]:
    """Plausible dates written in line[start:end], left to right."""
    found: list[datetime] = []
    prev: tuple[date, int] | None = None
    for m in _DATE_RE.finditer(line, start, end):
        # `12/20~1/10`: the second date is read relative to the first so it rolls into next year.
        ranged = prev is not None and any(c in _RANGE_CHARS for c in line[prev[1] : m.start()])
        d = _build_date(m, posted_on, prev[0] if prev and ranged else None)
        if d is None:
            continue
        prev = (d, m.end())
        if _plausible(d, posted_on):
            found.append(datetime.combine(d, _time_after(line, m.end()), KST))
    return found


NO_GRACE = timedelta(0)
# What may sit between the two ends of a range: `.(월) 13:00 ~`. Anything else is two dates.
_RANGE_GAP_RE = re.compile(
    r"[\s.]*(?:\([월화수목금토일]\)|[월화수목금토일](?:요일)?)?[\s.]*"
    r"(?:(?:오전|오후)?\s*\d{1,2}\s*(?::\s*\d{2}|시(?:\s*\d{1,2}\s*분)?)|정오|자정)?"
    r"\s*[" + re.escape(_RANGE_CHARS) + r"]\s*"
)
_UNTIL_RE = re.compile(rf"^{_WEEKDAY}(?:\s*(?:오전|오후)?\s*[\d:시분\s]*|\s*(?:정오|자정))?\s*까지")
_UNTIL_SPAN = timedelta(days=366)
# `2026. 11. 3.(화) ~ 소진 시까지`: the date opens a period that has no end date. It is the start,
# not a deadline, so it is no candidate.
_OPEN_END_RE = re.compile(
    rf"^{_WEEKDAY}(?:\s*(?:오전|오후)?\s*[\d:시분\s]*|\s*(?:정오|자정))?\s*[{_TILDE}]\s*"
    r"(?:예산\s*)?(?:소진|별도|추후|상시|선착순|공지|충원)"
)


def _deadline_candidates(line: str, start: int, end: int, posted_on: date) -> list[datetime]:
    """The deadlines written in line[start:end]: one per date, or per `A~B` range (its end).

    `11/15까지 (기존 11/10)` is two candidates, `11/1 ~ 11/5` is one. A deadline before the posting
    date is over before anyone could read the notice, so it is no candidate.
    """
    groups: list[tuple[date, datetime | None]] = []  # last date of each chain, and its datetime
    prev_end: int | None = None
    for m in _DATE_RE.finditer(line, start, end):
        chained = prev_end is not None and _RANGE_GAP_RE.fullmatch(line, prev_end, m.start())
        prev = groups[-1][0] if groups else None
        d = _build_date(m, posted_on, prev if chained else None)
        prev_end = m.end()
        if d is None:
            continue
        if _OPEN_END_RE.match(line[_skip_dot(line, m.end()) :][:40]):
            continue
        at = (
            datetime.combine(d, _time_after(line, m.end()), KST)
            if _plausible(d, posted_on, NO_GRACE)
            else None
        )
        if chained and groups:
            groups[-1] = (d, at)
        else:
            groups.append((d, at))
    return [at for _, at in groups if at is not None]


@dataclass(frozen=True)
class _EventDate:
    at: datetime  # the first date written (23:59 when it has no time)
    is_range: bool  # `11/16 ~ 11/17`: it spans more than one day


def _event_dates(
    line: str, start: int, end: int, posted_on: date, *, drop_deadlines: bool = False
) -> list[_EventDate]:
    """The event dates written in line[start:end]: one per date, or per `A~B` range.

    `11/16 ~ 11/17` is one entry that says it is a range; `11/6 09:30~18:30` is one plain date.
    With `drop_deadlines` a date followed by `까지` or `마감` is left out: standing on a line of its
    own it is a deadline, not one more day of the event.
    """
    chains: list[list[tuple[date, datetime | None, bool]]] = []
    prev_end: int | None = None
    for m in _DATE_RE.finditer(line, start, end):
        chained = prev_end is not None and _RANGE_GAP_RE.fullmatch(line, prev_end, m.start())
        prev = chains[-1][-1][0] if chains else None
        d = _build_date(m, posted_on, prev if chained else None)
        prev_end = m.end()
        if d is None:
            continue
        at = (
            datetime.combine(d, _time_after(line, m.end()), KST)
            if _plausible(d, posted_on)
            else None
        )
        after = _skip_dot(line, m.end())
        marked = bool(_AFTER_RE.match(line[after : after + 30]))
        if chained and chains:
            chains[-1].append((d, at, marked))
        else:
            chains.append([(d, at, marked)])
    found: list[_EventDate] = []
    for chain in chains:
        if drop_deadlines and chain[-1][2]:
            continue
        plausible = [at for _, at, _ in chain if at is not None]
        if plausible:
            found.append(_EventDate(plausible[0], len({d for d, _, _ in chain}) > 1))
    return found


def _resolve_event(entries: list[_EventDate]) -> tuple[datetime | None, bool]:
    """(the event date all entries agree on, whether they disagree). No entry: (None, False).

    A range or two different days are a conflict. The same day with two different times keeps
    the day and drops the time (23:59, "date only"); a time written on just one of them is kept.
    """
    if not entries:
        return None, False
    if any(e.is_range for e in entries) or len({e.at.date() for e in entries}) > 1:
        return None, True
    timed = {e.at for e in entries if e.at.time() != DATE_ONLY_TIME}
    if len(timed) > 1:
        return datetime.combine(entries[0].at.date(), DATE_ONLY_TIME, KST), False
    if timed:
        return next(iter(timed)), False
    return entries[0].at, False


def _until_dates(text: str, posted_on: date) -> set[date]:
    """Every date in the body that is written as `날짜까지`, clue word or not."""
    found: set[date] = set()
    for raw in text.splitlines():
        line = _tidy(raw)
        for m in _DATE_RE.finditer(line):
            after = _skip_dot(line, m.end())
            if not _UNTIL_RE.match(line[after : after + 30]):
                continue
            d = _build_date(m, posted_on, None)
            if d is not None and abs(d - posted_on) <= _UNTIL_SPAN:
                found.add(d)
    return found


def _resolve_deadline(
    candidates: list[tuple[datetime, str]],
) -> tuple[tuple[datetime, str] | None, bool]:
    """(the deadline all candidates agree on, whether they disagree). No candidate: (None, False).

    The same day named twice is one deadline; a time written on one of them (`마감 : 11/5 18:00`
    next to `접수기간 : 11/1~11/5`) is kept. Two different times on one day are a conflict.
    """
    if not candidates:
        return None, False
    if len({at.date() for at, _ in candidates}) > 1:
        return None, True
    timed = {at: line for at, line in candidates if at.time() != DATE_ONLY_TIME}
    if len(timed) > 1:
        return None, True
    if timed:
        return next(iter(timed.items())), False
    return candidates[0], False


def _dates_before_maegam(line: str, posted_on: date) -> list[datetime]:
    """Dates written right before `마감`: `2026. 10. 28(수) ~ 11. 15(일) 자정 마감`.

    The clue word comes after its date here, so the usual "date after a clue" reading misses it.
    In a range only the end can be followed by `마감`; the start is read just to place the end.
    """
    found: list[datetime] = []
    prev: tuple[date, int] | None = None
    for m in _DATE_RE.finditer(line):
        ranged = prev is not None and any(c in _RANGE_CHARS for c in line[prev[1] : m.start()])
        d = _build_date(m, posted_on, prev[0] if prev and ranged else None)
        if d is None:
            continue
        prev = (d, m.end())
        after = _skip_dot(line, m.end())
        if _BEFORE_MAEGAM_RE.match(line[after : after + 30]) and _plausible(d, posted_on, NO_GRACE):
            found.append(datetime.combine(d, _time_after(line, m.end()), KST))
    return found


# After the first date under an event heading, this many lines without a date may follow before
# the dates stop (`※ 장소 : ...` between two sessions).
MAX_EVENT_GAP = 2


def extract_from_body(text: str, posted_on: date) -> BodyDates:
    """Deadline and event date of a notice, read from the dates that follow a clue word.

    A clue and its date may sit on two lines (`접수기간` / `- 11/1 ~ 11/5`); one following line is
    checked. Several different deadlines, or several different `날짜까지` anywhere in the body (a
    list of schools, each with its own date): no deadline, `deadline_ambiguous` says so.

    The event date comes from the first clue that has dates. A heading with no date of its own
    (`1. 일시 (4회 중 택 1)`) takes the dated lines below it as one group: up to `MAX_EVENT_GAP`
    lines without a date may sit between them, and a clue word or a `날짜까지` line is no session.
    A range (`11/16 ~ 11/17`) or two different days in the group: no event date,
    `event_ambiguous` says so. The same day at two times keeps the day.
    """
    deadlines: list[tuple[datetime, str]] = []
    event_dates: list[_EventDate] = []
    waiting: str | None = None  # kind of a clue whose date has not shown up yet
    event_open = False  # dated lines under an event heading may still follow
    event_gap = 0  # lines without a date since the last event date

    def record_deadline(dates: list[datetime], line: str) -> None:
        evidence = " ".join(line.split())[:EVIDENCE_MAX]
        deadlines.extend((at, evidence) for at in dates)

    for raw in text.splitlines():
        line = _tidy(raw)
        if not line.strip():
            continue
        before_maegam = _dates_before_maegam(line, posted_on)
        if before_maegam:
            record_deadline(before_maegam, line)
        clues = _find_clues(line)
        if not clues:
            kind, waiting = waiting, None
            if kind == "deadline":
                dates = _deadline_candidates(line, 0, len(line), posted_on)
                if dates:
                    record_deadline(dates, line)
            elif kind == "event":
                found = _event_dates(line, 0, len(line), posted_on)
                if found:
                    event_dates.extend(found)
                    event_open, event_gap = True, 0
            elif event_open:
                found = _event_dates(line, 0, len(line), posted_on, drop_deadlines=True)
                event_dates.extend(found)
                event_gap = 0 if found else event_gap + 1
                event_open = event_gap <= MAX_EVENT_GAP
            continue
        waiting = None
        event_open, event_gap = False, 0  # a clue word ends the group above it
        for i, (_, end, kind) in enumerate(clues):
            seg_end = clues[i + 1][0] if i + 1 < len(clues) else len(line)
            if kind == "deadline":
                dates = _deadline_candidates(line, end, seg_end, posted_on)
                if dates:
                    record_deadline(dates, line)
                    continue
            elif event_dates:
                continue  # the first event dates win; later ones are not read
            else:
                found = _event_dates(line, end, seg_end, posted_on)
                if found:
                    event_dates.extend(found)
                    continue
            if i == len(clues) - 1:
                waiting = kind
        if before_maegam:
            waiting = None  # this line's `마감` already has its date; do not borrow the next line's

    best, differ = _resolve_deadline(deadlines)
    event_at, event_ambiguous = _resolve_event(event_dates)
    # A list of `학교: 날짜까지` lines has no single deadline, whatever its clue words say.
    ambiguous = differ or len(_until_dates(text, posted_on)) > 1
    if best is None or ambiguous:
        return BodyDates(
            event_at=event_at, deadline_ambiguous=ambiguous, event_ambiguous=event_ambiguous
        )
    return BodyDates(
        deadline_at=best[0],
        event_at=event_at,
        deadline_evidence=best[1],
        event_ambiguous=event_ambiguous,
    )


def event_for(found: BodyDates, title: str, posted_on: date) -> datetime | None:
    """The event date to store: the body's; the title's only when the body had no event date.

    A body that named several days (`event_ambiguous`) is not overruled by one date in the title.
    """
    if found.event_at is not None or found.event_ambiguous:
        return found.event_at
    return extract_event_from_title(title, posted_on)


@dataclass(frozen=True)
class DatedLine:
    text: str  # the line as the extractor sees it (spaces collapsed)
    start: int  # where its first date begins, so `text[:start]` is what leads up to it
    dates: tuple[datetime, ...]


def dated_lines(text: str, posted_on: date) -> list[DatedLine]:
    """Every body line that holds a plausible date, clue word or not.

    For `scripts/audit_deadlines.py`: a notice with no deadline is shown with these lines so you
    can see which wording the clue words missed.
    """
    found: list[DatedLine] = []
    for raw in text.splitlines():
        line = _tidy(raw)
        dates = _segment_dates(line, 0, len(line), posted_on)
        first = _DATE_RE.search(line)
        if dates and first:
            found.append(DatedLine(line, first.start(), tuple(dates)))
    return found
