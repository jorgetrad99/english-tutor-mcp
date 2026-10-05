# syntax=docker/dockerfile:1.7
# Product image (core loop v0). Build from the repository root:
#   docker build -f deploy/Dockerfile -t tutor:latest .
# One image, two jobs: the default entrypoint serves MCP, OAuth and the website on port 8000;
# `tutor-migrate` (the compose `migrate` service) applies migrations and provisions the DB roles.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
# Keep the uv minor version equal to `uv --version` on the dev machine (uv_build is pinned <0.13).
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so a code change does not reinstall them. No dev dependency group.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
# Build-time check: the installed package (not the source tree) carries its data files.
COPY deploy/check_package_data.py /tmp/check_package_data.py
RUN /app/.venv/bin/python /tmp/check_package_data.py

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 tutor \
 && useradd --system --uid 10001 --gid tutor --home-dir /app --no-create-home \
      --shell /usr/sbin/nologin tutor \
 && mkdir -p /data/oauth \
 && chown -R tutor:tutor /data \
 && chmod 0700 /data/oauth
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY alembic ./alembic
COPY --chmod=0755 deploy/migrate.sh /usr/local/bin/tutor-migrate
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TUTOR_HOST=0.0.0.0 \
    TUTOR_PORT=8000 \
    TUTOR_OAUTH_STORAGE_DIR=/data/oauth
USER 10001:10001
EXPOSE 8000
# MCP-side path: the website paths reject any Host other than the public one (TrustedHost).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/.well-known/oauth-authorization-server', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["python", "-m", "tutor"]
