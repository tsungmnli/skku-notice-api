import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.auth.errors import AuthError, auth_error_handler
from app.auth.google import build_oauth
from app.auth.limiter import RateLimiter
from app.auth.mail import Mailer
from app.config import API_PREFIX, Settings, get_settings
from app.db import database_check, make_engine, make_sessionmaker
from app.middleware import (
    BodyLimitMiddleware,
    NoStoreMiddleware,
    OriginCheckMiddleware,
    web_origins,
)
from app.routers import health, v1

ReadinessCheck = Callable[[], Awaitable[None]]


def configure_logging() -> None:
    """Show the app's own INFO logs. uvicorn only sets up its own loggers, so without this the
    dev mailer's "mail not sent" line (the only place the verify/reset link appears when
    SMTP_HOST is unset) is silently dropped. Safe to call repeatedly (one handler)."""
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)


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
    configure_logging()
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
    # Per app (not module globals), so tests never share rate-limit counters or a mailer.
    app.state.limiter = RateLimiter()
    app.state.mailer = Mailer(settings)
    app.state.oauth = build_oauth(settings)  # None unless Google sign-in is configured
    if engine is not None:
        app.state.readiness_checks.append(database_check(engine))

    # Each add_middleware wraps the ones added before it: the body limit sits innermost, CORS
    # outside the rest so that even our 403/413 answers carry the CORS headers.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(
        OriginCheckMiddleware,
        allowed_origins=web_origins(settings.cors_origins, settings.public_url),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the web app sends the `sid` cookie
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Client"],
        expose_headers=["Retry-After"],
    )
    if app.state.oauth is not None:
        # Holds only the short-lived OAuth state/nonce during the Google round trip; the
        # login itself is the separate `sid` cookie. Scoped to the Google routes.
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_secret,
            session_cookie="oauth_state",
            path=f"{API_PREFIX}/v1/auth/google",
            max_age=600,
            same_site="lax",
            https_only=settings.app_env == "prod",
        )
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(AuthError, auth_error_handler)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(v1.router, prefix=f"{API_PREFIX}/v1")
    return app
