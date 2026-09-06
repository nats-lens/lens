# Production: ONE container serving the built React app and the Python backend.
#
# Stage 1 builds the SPA. Stage 2 resolves the Python environment. Stage 3 is the
# runtime, which carries neither node nor a compiler.

# ---------------------------------------------------------------- 1. SPA build
FROM node:24-alpine AS web
WORKDIR /w
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------ 2. Python deps
FROM python:3.14-alpine AS pydeps
RUN apk add --no-cache build-base libffi-dev
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# ---------------------------------------------------------------- 3. Runtime
FROM python:3.14-alpine AS runtime

# Stamped by the release workflow from the git tag. The package is on PYTHONPATH
# rather than pip-installed, so without this there is no version to read.
ARG NATS_LENS_VERSION=0.0.0-dev
RUN apk add --no-cache libstdc++ libffi tini curl \
 && adduser -D -u 10001 natslens

COPY --from=pydeps /opt/venv /opt/venv
COPY backend/ /app/backend/
COPY alembic.ini /app/
COPY docker/entrypoint.sh /app/entrypoint.sh
COPY --from=web /w/dist /srv/static

# The SQLite registry and uploaded proto definitions live here. Bind-mount it to
# keep both across upgrades.
VOLUME ["/data"]

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
    NATS_LENS_STATIC_DIR=/srv/static \
    NATS_LENS_VERSION=${NATS_LENS_VERSION} \
    NATS_LENS_PROTO_UPLOAD_DIR=/data/uploads/protos \
    NATS_LENS_PROTO_DIR=/protos \
    DATABASE_URL=sqlite+aiosqlite:////data/nats-lens.db \
    NATS_LENS_PORT=8000

WORKDIR /app
RUN mkdir -p /data/uploads/protos /protos \
 && chmod +x /app/entrypoint.sh \
 && chown -R natslens:natslens /app /srv/static /data /protos
USER natslens

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/app/entrypoint.sh"]
