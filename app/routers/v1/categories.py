from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import queries
from app.cache import public_cache
from app.db import get_session
from app.schemas import CategoryOut

router = APIRouter(tags=["categories"])


@router.get("/categories", response_model=list[CategoryOut], dependencies=[Depends(public_cache)])
def list_categories(
    session: Annotated[Session, Depends(get_session)],
    department: Annotated[list[str] | None, Query()] = None,
    source: Annotated[list[str] | None, Query()] = None,
) -> list[CategoryOut]:
    return [
        CategoryOut(code=code, name=name, count=count)
        for code, name, count in queries.list_categories(session, department or [], source or [])
    ]
