from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import queries
from app.db import get_session
from app.schemas import CategoryOut

router = APIRouter(tags=["categories"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(session: Annotated[Session, Depends(get_session)]) -> list[CategoryOut]:
    return [
        CategoryOut(code=code, name=name, count=count)
        for code, name, count in queries.list_categories(session)
    ]
