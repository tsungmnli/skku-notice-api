"""Read queries behind the v1 endpoints. Pure functions of (session, arguments); no HTTP here."""

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session

from app.models import Article, Category, article_categories
from app.schemas import PinnedFilter, SortKey
from app.timeutil import KST, kst_day_start, kst_next_day_start

INT4_MAX = 2**31 - 1  # `articles.id` is a 32-bit integer; anything larger cannot exist.
MAX_SIZE = 50


@dataclass(frozen=True)
class ArticleFilters:
    q: str | None = None
    categories: list[str] = field(default_factory=list)
    writers: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None
    pinned: PinnedFilter = "include"
    has_deadline: bool | None = None
    deadline_from: date | None = None
    deadline_to: date | None = None
    sort: SortKey = "posted_desc"


def like_pattern(term: str) -> str:
    """`%term%` with LIKE wildcards in the term escaped (use with escape="\\")."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _term_matches(term: str) -> ColumnElement[bool]:
    pattern = like_pattern(term)
    category_name = (
        select(1)
        .select_from(
            article_categories.join(Category, Category.code == article_categories.c.category_code)
        )
        .where(article_categories.c.article_id == Article.id, Category.name.ilike(pattern, "\\"))
        .exists()
    )
    return or_(
        Article.title.ilike(pattern, "\\"),
        Article.writer.ilike(pattern, "\\"),
        category_name,
    )


def _conditions(f: ArticleFilters, now: datetime) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []

    # Every whitespace-separated word must hit the title, the writer or a category name.
    for term in (f.q or "").split():
        conds.append(_term_matches(term))

    if f.categories:
        conds.append(
            Article.id.in_(
                select(article_categories.c.article_id).where(
                    article_categories.c.category_code.in_(f.categories)
                )
            )
        )
    if f.writers:
        conds.append(Article.writer.in_(f.writers))
    if f.date_from:
        conds.append(Article.posted_on >= f.date_from)
    if f.date_to:
        conds.append(Article.posted_on <= f.date_to)

    if f.pinned == "only":
        conds.append(Article.is_pinned.is_(True))
    elif f.pinned == "exclude":
        conds.append(Article.is_pinned.is_(False))

    if f.has_deadline is True:
        conds.append(Article.deadline_at.is_not(None))
    elif f.has_deadline is False:
        conds.append(Article.deadline_at.is_(None))

    # The deadline *date* in Seoul: [00:00 of from, 00:00 of the day after to).
    # Comparing instants (not casting the column) keeps the partial index usable.
    ranged = f.deadline_from is not None or f.deadline_to is not None
    if f.deadline_from:
        conds.append(Article.deadline_at >= kst_day_start(f.deadline_from))
    if f.deadline_to:
        upper = kst_next_day_start(f.deadline_to)
        if upper is not None:
            conds.append(Article.deadline_at < upper)
    if ranged:
        conds.append(Article.deadline_at.is_not(None))

    if f.sort == "deadline_asc":
        conds.append(Article.deadline_at.is_not(None))
        if not ranged:
            # Hide deadlines that already passed (Seoul "today" 00:00); an explicit range wins.
            conds.append(Article.deadline_at >= kst_day_start(now.astimezone(KST).date()))

    return conds


def _order_by(sort: SortKey) -> list[ColumnElement]:
    # Every order ends in `id` so page boundaries never shuffle rows.
    match sort:
        case "posted_asc":
            return [Article.posted_on.asc(), Article.id.asc()]
        case "views_desc":
            return [
                func.coalesce(Article.view_count, 0).desc(),
                Article.posted_on.desc(),
                Article.id.desc(),
            ]
        case "deadline_asc":
            return [Article.deadline_at.asc(), Article.id.asc()]
        case _:
            return [Article.posted_on.desc(), Article.id.desc()]


def list_articles(
    session: Session, f: ArticleFilters, *, page: int, size: int, now: datetime
) -> tuple[list[Article], int]:
    """One page of articles plus the total number of matches."""
    conds = _conditions(f, now)
    total = session.scalar(select(func.count()).select_from(Article).where(*conds)) or 0
    stmt: Select[tuple[Article]] = (
        select(Article)
        .where(*conds)
        .order_by(*_order_by(f.sort))
        .limit(size)
        .offset((page - 1) * size)
    )
    return list(session.scalars(stmt)), total


def get_article(session: Session, article_id: int) -> Article | None:
    if not 1 <= article_id <= INT4_MAX:
        return None
    return session.get(Article, article_id)


def get_articles_by_id(session: Session, ids: list[int]) -> dict[int, Article]:
    valid = {i for i in ids if 1 <= i <= INT4_MAX}
    if not valid:
        return {}
    return {a.id: a for a in session.scalars(select(Article).where(Article.id.in_(valid)))}


def list_categories(session: Session) -> list[tuple[str, str, int]]:
    """(code, name, article count) in display order; categories without articles included."""
    count = func.count(article_categories.c.article_id)
    stmt = (
        select(Category.code, Category.name, count)
        .select_from(Category)
        .outerjoin(article_categories, article_categories.c.category_code == Category.code)
        .group_by(Category.code, Category.name, Category.sort_order)
        .order_by(Category.sort_order, Category.code)
    )
    return [(code, name, n) for code, name, n in session.execute(stmt)]


def list_writers(session: Session, *, q: str | None, limit: int) -> list[tuple[str, int]]:
    count = func.count()
    stmt = select(Article.writer, count).group_by(Article.writer)
    if q:
        stmt = stmt.where(Article.writer.ilike(like_pattern(q), "\\"))
    stmt = stmt.order_by(count.desc(), Article.writer.asc()).limit(limit)
    return [(writer, n) for writer, n in session.execute(stmt)]
