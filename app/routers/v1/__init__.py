from fastapi import APIRouter

from app.routers.v1 import articles, calendar, categories, writers

router = APIRouter()
router.include_router(articles.router)
router.include_router(categories.router)
router.include_router(writers.router)
router.include_router(calendar.router)
