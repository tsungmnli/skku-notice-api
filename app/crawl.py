"""Crawl the school's boards: `python -m app.crawl [--pages N] [--source SLUG] [--dry-run]`.

This is the CronJob command. It reads every enabled board in turn (the `sources` table), then the
view pages of notices that need one. A board that fails does not stop the others; the exit code
says whether all went well: 0 = every board stored, 1 = at least one failed or was skipped (the
Job's retry policy applies), 2 = `--source` named a board that does not exist or is disabled.
`--dry-run` fetches and parses but never touches the database, so it needs no DATABASE_URL: it
reads the boards of the registry (app/crawler/sources.py) instead (view pages are read only in a
real run: which notices need one is known from the database).

`--source SLUG` reads one board only (and only its view pages), e.g. to backfill a new board.

`--sync-sources` copies the board registry (app/crawler/sources.py) into the database and exits;
it never touches the school's servers. Run it after adding a board.

`--incremental` stops paging at the first list page with nothing new (one request on a quiet
day); `--no-incremental` forces the full walk. Without either, CRAWL_INCREMENTAL decides.
"""

import argparse
import logging
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.auth.cleanup import purge_expired, purge_stale_reads
from app.crawler.config import get_crawler_settings
from app.crawler.details import read_details
from app.crawler.fetch import Fetcher
from app.crawler.run import (
    Board,
    BoardFailure,
    UnknownSource,
    crawl,
    fetch_articles,
    load_boards,
    registry_boards,
    run_each,
    with_deadlines,
)
from app.crawler.sources import sync_sources
from app.crawler.store import StoreResult
from app.db import make_engine, make_sessionmaker
from app.timeutil import now_kst

logger = logging.getLogger("app.crawl")


def purge_account_rows(factory) -> None:
    """Drop expired sessions, spent e-mail tokens and stale read marks. Housekeeping only: if it
    fails the crawl still counts as done, and the next run tries again."""
    try:
        with factory() as session, session.begin():
            purged = purge_expired(session, now=now_kst())
    except SQLAlchemyError:
        logger.warning("account cleanup failed", exc_info=True)
    else:
        logger.info(
            "cleanup: sessions=%d email_tokens=%d removed", purged.sessions, purged.email_tokens
        )
    # Its own transaction: a failure above must not stop this, nor the other way round.
    try:
        with factory() as session, session.begin():
            stale_reads = purge_stale_reads(session, now=now_kst())
    except SQLAlchemyError:
        logger.warning("read-mark cleanup failed", exc_info=True)
    else:
        logger.info("cleanup: stale read marks=%d removed", stale_reads)


def run_sync_sources(database_url: str) -> int:
    engine = make_engine(database_url)
    try:
        with make_sessionmaker(engine)() as session, session.begin():
            result = sync_sources(session)
    except SQLAlchemyError as exc:
        logger.error("source sync failed: %s", exc)
        return 1
    finally:
        engine.dispose()
    logger.info("synced %d departments, %d sources", result.departments, result.sources)
    return 0


def run_dry(fetcher: Fetcher, boards: list[Board], pages: int) -> list[BoardFailure]:
    """Print what each board would store. Returns the boards that could not be read."""

    def show(board: Board) -> int:
        items = fetch_articles(fetcher.board(board.list_url), pages, list_url=board.list_url)
        deadlines = with_deadlines(items)
        for it in items:
            flag = "PIN" if it.is_pinned else "   "
            dl = deadlines[it.article_no]
            print(
                f"{board.slug:<14} {flag} {it.article_no} {it.posted_on} {it.category_code:<11} "
                f"{dl.isoformat() if dl else '-':<25} {it.title}"
            )
        logger.info("board %s: %d articles, nothing written", board.slug, len(items))
        return len(items)

    _, failed = run_each(boards, show)
    return failed


def crawl_boards(
    factory, fetcher: Fetcher, boards: list[Board], *, pages: int, incremental: bool
) -> tuple[StoreResult, int, list[BoardFailure]]:
    """Crawl each board and add up the results. Returns (totals, articles seen, failures)."""

    def one(board: Board) -> tuple[StoreResult, int]:
        result, seen = crawl(
            factory,
            fetcher.board(board.list_url),
            max_pages=pages,
            incremental=incremental,
            source_id=board.id,
            list_url=board.list_url,
        )
        logger.info(
            "board %s: seen=%d inserted=%d updated=%d unchanged=%d unpinned=%d",
            board.slug,
            seen,
            result.inserted,
            result.updated,
            result.unchanged,
            result.unpinned,
        )
        return result, seen

    done, failed = run_each(boards, one)
    totals = StoreResult(
        inserted=sum(r.inserted for _, (r, _) in done),
        updated=sum(r.updated for _, (r, _) in done),
        unchanged=sum(r.unchanged for _, (r, _) in done),
        unpinned=sum(r.unpinned for _, (r, _) in done),
    )
    return totals, sum(seen for _, (_, seen) in done), failed


def report_failures(boards: list[Board], failed: list[BoardFailure]) -> None:
    if failed:
        logger.error(
            "%d of %d boards failed: %s",
            len(failed),
            len(boards),
            ", ".join(f.slug for f in failed),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crawl", description=__doc__)
    parser.add_argument("--pages", type=int, help="list pages to read (default: CRAWL_MAX_PAGES)")
    parser.add_argument(
        "--source",
        metavar="SLUG",
        help="read only this board, e.g. sw or sco-data (default: every enabled board)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would be stored")
    parser.add_argument(
        "--incremental",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="stop at the first list page with nothing new (default: CRAWL_INCREMENTAL)",
    )
    parser.add_argument(
        "--sync-sources",
        action="store_true",
        help="copy the board registry into the database and exit (no crawling)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_crawler_settings()
    if args.sync_sources:
        return run_sync_sources(settings.sqlalchemy_url)
    pages = args.pages or settings.crawl_max_pages
    incremental = settings.crawl_incremental if args.incremental is None else args.incremental

    with Fetcher(
        user_agent=settings.crawl_user_agent,
        delay=settings.crawl_delay_seconds,
        jitter=settings.crawl_jitter,
        max_requests=settings.crawl_max_requests,
    ) as fetcher:
        if args.dry_run:
            try:
                boards = registry_boards(args.source)
            except UnknownSource as exc:
                logger.error("%s", exc)
                return 2
            with fetcher.budget(settings.crawl_list_requests):
                failed = run_dry(fetcher, boards, pages)
            report_failures(boards, failed)
            return 1 if failed else 0

        engine = make_engine(settings.sqlalchemy_url)
        try:
            factory = make_sessionmaker(engine)
            try:
                boards = load_boards(factory, args.source)
            except UnknownSource as exc:
                logger.error("%s (is the registry synced? see --sync-sources)", exc)
                return 2
            except SQLAlchemyError as exc:
                logger.error("could not read the board list: %s", exc)
                return 1
            if not boards:
                logger.error("no enabled boards in the database (run --sync-sources first)")
                return 1

            # The list pages get their own allowance; the view pages get the rest of the run's cap.
            with fetcher.budget(settings.crawl_list_requests):
                totals, seen, failed = crawl_boards(
                    factory, fetcher, boards, pages=pages, incremental=incremental
                )
            details = read_details(
                factory,
                fetcher,
                limit=settings.crawl_detail_limit,
                max_age_days=settings.crawl_detail_max_age_days,
                source_id=boards[0].id if args.source else None,
            )
            purge_account_rows(factory)
        finally:
            engine.dispose()

    report_failures(boards, failed)
    logger.info(
        "done: boards=%d ok=%d failed=%d seen=%d inserted=%d updated=%d unchanged=%d unpinned=%d",
        len(boards),
        len(boards) - len(failed),
        len(failed),
        seen,
        totals.inserted,
        totals.updated,
        totals.unchanged,
        totals.unpinned,
    )
    note = ""
    if details.gave_up:
        note = " (gave up: the school's server keeps failing)"
    elif details.budget_hit:
        note = " (stopped: request budget used up)"
    logger.info(
        "details: checked=%d deadlines=%d events=%d failed=%d%s",
        details.checked,
        details.deadlines,
        details.events,
        details.failed,
        note,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
