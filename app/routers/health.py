import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)


@router.get("/healthz")
def healthz() -> dict[str, bool]:
    """Liveness: the process is up. Must not touch any dependency."""
    return {"ok": True}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: every registered dependency check passes."""
    failed: list[str] = []
    for check in request.app.state.readiness_checks:
        try:
            await check()
        except Exception:
            logger.exception("readiness check failed: %s", check.__name__)
            failed.append(check.__name__)
    if failed:
        return JSONResponse(status_code=503, content={"ok": False, "failed": failed})
    return JSONResponse(content={"ok": True})
