from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app import queries
from app.db import get_session
from app.ics import build_calendar

router = APIRouter(tags=["calendar"])

MAX_IDS = 100


# Calendar apps probe with HEAD before (or instead of) GET, so both are served; HEAD stays out
# of the OpenAPI schema (it would repeat GET's operation id).
@router.head("/calendar", response_class=Response, include_in_schema=False)
@router.get(
    "/calendar",
    response_class=Response,
    responses={200: {"content": {"text/calendar": {}}}},
)
def calendar_feed(
    session: Annotated[Session, Depends(get_session)],
    ids: Annotated[list[int] | None, Query(alias="id")] = None,
) -> Response:
    """ICS feed: per requested article a `[마감]` event (deadline) and a `[행사]` event (event day).

    Subscribed to by Google Calendar & co., which fetch the URL themselves.
    """
    if not ids or len(ids) > MAX_IDS:
        raise HTTPException(422, f"조회할 ID는 1~{MAX_IDS}개까지 가능합니다.")

    found = queries.get_articles_by_id(session, ids).values()
    dated = sorted(
        (a for a in found if a.deadline_at is not None or a.event_at is not None),
        key=lambda a: (min(d for d in (a.deadline_at, a.event_at) if d is not None), a.id),
    )
    return Response(
        build_calendar(dated),
        media_type="text/calendar",
        headers={
            # Short, so a new deadline shows up soon, but enough to absorb a client's retries.
            "Cache-Control": "public, max-age=300",
            "Content-Disposition": 'inline; filename="skku-notice.ics"',
        },
    )
