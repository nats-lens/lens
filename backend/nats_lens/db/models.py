"""The registry. Everything nats-lens remembers between runs.

Stored in SQLite. The registry is a handful of small tables written by one
process, so a file beats a server here -- and it removes a container from
every deployment. List and mapping columns are therefore `JSON` rather than
Postgres `ARRAY`/`JSONB`; SQLAlchemy serialises them the same way on both.

Deliberately small: servers, how to authenticate to them, protobuf descriptors
and the subject rules that select them, saved filters, preferences. Nothing that
NATS itself is the source of truth for is stored here -- no message history, no
advisory log, no metrics time series. Those are read live and labelled with their
source (see `nats_lens.provenance`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ServerGroup(Base, Timestamped):
    __tablename__ = "server_group"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    servers: Mapped[list[Server]] = relationship(back_populates="group")


class Server(Base, Timestamped):
    """A registered NATS server, and everything needed to reach it."""

    __tablename__ = "server"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("server_group.id", ondelete="SET NULL")
    )
    colour: Mapped[str] = mapped_column(String(9), default="#a6b1ee", nullable=False)

    # Client endpoint. First URL is primary, the rest are failover seeds tried in order.
    urls: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    auth_mode: Mapped[str] = mapped_column(String(16), default="none", nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    nkey_jwt_is_file: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    creds_path: Mapped[str | None] = mapped_column(Text)
    """Set when credentials are mounted as a file rather than stored inline."""

    # Transport security.
    tls_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    tls_verify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tls_ca_path: Mapped[str | None] = mapped_column(Text)
    tls_cert_path: Mapped[str | None] = mapped_column(Text)
    tls_key_path: Mapped[str | None] = mapped_column(Text)

    # The monitoring port. Absent means the whole `monitor` provenance source is unavailable.
    monitoring_url: Mapped[str | None] = mapped_column(Text)
    monitoring_poll_seconds: Mapped[float] = mapped_column(Float, default=5.0, nullable=False)

    # The $SYS account. Absent means no push events and no STATSZ heartbeats.
    system_account_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    system_username: Mapped[str | None] = mapped_column(String(255))
    system_creds_path: Mapped[str | None] = mapped_column(Text)

    # Advanced client options.
    client_name: Mapped[str] = mapped_column(String(120), default="nats-lens", nullable=False)
    inbox_prefix: Mapped[str] = mapped_column(String(64), default="_INBOX", nullable=False)
    jetstream_domain: Mapped[str | None] = mapped_column(String(120))
    max_reconnect_attempts: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    """-1 means unlimited, matching nats-py."""

    connect_on_startup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    group: Mapped[ServerGroup | None] = relationship(back_populates="servers")
    secrets: Mapped[list[ServerSecret]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    rules: Mapped[list[SubjectRule]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    filters: Mapped[list[SavedFilter]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )


class ServerSecret(Base, Timestamped):
    """Sealed credential material. Read only by the connection manager."""

    __tablename__ = "server_secret"
    __table_args__ = (UniqueConstraint("server_id", "kind", name="uq_server_secret_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("server.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    hint: Mapped[str | None] = mapped_column(String(64))
    """A safe fragment shown in the UI, never key material."""

    server: Mapped[Server] = relationship(back_populates="secrets")


class Descriptor(Base, Timestamped):
    """A compiled protobuf FileDescriptorSet, and the source it came from."""

    __tablename__ = "descriptor"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    package: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_descriptor_set: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    protoc_version: Mapped[str | None] = mapped_column(String(32))
    imported_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    """True for descriptors that exist to satisfy imports and are never matched by a rule."""
    note: Mapped[str | None] = mapped_column(Text)

    origin: Mapped[str] = mapped_column(String(16), default="upload", nullable=False)
    """`upload` or `mounted`. Decides whether this may be deleted from the UI: a
    mounted file belongs to whoever mounted it, and deleting the row would only
    have it reappear on the next scan."""
    source_path: Mapped[str | None] = mapped_column(String(1024))
    """Where the file lives, so the screen can say it and a rescan can find it."""
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    """What was compiled. A rescan recompiles only what actually changed."""

    types: Mapped[list[MessageType]] = relationship(
        back_populates="descriptor", cascade="all, delete-orphan"
    )


class MessageType(Base):
    """One message type inside a descriptor, denormalised so the list view is one query."""

    __tablename__ = "message_type"
    __table_args__ = (UniqueConstraint("full_name", name="uq_message_type_full_name"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    descriptor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("descriptor.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    field_names: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False)

    descriptor: Mapped[Descriptor] = relationship(back_populates="types")


class SubjectRule(Base, Timestamped):
    """Step 2 of the decoding chain: subject pattern -> protobuf message type."""

    __tablename__ = "subject_rule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    server_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("server.id", ondelete="CASCADE"))
    """NULL means the rule applies to every server."""
    pattern: Mapped[str] = mapped_column(String(511), nullable=False)
    type_full_name: Mapped[str] = mapped_column(String(511), nullable=False)
    precedence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    """Tie-break only. Specificity of the pattern decides first."""
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    server: Mapped[Server | None] = relationship(back_populates="rules")


class SavedFilter(Base, Timestamped):
    """A subject the user keeps coming back to, shown as a chip on the Core screen."""

    __tablename__ = "saved_filter"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    server_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("server.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str] = mapped_column(String(511), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="core", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    server: Mapped[Server] = relationship(back_populates="filters")
