"""Response/request bodies of the v1 API. They mirror `lib/api/types.ts` of the web app 1:1."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_serializer

from app.models import Article
from app.timeutil import KST

SortKey = Literal["posted_desc", "posted_asc", "views_desc", "deadline_asc"]
PinnedFilter = Literal["include", "exclude", "only"]


class ArticleOut(BaseModel):
    id: int
    article_no: int
    title: str
    writer: str
    posted_on: date
    display_no: int | None
    is_pinned: bool
    view_count: int | None
    url: str
    categories: list[str]
    deadline_at: datetime | None

    @field_serializer("deadline_at")
    def _as_seoul_iso(self, value: datetime | None) -> str | None:
        # The DB hands back timestamptz in the session zone (UTC); the contract is `+09:00`.
        return value.astimezone(KST).isoformat(timespec="seconds") if value else None

    @classmethod
    def from_model(cls, a: Article) -> "ArticleOut":
        return cls(
            id=a.id,
            article_no=a.article_no,
            title=a.title,
            writer=a.writer,
            posted_on=a.posted_on,
            display_no=a.display_no,
            is_pinned=a.is_pinned,
            view_count=a.view_count,
            url=a.url,
            categories=[c.code for c in a.categories],
            deadline_at=a.deadline_at,
        )


class ArticleListResponse(BaseModel):
    items: list[ArticleOut]
    total: int
    page: int
    size: int
    has_next: bool


class CategoryOut(BaseModel):
    code: str
    name: str
    count: int


class WriterOut(BaseModel):
    writer: str
    count: int


class LookupRequest(BaseModel):
    ids: list[int]


class LookupResponse(BaseModel):
    items: list[ArticleOut]
    missing_ids: list[int]
