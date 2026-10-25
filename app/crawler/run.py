"""One crawl run: fetch the first N list pages, parse, extract deadlines, store."""

import logging
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.crawler.deadline import extract_deadline
from app.crawler.fetch import ListSource
from app.crawler.parser import ListedArticle, parse_list
from app.crawler.store import StoreResult, merge_duplicates, upsert_articles, upsert_categories

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


class CrawlError(RuntimeError):
    """The run cannot be trusted (e.g. the first page had no articles)."""


def fetch_articles(source: ListSource, max_pages: int) -> list[ListedArticle]:
    """Walk list pages from the top. Stops at the first empty page past page 1."""
    items: list[ListedArticle] = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        rows = parse_list(source.list_page(offset))
        logger.info("page %d (offset %d): %d articles", page + 1, offset, len(rows))
        if not rows:
            if page == 0:
                # An empty first page means an outage or a changed layout, never "no news".
                raise CrawlError("first list page had no articles")
            break
        items.extend(rows)
    return merge_duplicates(items)


def with_deadlines(items: list[ListedArticle]) -> dict[int, datetime | None]:
    return {it.article_no: extract_deadline(it.title, it.posted_on) for it in items}


def crawl(
    factory: sessionmaker[Session], source: ListSource, *, max_pages: int
) -> tuple[StoreResult, int]:
    """Returns (store result, number of distinct articles seen). One transaction for the run."""
    items = fetch_articles(source, max_pages)
    deadlines = with_deadlines(items)
    with factory() as session, session.begin():
        upsert_categories(session)
        result = upsert_articles(session, items, deadlines, sync_pinned=True)
    return result, len(items)
