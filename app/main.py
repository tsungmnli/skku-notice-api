from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import API_PREFIX, Settings, get_settings
from app.db import database_check, make_engine, make_sessionmaker
from app.routers import health, v1

ReadinessCheck = Callable[[], Awaitable[None]]


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Collapse FastAPI's error list into the contract's `{"detail": "<message>"}`.

    The web client shows the response body to the user as-is, so it must be one string.
    """
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"] if p not in ("query", "body", "path"))
    where = f" ({field})" if field else ""
    return JSONResponse(
        status_code=422,
        content={"detail": f"요청 값이 올바르지 않습니다{where}: {first['msg']}"},
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    # The engine is lazy (no connection until the first query), so building it is cheap
    # and works without a database, as in the unit tests.
    engine = make_engine(settings.sqlalchemy_url) if settings.database_url else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="SKKU Notice API",
        docs_url=f"{API_PREFIX}/docs" if settings.docs_on else None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json" if settings.docs_on else None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine) if engine else None
    # Dependencies register an async check here; /readyz runs them all.
    app.state.readiness_checks = []  # list[ReadinessCheck]
    if engine is not None:
        app.state.readiness_checks.append(database_check(engine))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(v1.router, prefix=f"{API_PREFIX}/v1")
    return app
