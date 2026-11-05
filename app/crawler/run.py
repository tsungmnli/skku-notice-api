"""One crawl run: fetch the first N list pages, parse, extract deadlines, store.

A *full* run reads all N pages. An *incremental* run (`known` is given) stops after the first page
that holds only posts already stored, which is usually page 1: one request instead of N. What it
gives up: an edited title or a new view count on pages 2..N is only noticed by a full run, so keep
a full run on a slower schedule (see README).
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.deadline import extract_deadline
from app.crawler.fetch import FetchError, ListSource, RequestBudgetExceeded
from app.crawler.parser import BOARD_URL, ListedArticle, ParseError, parse_list
from app.crawler.sources import CAMPUS_SOURCE_ID, SOURCES
from app.crawler.store import (
    StoreResult,
    known_article_nos,
    merge_duplicates,
    upsert_articles,
    upsert_categories,
)
from app.models import Source

logger = logging.getLogger(__name__)

PAGE_SIZE = 10


class CrawlError(RuntimeError):
    """The run cannot be trusted (e.g. the first page had no articles)."""


class UnknownSource(ValueError):
    """`--source` named a board that is not registered, or is not crawled (disabled)."""


@dataclass(frozen=True)
class Board:
    """One board to read in this run. `id` is the `sources` row; 0 in a dry run, which has no
    database and stores nothing."""

    id: int
    slug: str
    list_url: str


@dataclass(frozen=True)
class BoardFailure:
    slug: str
    reason: str


def load_boards(factory: sessionmaker[Session], slug: str | None = None) -> list[Board]:
    """The enabled boards in the database, in the order students see them.

    With `slug`, just that one: it must exist and be enabled, or `UnknownSource` is raised (a
    typo should not look like a quiet run).
    """
    stmt = select(Source.id, Source.slug, Source.list_url).where(Source.enabled.is_(True))
    if slug is not None:
        stmt = stmt.where(Source.slug == slug)
    with factory() as session:
        rows = session.execute(stmt.order_by(Source.sort_order, Source.id)).all()
    if slug is not None and not rows:
        raise UnknownSource(f"no enabled source {slug!r}")
    return [Board(r.id, r.slug, r.list_url) for r in rows]


def registry_boards(slug: str | None = None) -> list[Board]:
    """The same list straight from the registry, for a dry run that has no database."""
    boards = [Board(0, s.slug, s.list_url) for s in SOURCES if s.enabled]
    if slug is not None:
        boards = [b for b in boards if b.slug == slug]
        if not boards:
            raise UnknownSource(f"no enabled source {slug!r}")
    return boards


def run_each[T](
    boards: Sequence[Board], step: Callable[[Board], T]
) -> tuple[list[tuple[Board, T]], list[BoardFailure]]:
    """Run `step` on each board in turn. A board that fails does not stop the others.

    What counts as that board's failure: the school's server (`FetchError`), a page that does not
    read (`ParseError`, `CrawlError`) or a database error. Anything else is a bug and propagates.
    When the request budget runs out, the boards after it are not tried: each is reported as
    skipped, so the run still ends as a failure and the next run picks them up.
    """
    done: list[tuple[Board, T]] = []
    failed: list[BoardFailure] = []
    for i, board in enumerate(boards):
        try:
            done.append((board, step(board)))
        except RequestBudgetExceeded as exc:
            logger.error("board %s: %s; the remaining boards are skipped", board.slug, exc)
            failed.append(BoardFailure(board.slug, str(exc)))
            failed.extend(
                BoardFailure(b.slug, "skipped: request budget used up") for b in boards[i + 1 :]
            )
            break
        except (FetchError, ParseError, CrawlError, SQLAlchemyError) as exc:
            logger.error("board %s failed: %s", board.slug, exc)
            failed.append(BoardFailure(board.slug, str(exc)))
    return done, failed


# Given post numbers, the ones already in the database.
KnownLookup = Callable[[Sequence[int]], set[int]]


def fetch_articles(
    source: ListSource,
    max_pages: int,
    *,
    known: KnownLookup | None = None,
    list_url: str = BOARD_URL,
) -> list[ListedArticle]:
    """Walk list pages from the top. Stops at the first empty page past page 1, and, when `known`
    is given, after the first page that has no post the database does not know yet.

    Pinned posts count like the others: a new pinned notice must not be missed, and one that is
    stored is "known" from the next run on, so it cannot keep the walk going.

    `source` already points at one board; `list_url` is that board's address, used for the
    links of the parsed rows.
    """
    items: list[ListedArticle] = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        rows = parse_list(source.list_page(offset), list_url)
        logger.info("page %d (offset %d): %d articles", page + 1, offset, len(rows))
        if not rows:
            if page == 0:
                # An empty first page means an outage or a changed layout, never "no news".
                raise CrawlError("first list page had no articles")
            break
        items.extend(rows)
        if known is not None and not _has_unknown(rows, known):
            logger.info("page %d has nothing new; stopping early", page + 1)
            break
    return merge_duplicates(items)


def _has_unknown(rows: list[ListedArticle], known: KnownLookup) -> bool:
    numbers = [r.article_no for r in rows]
    return bool(set(numbers) - known(numbers))


def with_deadlines(items: list[ListedArticle]) -> dict[int, datetime | None]:
    return {it.article_no: extract_deadline(it.title, it.posted_on) for it in items}


def crawl(
    factory: sessionmaker[Session],
    source: ListSource,
    *,
    max_pages: int,
    incremental: bool = False,
    source_id: int = CAMPUS_SOURCE_ID,
    list_url: str = BOARD_URL,
) -> tuple[StoreResult, int]:
    """Crawl one board: `source` reads it, `source_id` and `list_url` say which board it is.

    Returns (store result, number of distinct articles seen). One transaction for the run."""

    def known(numbers: Sequence[int]) -> set[int]:
        with factory() as session:
            return known_article_nos(session, numbers, source_id)

    items = fetch_articles(
        source, max_pages, known=known if incremental else None, list_url=list_url
    )
    deadlines = with_deadlines(items)
    with factory() as session, session.begin():
        upsert_categories(session)
        result = upsert_articles(session, items, deadlines, source_id=source_id, sync_pinned=True)
    return result, len(items)
