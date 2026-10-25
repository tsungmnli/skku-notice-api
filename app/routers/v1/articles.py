from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import queries
from app.db import get_session
from app.queries import MAX_SIZE, ArticleFilters
from app.schemas import (
    ArticleListResponse,
    ArticleOut,
    LookupRequest,
    LookupResponse,
    PinnedFilter,
    SortKey,
)
from app.timeutil import get_now

router = APIRouter(tags=["articles"])

MAX_PAGE = 100_000  # keeps OFFSET far from integer overflow


@router.get("/articles", response_model=ArticleListResponse)
def list_articles(
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
    q: Annotated[str | None, Query(max_length=100)] = None,
    category: Annotated[list[str] | None, Query()] = None,
    writer: Annotated[list[str] | None, Query()] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    pinned: PinnedFilter = "include",
    has_deadline: bool | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    sort: SortKey = "posted_desc",
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    size: int = 20,
) -> ArticleListResponse:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from은 date_to보다 이후일 수 없습니다.")
    if deadline_from and deadline_to and deadline_from > deadline_to:
        raise HTTPException(422, "deadline_from은 deadline_to보다 이후일 수 없습니다.")

    size = min(MAX_SIZE, max(1, size))
    filters = ArticleFilters(
        q=q,
        categories=category or [],
        writers=writer or [],
        date_from=date_from,
        date_to=date_to,
        pinned=pinned,
        has_deadline=has_deadline,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        sort=sort,
    )
    rows, total = queries.list_articles(session, filters, page=page, size=size, now=now)
    return ArticleListResponse(
        items=[ArticleOut.from_model(a) for a in rows],
        total=total,
        page=page,
        size=size,
        has_next=(page - 1) * size + size < total,
    )


# Declared before `/articles/{article_id}` for readability; the methods differ, so no clash.
@router.post("/articles/lookup", response_model=LookupResponse)
def lookup_articles(
    body: LookupRequest, session: Annotated[Session, Depends(get_session)]
) -> LookupResponse:
    if not 1 <= len(body.ids) <= 200:
        raise HTTPException(422, "조회할 ID는 1~200개까지 가능합니다.")

    # dict.fromkeys: de-duplicate while keeping the request order.
    ordered = list(dict.fromkeys(body.ids))
    found = queries.get_articles_by_id(session, ordered)
    return LookupResponse(
        items=[ArticleOut.from_model(found[i]) for i in ordered if i in found],
        missing_ids=[i for i in ordered if i not in found],
    )


@router.get("/articles/{article_id}", response_model=ArticleOut)
def get_article(article_id: int, session: Annotated[Session, Depends(get_session)]) -> ArticleOut:
    article = queries.get_article(session, article_id)
    if article is None:
        raise HTTPException(404, "게시글을 찾을 수 없습니다.")
    return ArticleOut.from_model(article)
