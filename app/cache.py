"""Cache-Control for the public read endpoints.

`/articles`, `/categories` and `/writers` answer every visitor the same, because the NEW/UPDATED
badges are worked out in the browser (see app/freshness.py), so the response can be kept by the
browser and by Cloudflare. A minute is short enough that a notice the crawler just stored shows
up at once and long enough to absorb a page refreshed in a loop or a burst of visitors.

Applied as a route dependency on GET routes only. Errors (404, 422) are raised before the
header would be copied onto the response, so they are not cached. Nothing under `/me` or
`/auth` may use this: those are per-person and get `no-store` (app/middleware.py).
"""

from fastapi import Response

PUBLIC_MAX_AGE = 60


def public_cache(response: Response) -> None:
    response.headers["Cache-Control"] = f"public, max-age={PUBLIC_MAX_AGE}"
