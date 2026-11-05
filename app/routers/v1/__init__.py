from fastapi import APIRouter

from app.routers.v1 import (
    articles,
    auth,
    calendar,
    categories,
    departments,
    google_auth,
    me,
    writers,
)

router = APIRouter()
router.include_router(articles.router)
router.include_router(categories.router)
router.include_router(departments.router)
router.include_router(writers.router)
router.include_router(calendar.router)
router.include_router(auth.router)
router.include_router(google_auth.router)
router.include_router(me.router)
