from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import queries
from app.db import get_session
from app.schemas import WriterOut

router = APIRouter(tags=["writers"])

MAX_LIMIT = 200


@router.get("/writers", response_model=list[WriterOut])
def list_writers(
    session: Annotated[Session, Depends(get_session)],
    q: str | None = None,
    limit: int = MAX_LIMIT,
) -> list[WriterOut]:
    limit = min(MAX_LIMIT, max(1, limit))
    rows = queries.list_writers(session, q=(q or "").strip() or None, limit=limit)
    return [WriterOut(writer=writer, count=count) for writer, count in rows]
