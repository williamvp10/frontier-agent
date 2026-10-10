# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.5 AS uv
FROM python:3.12.13-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /opt/frontier
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12.13-slim-bookworm AS runtime
RUN groupadd --gid 10001 frontier && useradd --uid 10001 --gid 10001 --no-create-home frontier
WORKDIR /opt/frontier
COPY --from=builder /opt/frontier/.venv ./.venv
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts/bootstrap.py ./scripts/bootstrap.py
RUN mkdir -p /var/lib/frontier/artifacts && chown -R 10001:10001 /var/lib/frontier
ENV PATH="/opt/frontier/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
