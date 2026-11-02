"""Why some notices have no deadline or event date: `python -m scripts.audit_deadlines`.

Development tool, read-only. The body of a notice is not kept in the database, so this reads the
view pages again (politely, one request a second) and prints every body line that holds a date,
marked with what the extractor made of it. A line the extractor did not read is a lead: the word
in front of its date is listed as a clue-word candidate. Add the ones that mean "deadline" to
`app/crawler/deadline_keywords.py`, then run this again.

A value can also be empty on purpose: the body named several deadlines, or the event is a range
or several sessions, so the extractor gave none (`deadline_ambiguous`, `event_ambiguous`; the
database does not keep them). Such notices are flagged with ⚠ and counted in the summary; look at
them to see whether the safeguard was right to empty them.

    python -m scripts.audit_deadlines                    # newest 20 notices without a deadline (DB)
    python -m scripts.audit_deadlines --limit 50 --days 30
    python -m scripts.audit_deadlines --missing event --ambiguous-only   # emptied event dates
    python -m scripts.audit_deadlines --article-no 141478 141483   # no DB needed
    python -m scripts.audit_deadlines --html saved_page.html       # a saved page, no network
"""

import argparse
import logging
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.config import get_crawler_settings
from app.crawler.deadline import (
    BodyDates,
    DatedLine,
    dated_lines,
    event_for,
    extract_deadline,
    extract_from_body,
)
from app.crawler.deadline_keywords import DEADLINE_CLUES, EVENT_CLUES
from app.crawler.fetch import DetailSource, Fetcher, FetchError
from app.crawler.parser import ParseError, article_url, parse_detail
from app.db import make_engine, make_sessionmaker
from app.models import Article
from app.timeutil import now_kst

MAX_LIMIT = 100  # one run never makes more requests to the school's server than this
MISSING_CHOICES = ("deadline", "event", "either")  # which empty value picks a notice to read
_KNOWN = {w.replace(" ", "") for w in (*DEADLINE_CLUES, *EVENT_CLUES)}
_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")


@dataclass(frozen=True)
class Notice:
    article_no: int
    title: str
    posted_on: date


@dataclass
class NoticeAudit:
    notice: Notice
    title_deadline: datetime | None = None
    body: BodyDates = field(default_factory=BodyDates)
    event_at: datetime | None = None
    lines: list[tuple[str, bool, str | None]] = field(default_factory=list)  # (line, read, word)
    error: str | None = None

    @property
    def has_deadline(self) -> bool:
        return self.title_deadline is not None or self.body.deadline_at is not None

    @property
    def deadline_ambiguous(self) -> bool:
        """The body named several deadlines, so none was kept. A title deadline would have won."""
        return self.body.deadline_ambiguous and self.title_deadline is None

    @property
    def event_ambiguous(self) -> bool:
        """The body's event dates are a range or several days, so none was kept."""
        return self.body.event_ambiguous

    @property
    def ambiguous(self) -> bool:
        return self.deadline_ambiguous or self.event_ambiguous


def clue_candidate(lead: str) -> str | None:
    """The word in front of a date: `참가방법 : 이메일 접수(~` -> `접수`."""
    words = _WORD_RE.findall(lead)
    return words[-1] if words else None


def audit_notice(notice: Notice, body_text: str) -> NoticeAudit:
    found = extract_from_body(body_text, notice.posted_on)
    audit = NoticeAudit(
        notice,
        title_deadline=extract_deadline(notice.title, notice.posted_on),
        body=found,
        event_at=event_for(found, notice.title, notice.posted_on),
    )
    read_dates = {d for d in (found.deadline_at, found.event_at) if d is not None}
    for dl in dated_lines(body_text, notice.posted_on):
        # Read: one of its dates is what the extractor kept (the clue may be on the line before),
        # or the line stands on its own (clue and date together) for the extractor.
        alone = extract_from_body(dl.text, notice.posted_on)
        was_read = bool(read_dates.intersection(dl.dates)) or any(
            d is not None for d in (alone.deadline_at, alone.event_at)
        )
        audit.lines.append((dl.text, was_read, None if was_read else candidate_of(dl)))
    return audit


def candidate_of(dl: DatedLine) -> str | None:
    word = clue_candidate(dl.text[: dl.start])
    return None if word is None or word in _KNOWN else word


def fmt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def render(audit: NoticeAudit) -> str:
    n = audit.notice
    out = [f"#{n.article_no}  {n.posted_on}  {n.title}"] if n.article_no else [n.title]
    if n.article_no:
        out.append(f"  {article_url(n.article_no)}")
    if audit.error:
        return "\n".join([*out, f"  ! {audit.error}", ""])
    out.append(
        f"  제목 마감 {fmt(audit.title_deadline)} | 본문 마감 {fmt(audit.body.deadline_at)}"
        f" | 행사일 {fmt(audit.event_at)}"
    )
    if audit.deadline_ambiguous:
        out.append("  ⚠ 마감 모호: 본문에 서로 다른 마감 날짜가 여럿이라 비웠습니다")
    if audit.event_ambiguous:
        out.append("  ⚠ 행사일 모호: 범위이거나 여러 날이라 비웠고, 제목 날짜도 쓰지 않습니다")
    if audit.body.deadline_evidence:
        out.append(f"  근거: {audit.body.deadline_evidence}")
    if not audit.lines:
        out.append("  (날짜가 들어간 줄 없음)")
    for line, read, word in audit.lines:
        mark = "✓" if read else "·"
        hint = f"   <- 단서어 후보: {word}" if word else ""
        out.append(f"  {mark} {line[:120]}{hint}")
    out.append("")
    return "\n".join(out)


def label(notice: Notice) -> str:
    """`#141425`; a saved page (`--html`) has no number, so its name stands in."""
    return f"#{notice.article_no}" if notice.article_no else notice.title


def summarize(audits: Sequence[NoticeAudit]) -> str:
    ok = [a for a in audits if not a.error]
    missed = [a for a in ok if not a.has_deadline]
    no_deadline = [a for a in ok if a.deadline_ambiguous]
    no_event = [a for a in ok if a.event_ambiguous]
    counts = Counter(w for a in missed for _, _, w in a.lines if w)
    out = [
        f"읽은 글 {len(ok)}건 (실패 {len(audits) - len(ok)}건): "
        f"마감 있음 {len(ok) - len(missed)}건, 마감 없음 {len(missed)}건",
        f"모호해서 비운 글: 마감 {len(no_deadline)}건, 행사일 {len(no_event)}건",
    ]
    for name, hits in (("마감", no_deadline), ("행사일", no_event)):
        if hits:
            out.append(f"  {name}: " + " ".join(label(a.notice) for a in hits))
    if counts:
        out.append("마감이 없는 글에서 날짜 앞에 나온 단어 (많은 순):")
        out += [f"  {word}  x{n}" for word, n in counts.most_common(15)]
        out.append(
            "마감을 뜻하는 단어는 app/crawler/deadline_keywords.py 의 DEADLINE_CLUES에 추가하세요."
        )
    return "\n".join(out)


def load_missing(
    factory: sessionmaker[Session],
    *,
    days: int,
    limit: int,
    today: date | None = None,
    missing: str = "deadline",
) -> list[Notice]:
    """Newest notices posted within `days` that have no deadline (`missing`: "deadline"), no event
    date ("event") or lack either ("either"), checked or not.

    The database does not say whether an empty value is "no such date" or "several, so none was
    kept", so the pages of these notices are read again to find out.
    """
    today = today or now_kst().date()
    no_deadline, no_event = Article.deadline_at.is_(None), Article.event_at.is_(None)
    lacks = {"deadline": no_deadline, "event": no_event, "either": or_(no_deadline, no_event)}
    with factory() as session:
        rows = session.execute(
            select(Article.article_no, Article.title, Article.posted_on)
            .where(lacks[missing], Article.posted_on >= today - timedelta(days=days))
            .order_by(Article.posted_on.desc(), Article.id.desc())
            .limit(limit)
        ).all()
    return [Notice(r.article_no, r.title, r.posted_on) for r in rows]


def run_audit(notices: Sequence[Notice], source: DetailSource) -> list[NoticeAudit]:
    audits: list[NoticeAudit] = []
    for notice in notices:
        try:
            audits.append(audit_notice(notice, parse_detail(source.detail_page(notice.article_no))))
        except (FetchError, ParseError) as exc:
            audits.append(NoticeAudit(notice, error=str(exc)))
    return audits


def visible(audits: Sequence[NoticeAudit], *, ambiguous_only: bool) -> list[NoticeAudit]:
    """What to print one by one: with `ambiguous_only` just the flagged ones, and any failure."""
    return [a for a in audits if not ambiguous_only or a.ambiguous or a.error]


def report(audits: Sequence[NoticeAudit], *, ambiguous_only: bool = False) -> None:
    for a in visible(audits, ambiguous_only=ambiguous_only):
        print(render(a))
    print(summarize(audits))  # always over every notice read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.audit_deadlines", description=__doc__)
    parser.add_argument("--days", type=int, default=60, help="posted within N days (default 60)")
    parser.add_argument("--limit", type=int, default=20, help=f"at most N (max {MAX_LIMIT})")
    parser.add_argument(
        "--missing",
        choices=MISSING_CHOICES,
        default="deadline",
        help="read notices that lack a deadline (default), an event date, or either (database)",
    )
    parser.add_argument(
        "--ambiguous-only",
        action="store_true",
        help="print only notices whose deadline or event date was emptied as ambiguous "
        "(and failures); the summary still counts every notice read",
    )
    parser.add_argument("--article-no", type=int, nargs="+", help="these notices, no database")
    parser.add_argument("--html", type=Path, nargs="+", help="saved view pages, no network")
    parser.add_argument(
        "--posted",
        type=date.fromisoformat,
        help="posting date (YYYY-MM-DD) for --article-no/--html",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = get_crawler_settings()
    posted = args.posted or now_kst().date()
    limit = max(1, min(args.limit, MAX_LIMIT))

    if args.html:
        audits = [
            audit_notice(Notice(0, path.name, posted), parse_detail(path.read_text("utf-8")))
            for path in args.html
        ]
        report(audits, ambiguous_only=args.ambiguous_only)
        return 0

    try:
        with Fetcher(
            user_agent=settings.crawl_user_agent, delay=settings.crawl_delay_seconds
        ) as fetcher:
            if args.article_no:
                notices = [Notice(no, f"(article {no})", posted) for no in args.article_no[:limit]]
            else:
                engine = make_engine(settings.sqlalchemy_url)
                try:
                    notices = load_missing(
                        make_sessionmaker(engine),
                        days=args.days,
                        limit=limit,
                        missing=args.missing,
                    )
                finally:
                    engine.dispose()
            print(f"{len(notices)}건을 읽습니다 (글마다 {settings.crawl_delay_seconds:g}초 간격)\n")
            audits = run_audit(notices, fetcher)
    except RuntimeError as exc:  # DATABASE_URL missing
        print(f"error: {exc}", file=sys.stderr)
        return 1
    report(audits, ambiguous_only=args.ambiguous_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
