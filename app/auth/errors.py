"""Auth errors, answered in the API's `{"detail": "<message>", "code": "<machine code>"}` shape.

`detail` is shown to the user as-is (Korean); `code` is what a client branches on.
"""

from fastapi import Request
from fastapi.responses import JSONResponse


class AuthError(Exception):
    def __init__(
        self,
        detail: str,
        code: str,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status_code = status_code
        self.headers = headers


async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code},
        headers=exc.headers,
    )


def not_authenticated() -> AuthError:
    return AuthError("로그인이 필요합니다.", "not_authenticated", 401)


def rate_limited(retry_after: int) -> AuthError:
    return AuthError(
        "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
        "rate_limited",
        429,
        headers={"Retry-After": str(max(1, retry_after))},
    )
