"""`nats-lens` console entry point.

Granian rather than uvicorn: HTTP and WebSocket framing are handled in Rust, so
the Python side only ever sees ASGI events. That drops h11, httptools, pyyaml and
the Python `websockets` library from the runtime, which is the actual reason for
the choice -- the event loop is uvloop either way.

One worker, always. Connections, subscription state and the monitoring pollers
live in this process; see `_assert_single_worker` in app.py.
"""

from __future__ import annotations

from granian import Granian
from granian.constants import Interfaces, Loops
from granian.log import LogLevels

from nats_lens.config import Settings


def main() -> None:
    settings = Settings.from_env()
    Granian(
        "nats_lens.app:app",
        address=settings.host,
        port=settings.port,
        interface=Interfaces.ASGI,
        loop=Loops.uvloop,
        workers=1,
        websockets=True,
        log_level=LogLevels.debug if settings.debug else LogLevels.info,
    ).serve()


if __name__ == "__main__":
    main()
