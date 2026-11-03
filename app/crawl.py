"""Crawl the school board: `python -m app.crawl [--pages N] [--dry-run]`.

This is the CronJob command. Exit code 0 = stored, 1 = failed (the Job's retry policy applies).
`--dry-run` fetches and parses but never touches the database, so it needs no DATABASE_URL
(view pages are read only in a real run: which notices need one is known from the database).
"""

import argparse
import logging
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.auth.cleanup import purge_expired
from app.crawler.config import get_crawler_settings
from app.crawler.details import read_details
from app.crawler.fetch import Fetcher, FetchError
from app.crawler.parser import ParseError
from app.crawler.run import CrawlError, crawl, fetch_articles, with_deadlines
from app.db import make_engine, make_sessionmaker
from app.timeutil import now_kst

logger = logging.getLogger("app.crawl")


def purge_account_rows(factory) -> None:
    """Drop expired sessions and spent e-mail tokens. Housekeeping only: if it fails the crawl
    still counts as done, and the next run tries again."""
    try:
        with factory() as session, session.begin():
            purged = purge_expired(session, now=now_kst())
    except SQLAlchemyError:
        logger.warning("account cleanup failed", exc_info=True)
        return
    logger.info(
        "cleanup: sessions=%d email_tokens=%d removed", purged.sessions, purged.email_tokens
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.crawl", description=__doc__)
    parser.add_argument("--pages", type=int, help="list pages to read (default: CRAWL_MAX_PAGES)")
    parser.add_argument("--dry-run", action="store_true", help="print what would be stored")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = get_crawler_settings()
    pages = args.pages or settings.crawl_max_pages

    try:
        with Fetcher(
            user_agent=settings.crawl_user_agent, delay=settings.crawl_delay_seconds
        ) as fetcher:
            if args.dry_run:
                items = fetch_articles(fetcher, pages)
                deadlines = with_deadlines(items)
                for it in items:
                    flag = "PIN" if it.is_pinned else "   "
                    dl = deadlines[it.article_no]
                    print(
                        f"{flag} {it.article_no} {it.posted_on} {it.category_code:<11} "
                        f"{dl.isoformat() if dl else '-':<25} {it.title}"
                    )
                logger.info("dry run: %d articles, nothing written", len(items))
                return 0

            engine = make_engine(settings.sqlalchemy_url)
            try:
                factory = make_sessionmaker(engine)
                result, seen = crawl(factory, fetcher, max_pages=pages)
                details = read_details(
                    factory,
                    fetcher,
                    limit=settings.crawl_detail_limit,
                    max_age_days=settings.crawl_detail_max_age_days,
                )
                purge_account_rows(factory)
            finally:
                engine.dispose()
    except (FetchError, ParseError, CrawlError) as exc:
        logger.error("crawl failed: %s", exc)
        return 1

    logger.info(
        "done: seen=%d inserted=%d updated=%d unchanged=%d unpinned=%d",
        seen,
        result.inserted,
        result.updated,
        result.unchanged,
        result.unpinned,
    )
    logger.info(
        "details: checked=%d deadlines=%d events=%d failed=%d%s",
        details.checked,
        details.deadlines,
        details.events,
        details.failed,
        " (gave up: the school's server keeps failing)" if details.gave_up else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
