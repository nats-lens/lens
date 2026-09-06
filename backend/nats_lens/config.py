"""Settings, read from the environment once at startup."""

from __future__ import annotations

import os
from pathlib import Path

import msgspec


class Settings(msgspec.Struct, frozen=True):
    database_url: str = "sqlite+aiosqlite:///./data/nats-lens.db"
    secret_key: str = ""
    """Base64 32-byte key for AES-GCM secret encryption. Required in production."""

    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    static_dir: Path | None = None
    """Where the built SPA lives. Set in the production image; unset in dev (Vite serves it)."""

    cors_origins: tuple[str, ...] = ()

    monitor_timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 5.0
    ws_queue_size: int = 2048
    """Per-socket outbound buffer. Beyond this nats-lens drops oldest and reports the count."""

    proto_upload_dir: Path = Path("data/uploads/protos")
    """Where a descriptor uploaded through the UI is written.

    Uploads land on disk as well as in the registry so they survive, back up by
    being copied, and sit beside the mounted ones rather than being locked inside
    a database file."""

    proto_mount_dir: Path | None = None
    """A directory of `.proto` sources or descriptor sets, supplied by mounting it.

    Read-only as far as nats-lens is concerned. Scanned on start and on demand.
    Unlike an upload, a file here is compiled *inside its own tree*, so imports
    between your own files resolve without building a descriptor set first."""

    @classmethod
    def from_env(cls) -> Settings:
        """Defaults are spelled out here rather than read off the class.

        msgspec keeps field defaults in the struct machinery, not as class
        attributes, so `cls.port` is a descriptor and not the number 8000.
        """
        env = os.environ
        static = env.get("NATS_LENS_STATIC_DIR")
        origins = env.get("NATS_LENS_CORS_ORIGINS", "")
        mounted = env.get("NATS_LENS_PROTO_DIR", "").strip()
        return cls(
            database_url=env.get(
                "DATABASE_URL",
                "sqlite+aiosqlite:///./data/nats-lens.db",
            ),
            secret_key=env.get("NATS_LENS_SECRET_KEY", ""),
            host=env.get("NATS_LENS_HOST", "0.0.0.0"),
            port=int(env.get("NATS_LENS_PORT", "8000")),
            debug=env.get("NATS_LENS_DEBUG", "").lower() in {"1", "true", "yes"},
            static_dir=Path(static) if static else None,
            cors_origins=tuple(o.strip() for o in origins.split(",") if o.strip()),
            monitor_timeout_seconds=float(env.get("NATS_LENS_MONITOR_TIMEOUT", "5.0")),
            connect_timeout_seconds=float(env.get("NATS_LENS_CONNECT_TIMEOUT", "5.0")),
            ws_queue_size=int(env.get("NATS_LENS_WS_QUEUE_SIZE", "2048")),
            proto_upload_dir=Path(env.get("NATS_LENS_PROTO_UPLOAD_DIR", "data/uploads/protos")),
            proto_mount_dir=Path(mounted) if mounted else None,
        )
