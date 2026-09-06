#!/bin/sh
set -eu

if [ -z "${NATS_LENS_SECRET_KEY:-}" ]; then
  echo "nats-lens: NATS_LENS_SECRET_KEY is not set." >&2
  echo "Stored NATS credentials are encrypted with it, so starting without one would" >&2
  echo "leave them unreadable. Generate a key with:" >&2
  echo "  docker run --rm <image> python -c 'from nats_lens.crypto import generate_key; print(generate_key())'" >&2
  exit 1
fi

DB_DIR="$(dirname "${DATABASE_URL##*:///}")"
if [ ! -w "$DB_DIR" ]; then
  echo "nats-lens: $DB_DIR is not writable by uid $(id -u)." >&2
  echo "The SQLite registry lives there. A named volume needs no setup and is the" >&2
  echo "simplest fix:" >&2
  echo "  docker run -v nats-lens-data:/data ..." >&2
  echo "To keep a host bind mount, give the directory to this uid instead:" >&2
  echo "  mkdir -p ./data && sudo chown -R $(id -u):$(id -g) ./data" >&2
  exit 1
fi

echo "nats-lens: applying migrations"
alembic upgrade head

# One worker, always. Connections, subscription state and the monitoring pollers
# all live in this process; a second worker would serve a different view of them.
#
# Granian rather than uvicorn: HTTP and WebSocket framing happen in Rust, so the
# image carries neither h11/httptools nor the Python websockets library. The
# event loop is uvloop either way.
exec granian \
  --interface asgi \
  --loop uvloop \
  --host 0.0.0.0 \
  --port "${NATS_LENS_PORT:-8000}" \
  --workers 1 \
  nats_lens.app:app
