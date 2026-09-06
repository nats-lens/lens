# Development backend: source is bind-mounted, granian reloads.
FROM python:3.14-alpine

# build-base is insurance: every dependency in the lock resolves to a musllinux
# wheel today (verified for msgspec, aiosqlite, cffi, greenlet, pynacl, cryptography,
# uvloop, httptools and psycopg-binary), but a future bump should fail slowly at
# build time rather than mysteriously at import time.
RUN apk add --no-cache build-base libffi-dev curl

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python

WORKDIR /app

# Dependencies first, so a source edit does not reinstall the world.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --all-groups

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/backend

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && granian --interface asgi --loop uvloop --host 0.0.0.0 --port 8000 --workers 1 --reload --reload-paths /app/backend nats_lens.app:app"]
