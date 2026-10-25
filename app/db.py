from collections.abc import Awaitable, Callable, Iterator

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    """Create the (lazy) engine. Nothing connects until the first query.

    The DB lives on another machine, so:
    - pool_pre_ping drops connections that died while the DB restarted,
    - connect_timeout stays below the readiness probe timeout (3s) so /readyz answers 503
      instead of hanging.
    """
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        connect_args={"connect_timeout": 2},
    )


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def database_check(engine: Engine) -> Callable[[], Awaitable[None]]:
    """Readiness check for /readyz. Its name is what the probe reports on failure."""

    def ping() -> None:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    async def database() -> None:
        await run_in_threadpool(ping)

    return database


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    factory: sessionmaker[Session] | None = request.app.state.sessionmaker
    if factory is None:
        raise HTTPException(status_code=503, detail="데이터베이스가 설정되지 않았습니다.")
    with factory() as session:
        yield session
