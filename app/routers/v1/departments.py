from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import queries
from app.cache import public_cache
from app.db import get_session
from app.schemas import DepartmentOut, SourceOut

router = APIRouter(tags=["departments"])


@router.get(
    "/departments", response_model=list[DepartmentOut], dependencies=[Depends(public_cache)]
)
def list_departments(session: Annotated[Session, Depends(get_session)]) -> list[DepartmentOut]:
    return [
        DepartmentOut(
            code=d.code,
            name=d.name,
            count=d.count,
            sources=[
                SourceOut(slug=s.slug, name=s.name, level=s.level, count=s.count) for s in d.sources
            ],
        )
        for d in queries.list_departments(session)
    ]
