"""Response/request bodies of the v1 API. They mirror `lib/api/types.ts` of the web app 1:1."""

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_serializer

from app.models import Article
from app.timeutil import KST

SortKey = Literal["posted_desc", "posted_asc", "views_desc", "deadline_asc"]
PinnedFilter = Literal["include", "exclude", "only"]
DeadlineSource = Literal["title", "body"]


class ArticleOut(BaseModel):
    id: int
    article_no: int
    # The board the post is on (`Source.slug`, the same value as `sources[].slug` of
    # /departments) and the department that board belongs to (`Department.code`). Campus-wide
    # posts say `skku` for both.
    source: str
    department: str
    title: str
    writer: str
    posted_on: date
    display_no: int | None
    is_pinned: bool
    view_count: int | None
    url: str
    categories: list[str]
    deadline_at: datetime | None
    # When the event itself happens (seminar, shift). Not a deadline: never sorted or filtered on.
    event_at: datetime | None
    # Where `deadline_at` was read from. The stored evidence line is for debugging, not served.
    deadline_source: DeadlineSource | None
    # When the crawler saw the post's title change (UPDATED badge). null = never seen changed.
    content_changed_at: datetime | None

    @field_serializer("deadline_at")
    def _deadline_as_seoul_iso(self, value: datetime | None) -> str | None:
        # The DB hands back timestamptz in the session zone (UTC); the contract is `+09:00`.
        # A deadline without a time of day keeps its implicit 23:59 (due at the end of that day).
        return value.astimezone(KST).isoformat(timespec="seconds") if value else None

    @field_serializer("content_changed_at")
    def _changed_as_seoul_iso(self, value: datetime | None) -> str | None:
        return value.astimezone(KST).isoformat(timespec="seconds") if value else None

    @field_serializer("event_at")
    def _event_as_seoul_iso(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        local = value.astimezone(KST)
        # 23:59 is how the data marks "no time of day". An event has no end-of-day moment, so it
        # goes out as a bare date (`2026-11-20`) instead of a made-up 23:59.
        if (local.hour, local.minute) == (23, 59):
            return local.date().isoformat()
        return local.isoformat(timespec="seconds")

    @classmethod
    def from_model(cls, a: Article) -> "ArticleOut":
        return cls(
            id=a.id,
            article_no=a.article_no,
            source=a.source.slug,
            department=a.source.department_code,
            title=a.title,
            writer=a.writer,
            posted_on=a.posted_on,
            display_no=a.display_no,
            is_pinned=a.is_pinned,
            view_count=a.view_count,
            url=a.url,
            categories=[c.code for c in a.categories],
            deadline_at=a.deadline_at,
            event_at=a.event_at,
            deadline_source=a.deadline_source,
            content_changed_at=a.content_changed_at,
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


class SourceOut(BaseModel):
    slug: str
    name: str
    level: Literal["all", "undergrad", "grad"]
    count: int


class DepartmentOut(BaseModel):
    code: str
    name: str
    count: int  # articles on all of its boards
    sources: list[SourceOut]


class WriterOut(BaseModel):
    writer: str
    count: int


MAX_READS_PER_REQUEST = 200


class ReadMark(BaseModel):
    article_id: int = Field(ge=1, le=2**31 - 1)
    # When the user opened it. Optional: the web sends it when it uploads reads it kept while
    # signed out, which happened earlier than this request. Never later than the server's now.
    read_at: AwareDatetime | None = None


class ReadsIn(BaseModel):
    items: list[ReadMark] = Field(max_length=MAX_READS_PER_REQUEST)


class ReadOut(BaseModel):
    article_id: int
    read_at: datetime

    @field_serializer("read_at")
    def _read_as_seoul_iso(self, value: datetime) -> str:
        return value.astimezone(KST).isoformat(timespec="seconds")


class ReadsOut(BaseModel):
    items: list[ReadOut]


class ReadsSaved(BaseModel):
    # How many of the sent ids were accepted. Ids that do not exist, or whose notice is already
    # past its display window, are skipped without an error.
    saved: int


class LookupRequest(BaseModel):
    ids: list[int]


class LookupResponse(BaseModel):
    items: list[ArticleOut]
    missing_ids: list[int]
