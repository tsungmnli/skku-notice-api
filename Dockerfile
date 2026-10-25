# syntax=docker/dockerfile:1

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim
ENV PATH=/srv/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv
COPY --from=build /srv/.venv /srv/.venv
COPY app ./app
# Needed by `python -m app.migrate` (the Argo CD migration Job runs from this same image).
COPY alembic.ini ./
COPY migrations ./migrations
# Non-root; the numeric UID lets Kubernetes verify runAsNonRoot.
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
EXPOSE 8000
# The ingress terminates TLS and forwards X-Forwarded-*; trust it (cluster-internal only).
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
