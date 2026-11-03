"""Small ASGI middlewares: request size cap, `no-store` on account pages, Origin (CSRF) check.

Plain ASGI classes rather than `BaseHTTPMiddleware`, which buffers bodies and breaks streaming.
"""

from collections.abc import Iterable
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.deps import SESSION_COOKIE
from app.config import API_PREFIX

MAX_BODY_BYTES = 64 * 1024
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TOO_LARGE_DETAIL = "요청 본문이 너무 큽니다."
NO_STORE_PREFIXES = (f"{API_PREFIX}/v1/auth", f"{API_PREFIX}/v1/me")


class _TooLarge(HTTPException):
    """Raised from `receive` once a streamed body passes the limit. It is an HTTPException so
    FastAPI's body parsing re-raises it as a 413 instead of turning it into a generic 400."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=TOO_LARGE_DETAIL)


class BodyLimitMiddleware:
    """413 for request bodies over `max_bytes`.

    An honest `Content-Length` is refused before anything is read; a chunked body (no length)
    is cut off as soon as the bytes read pass the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": TOO_LARGE_DETAIL, "code": "payload_too_large"},
            status_code=413,
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


class NoStoreMiddleware:
    """`Cache-Control: no-store` on the account endpoints (a shared cache must never keep one
    person's `/me`). Responses that already set a Cache-Control are left alone, and the public
    calendar feed lives elsewhere so its caching is unaffected."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._applies(scope["path"]):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_header)

    @staticmethod
    def _applies(path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in NO_STORE_PREFIXES)


class OriginCheckMiddleware:
    """CSRF defence for cookie sessions, on top of `SameSite=Lax`.

    For POST/PUT/PATCH/DELETE: if the browser sent an `Origin`, it must be one of ours (the
    configured web origins, or the very host being called). A request that carries the `sid`
    cookie but no `Origin` and no `Authorization` header is refused too: browsers always send
    `Origin` on such requests, so only something unusual leaves it out. Bearer-token requests
    do not depend on ambient cookies and are not subject to CSRF.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str]) -> None:
        self.app = app
        self.allowed = frozenset(o.rstrip("/").lower() for o in allowed_origins if o)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is not None:
            refused = not self._origin_ok(origin, headers.get("host"))
        else:
            has_cookie = SESSION_COOKIE in cookie_parser(headers.get("cookie", ""))
            refused = has_cookie and "authorization" not in headers

        if refused:
            response = JSONResponse(
                {"detail": "허용되지 않은 출처의 요청입니다.", "code": "origin_not_allowed"},
                status_code=403,
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _origin_ok(self, origin: str, host: str | None) -> bool:
        origin = origin.rstrip("/").lower()
        if origin in self.allowed:
            return True
        # Same-origin call (the API docs page, or web and API on one host).
        return bool(host) and urlsplit(origin).netloc == host.lower()


def web_origins(cors_origins: Iterable[str], public_url: str | None) -> list[str]:
    """Origins of the web app: the CORS list plus the public URL's origin."""
    origins = list(cors_origins)
    if public_url:
        parts = urlsplit(public_url)
        if parts.scheme and parts.netloc:
            origins.append(f"{parts.scheme}://{parts.netloc}")
    return origins
