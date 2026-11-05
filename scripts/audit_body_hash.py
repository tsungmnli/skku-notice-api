"""Would a body hash say "edited" for a post nobody touched? `python -m scripts.audit_body_hash`

Development tool, read-only: it never writes the database. Before the crawler stores a body hash
and lets it drive the UPDATED badge, this measures the false-positive rate on real pages:

* `--twice` reads every page two times in a row. A hash that differs between the two readings
  moved without anyone editing the post (a counter, a stamp, a rotating banner inside the box):
  that is page noise, and it would have lit up UPDATED for nothing.
* `--snapshot FILE` remembers every hash (and the body lines, for the report) in a local file. The
  next run, hours or days later, reports which posts changed and which lines moved. The file
  holds notice text, so keep it out of git; it is for you to look at, not for the server.

Two hashes are compared. `text` is the body as the crawler already reads it; `box` is the whole
write box, which also covers anything else in it (attachment names, if the school puts them
there). "box only" changes are where an attachment swap would show up. Page 1's raw markup of a
post with an attachment is not among the saved test fixtures, so check one such post by hand.

    python -m scripts.audit_body_hash --twice                   # newest 30 of the last 14 days
    python -m scripts.audit_body_hash --snapshot body.json     # first run saves, later runs compare
    python -m scripts.audit_body_hash --snapshot body.json --update   # compare, then save again
    python -m scripts.audit_body_hash --article-no 141478 141483 --twice   # no database
"""

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.config import get_crawler_settings
from app.crawler.fetch import DetailSource, Fetcher, FetchError
from app.crawler.fingerprint import Fingerprint, changed_lines, fingerprint
from app.crawler.parser import ParseError, article_url
from app.db import make_engine, make_sessionmaker
from app.models import Article
from app.timeutil import now_kst
from scripts.audit_deadlines import Notice

MAX_LIMIT = 100  # one run never makes more requests to the school's server than twice this

SAME = "same"
NEW = "new"  # not in the snapshot yet
TEXT_CHANGED = "text"
BOX_ONLY = "box"  # the body text is the same, something else in the write box changed


@dataclass
class Reading:
    notice: Notice
    first: Fingerprint | None = None
    second: Fingerprint | None = None  # only with --twice
    error: str | None = None

    @property
    def noisy_text(self) -> bool:
        """The body hash moved between two back-to-back readings."""
        return (
            self.first is not None
            and self.second is not None
            and self.first.text_hash != self.second.text_hash
        )

    @property
    def noisy_box(self) -> bool:
        """Only the box hash moved: noise outside the body text."""
        return (
            self.first is not None
            and self.second is not None
            and not self.noisy_text
            and self.first.box_hash != self.second.box_hash
        )


def read_one(source: DetailSource, notice: Notice, *, twice: bool) -> Reading:
    reading = Reading(notice)
    try:
        reading.first = fingerprint(source.detail_page(notice.article_no))
        if twice:
            reading.second = fingerprint(source.detail_page(notice.article_no))
    except (FetchError, ParseError) as exc:
        reading.error = str(exc)
    return reading


def entry_of(notice: Notice, fp: Fingerprint) -> dict:
    return {
        "title": notice.title,
        "text_hash": fp.text_hash,
        "box_hash": fp.box_hash,
        "lines": list(fp.lines),
    }


def classify(old: dict | None, fp: Fingerprint) -> str:
    """How a reading compares with its snapshot entry."""
    if old is None:
        return NEW
    if old["text_hash"] != fp.text_hash:
        return TEXT_CHANGED
    if old["box_hash"] != fp.box_hash:
        return BOX_ONLY
    return SAME


def load_snapshot(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8")).get("items", {})


def save_snapshot(path: Path, items: dict[str, dict]) -> None:
    path.write_text(
        json.dumps(
            {"taken_at": now_kst().isoformat(timespec="seconds"), "items": items},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def load_recent(factory: sessionmaker[Session], *, days: int, limit: int) -> list[Notice]:
    since = now_kst().date() - timedelta(days=days)
    with factory() as session:
        rows = session.execute(
            select(Article.article_no, Article.title, Article.posted_on)
            .where(Article.posted_on >= since)
            .order_by(Article.posted_on.desc(), Article.id.desc())
            .limit(limit)
        ).all()
    return [Notice(no, title, posted) for no, title, posted in rows]


def render_twice(readings: Sequence[Reading]) -> str:
    out = []
    for r in readings:
        if r.error:
            out.append(f"#{r.notice.article_no}  ! {r.error}")
        elif r.noisy_text or r.noisy_box:
            kind = "본문" if r.noisy_text else "본문 밖(박스)"
            out.append(f"#{r.notice.article_no}  ⚠ {kind} 해시가 연달아 읽은 두 번 사이에 달라짐")
            out.append(f"  {article_url(r.notice.article_no)}")
            if r.first and r.second:
                out.extend(f"  {line}" for line in changed_lines(r.first.lines, r.second.lines))
    return "\n".join(out)


def render_snapshot(readings: Sequence[Reading], old: dict[str, dict]) -> tuple[str, Counter[str]]:
    counts: Counter[str] = Counter()
    out = []
    for r in readings:
        if r.error or r.first is None:
            counts["error"] += 1
            out.append(f"#{r.notice.article_no}  ! {r.error}")
            continue
        before = old.get(str(r.notice.article_no))
        verdict = classify(before, r.first)
        counts[verdict] += 1
        if verdict in (TEXT_CHANGED, BOX_ONLY):
            label = "본문 변경" if verdict == TEXT_CHANGED else "본문 밖(박스) 변경"
            out.append(f"#{r.notice.article_no}  {r.notice.title}")
            out.append(f"  {label}  {article_url(r.notice.article_no)}")
            assert before is not None
            out.extend(f"  {line}" for line in changed_lines(tuple(before["lines"]), r.first.lines))
    return "\n".join(out), counts


def summarize(readings: Sequence[Reading], snapshot: Counter[str] | None, twice: bool) -> str:
    ok = [r for r in readings if r.first is not None]
    out = [f"읽음 {len(ok)}건, 실패 {len(readings) - len(ok)}건"]
    if twice:
        text = sum(r.noisy_text for r in ok)
        box = sum(r.noisy_box for r in ok)
        pct = f"{100 * (text + box) / len(ok):.0f}%" if ok else "-"
        out.append(f"연달아 읽은 두 번이 다른 글: 본문 {text}건, 본문 밖 {box}건 (오탐 하한 {pct})")
        out.append("  본문 0건이면 본문 해시는 거짓 UPDATED를 만들지 않습니다.")
    if snapshot is not None:
        out.append(
            f"스냅샷과 비교: 그대로 {snapshot[SAME]}건, 본문 변경 {snapshot[TEXT_CHANGED]}건, "
            f"본문 밖 변경 {snapshot[BOX_ONLY]}건, 새 글 {snapshot[NEW]}건"
        )
        out.append("  '본문 변경'을 위 목록에서 눈으로 보고, 진짜 수정인지 잡음인지 세어 보세요.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.audit_body_hash", description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="posted within N days (default 14)")
    parser.add_argument("--limit", type=int, default=30, help=f"at most N posts (max {MAX_LIMIT})")
    parser.add_argument("--article-no", type=int, nargs="+", help="these posts, no database")
    parser.add_argument("--twice", action="store_true", help="read each page twice in a row")
    parser.add_argument("--snapshot", type=Path, help="local file: save on first run, then compare")
    parser.add_argument("--update", action="store_true", help="with --snapshot: then save again")
    parser.add_argument("--posted", type=date.fromisoformat, help="posting date for --article-no")
    args = parser.parse_args(argv)
    if not args.twice and not args.snapshot:
        parser.error("choose --twice, --snapshot, or both")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    settings = get_crawler_settings()
    limit = max(1, min(args.limit, MAX_LIMIT))
    posted = args.posted or now_kst().date()

    try:
        if args.article_no:
            notices = [Notice(no, f"(article {no})", posted) for no in args.article_no[:limit]]
        else:
            engine = make_engine(settings.sqlalchemy_url)
            try:
                notices = load_recent(make_sessionmaker(engine), days=args.days, limit=limit)
            finally:
                engine.dispose()
        per_notice = 2 if args.twice else 1
        print(
            f"{len(notices)}건을 읽습니다 (요청 {len(notices) * per_notice}건 안팎, "
            f"{settings.crawl_delay_seconds:g}초 간격)\n"
        )
        with Fetcher(
            user_agent=settings.crawl_user_agent,
            delay=settings.crawl_delay_seconds,
            jitter=settings.crawl_jitter,
            max_requests=len(notices) * per_notice + 10,
        ) as fetcher:
            readings = [read_one(fetcher, n, twice=args.twice) for n in notices]
    except RuntimeError as exc:  # DATABASE_URL missing, or the request budget
        print(f"error: {exc}", file=sys.stderr)
        return 1

    counts: Counter[str] | None = None
    if args.twice:
        text = render_twice(readings)
        print(text + "\n" if text else "연달아 읽은 두 번이 다른 글은 없습니다.\n")
    if args.snapshot:
        old = load_snapshot(args.snapshot)
        if old:
            text, counts = render_snapshot(readings, old)
            print(text + "\n" if text else "스냅샷과 달라진 글은 없습니다.\n")
        if not old or args.update:
            items = {} if not old else dict(old)
            items.update(
                {str(r.notice.article_no): entry_of(r.notice, r.first) for r in readings if r.first}
            )
            save_snapshot(args.snapshot, items)
            print(f"스냅샷 저장: {args.snapshot} ({len(items)}건)\n")
    print(summarize(readings, counts, args.twice))
    return 0


if __name__ == "__main__":
    sys.exit(main())
