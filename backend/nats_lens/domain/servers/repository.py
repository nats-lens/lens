"""The server registry, and the one place credentials are sealed and opened.

Two shapes come out of here. `ServerConfig` is what the Add-a-server form reads
back, and it can only ever carry `SecretRef` -- that a secret exists, and nothing
more. `ConnectionSpec` is what the connection manager needs, and it is the only
thing that ever holds plaintext. Keeping them apart is what makes
`test_no_secret_material_is_reachable_from_any_response` a cheap guarantee rather
than a thing to remember.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from nats_lens.conn.auth import AuthSpec, TlsSpec
from nats_lens.conn.connection import ConnectionSpec
from nats_lens.crypto import Sealed, SecretBox, SecretKind, SecretRef
from nats_lens.db.models import Server, ServerGroup, ServerSecret
from nats_lens.domain.servers.schemas import (
    AdvancedConfig,
    AuthMode,
    SecretInput,
    ServerConfig,
    ServerCreate,
    ServerUpdate,
    TlsConfig,
)

# Which secret each mode cannot work without. Used both to seal on the way in and
# to tell the form that a required credential is missing.
REQUIRED_SECRET: dict[AuthMode, SecretKind | None] = {
    AuthMode.NONE: None,
    AuthMode.USERPASS: SecretKind.PASSWORD,
    AuthMode.TOKEN: SecretKind.TOKEN,
    AuthMode.CREDS: SecretKind.CREDS,
    AuthMode.NKEY: SecretKind.NKEY_SEED,
}


class SecretVault:
    """A `SecretBox` opened on first use.

    Deferred on purpose. A registry of servers that need no credentials is useful on
    its own, and dev machines routinely run without `NATS_LENS_SECRET_KEY`. Building
    the box lazily means the key is demanded at the moment a secret is actually
    stored or opened -- with `SecretBox`'s own instructions -- rather than at boot,
    where the message would be about a server nobody was trying to reach.
    """

    def __init__(self, key_b64: str) -> None:
        self._key = key_b64
        self._box: SecretBox | None = None

    @property
    def box(self) -> SecretBox:
        if self._box is None:
            self._box = SecretBox(self._key)
        return self._box

    def seal(self, plaintext: str) -> Sealed:
        return self.box.seal(plaintext)

    def open(self, row: ServerSecret) -> str:
        return self.box.open_str(
            Sealed(ciphertext=row.ciphertext, nonce=row.nonce, key_version=row.key_version)
        )


def hint_for(kind: SecretKind, value: str) -> str:
    """A fragment safe to render. Never key material, not even a prefix of it.

    A password's last four characters are still four characters of the password, so
    the hint says how long it is and stops there. The one thing worth showing is a
    filename, and a filename is not a secret.
    """
    if kind is SecretKind.CREDS:
        return f"{len(value.encode())} bytes"
    return f"{len(value)} characters"


class ServerRepository:
    """CRUD over `Server`, `ServerSecret` and `ServerGroup`."""

    def __init__(self, session: AsyncSession, vault: SecretVault) -> None:
        self._session = session
        self._vault = vault

    def _query(self) -> Select[tuple[Server]]:
        return select(Server).options(selectinload(Server.secrets), selectinload(Server.group))

    async def list_all(self) -> list[Server]:
        result = await self._session.execute(self._query().order_by(Server.name))
        return list(result.scalars())

    async def get(self, server_id: uuid.UUID) -> Server | None:
        result = await self._session.execute(self._query().where(Server.id == server_id))
        return result.scalar_one_or_none()

    async def startup_servers(self) -> list[Server]:
        result = await self._session.execute(
            self._query().where(Server.connect_on_startup.is_(True)).order_by(Server.name)
        )
        return list(result.scalars())

    async def _group(self, name: str | None) -> ServerGroup | None:
        if not name:
            return None
        result = await self._session.execute(select(ServerGroup).where(ServerGroup.name == name))
        group = result.scalar_one_or_none()
        if group is None:
            group = ServerGroup(name=name)
            self._session.add(group)
            await self._session.flush()
        return group

    async def create(self, data: ServerCreate) -> Server:
        group = await self._group(data.group)
        row = Server(
            name=data.name,
            group_id=group.id if group else None,
            colour=data.colour,
            urls=list(data.urls),
            auth_mode=str(data.auth_mode),
            username=data.username,
            creds_path=data.creds_path,
            tls_enabled=data.tls.enabled,
            tls_verify=data.tls.verify,
            tls_ca_path=data.tls.ca_path,
            tls_cert_path=data.tls.cert_path,
            tls_key_path=data.tls.key_path,
            monitoring_url=data.monitoring_url,
            monitoring_poll_seconds=data.monitoring_poll_seconds,
            system_account_enabled=data.system_account_enabled,
            system_username=data.system_username,
            system_creds_path=data.system_creds_path,
            client_name=data.advanced.client_name,
            inbox_prefix=data.advanced.inbox_prefix,
            jetstream_domain=data.advanced.jetstream_domain,
            max_reconnect_attempts=data.advanced.max_reconnect_attempts,
            connect_on_startup=data.connect_on_startup,
            # Initialised explicitly so `_replace_secrets` finds a loaded
            # collection. Left off, SQLAlchemy treats `row.secrets` on a new
            # instance as an unloaded relationship and tries to lazy-load it,
            # which raises MissingGreenlet under the async session.
            secrets=[],
        )
        self._session.add(row)
        await self._session.flush()
        self._replace_secrets(row, data.secrets)
        await self._session.flush()
        await self._session.refresh(row, ["secrets", "group"])
        return row

    async def update(self, row: Server, data: ServerUpdate) -> Server:
        if data.group is not None:
            group = await self._group(data.group)
            row.group_id = group.id if group else None
        for field, column in (
            ("name", "name"),
            ("colour", "colour"),
            ("username", "username"),
            ("creds_path", "creds_path"),
            ("monitoring_url", "monitoring_url"),
            ("monitoring_poll_seconds", "monitoring_poll_seconds"),
            ("system_account_enabled", "system_account_enabled"),
            ("system_username", "system_username"),
            ("system_creds_path", "system_creds_path"),
            ("connect_on_startup", "connect_on_startup"),
        ):
            value = getattr(data, field)
            if value is not None:
                setattr(row, column, value)
        if data.urls is not None:
            row.urls = list(data.urls)
        if data.auth_mode is not None:
            row.auth_mode = str(data.auth_mode)
        if data.tls is not None:
            row.tls_enabled = data.tls.enabled
            row.tls_verify = data.tls.verify
            row.tls_ca_path = data.tls.ca_path
            row.tls_cert_path = data.tls.cert_path
            row.tls_key_path = data.tls.key_path
        if data.advanced is not None:
            row.client_name = data.advanced.client_name
            row.inbox_prefix = data.advanced.inbox_prefix
            row.jetstream_domain = data.advanced.jetstream_domain
            row.max_reconnect_attempts = data.advanced.max_reconnect_attempts
        if data.secrets is not None:
            self._replace_secrets(row, data.secrets)
        await self._session.flush()
        await self._session.refresh(row, ["secrets", "group"])
        return row

    async def delete(self, row: Server) -> None:
        await self._session.delete(row)
        await self._session.flush()

    def _replace_secrets(self, row: Server, secrets: tuple[SecretInput, ...]) -> None:
        """Replace by kind, as `ServerUpdate` promises. An empty value clears one."""
        by_kind = {s.kind: s for s in row.secrets}
        for incoming in secrets:
            existing = by_kind.get(str(incoming.kind))
            if not incoming.value:
                if existing is not None:
                    row.secrets.remove(existing)
                continue
            sealed = self._vault.seal(incoming.value)
            hint = hint_for(incoming.kind, incoming.value)
            if existing is None:
                row.secrets.append(
                    ServerSecret(
                        server_id=row.id,
                        kind=str(incoming.kind),
                        ciphertext=sealed.ciphertext,
                        nonce=sealed.nonce,
                        key_version=sealed.key_version,
                        hint=hint,
                    )
                )
            else:
                existing.ciphertext = sealed.ciphertext
                existing.nonce = sealed.nonce
                existing.key_version = sealed.key_version
                existing.hint = hint

    # --------------------------------------------------------- projections

    def open_secrets(self, row: Server) -> dict[SecretKind, str]:
        """Decrypt. The only call site is building a `ConnectionSpec`."""
        opened: dict[SecretKind, str] = {}
        for secret in row.secrets:
            try:
                kind = SecretKind(secret.kind)
            except ValueError:
                continue
            opened[kind] = self._vault.open(secret)
        return opened

    def spec(self, row: Server) -> ConnectionSpec:
        return to_spec(row, self.open_secrets(row))


def to_config(row: Server) -> ServerConfig:
    """What the form reads back. Secrets appear as refs and nothing else."""
    mode = AuthMode(row.auth_mode)
    refs = [
        SecretRef(kind=SecretKind(s.kind), is_set=True, hint=s.hint)
        for s in sorted(row.secrets, key=lambda s: s.kind)
        if _is_known_kind(s.kind)
    ]
    required = REQUIRED_SECRET[mode]
    if required is not None and not any(r.kind is required for r in refs):
        refs.append(SecretRef(kind=required, is_set=False))
    return ServerConfig(
        id=row.id,
        name=row.name,
        group=row.group.name if row.group else None,
        colour=row.colour,
        urls=tuple(row.urls),
        auth_mode=mode,
        username=row.username,
        creds_path=row.creds_path,
        secrets=tuple(refs),
        tls=TlsConfig(
            enabled=row.tls_enabled,
            verify=row.tls_verify,
            ca_path=row.tls_ca_path,
            cert_path=row.tls_cert_path,
            key_path=row.tls_key_path,
        ),
        monitoring_url=row.monitoring_url,
        monitoring_poll_seconds=row.monitoring_poll_seconds,
        system_account_enabled=row.system_account_enabled,
        system_username=row.system_username,
        system_creds_path=row.system_creds_path,
        advanced=AdvancedConfig(
            client_name=row.client_name,
            inbox_prefix=row.inbox_prefix,
            jetstream_domain=row.jetstream_domain,
            max_reconnect_attempts=row.max_reconnect_attempts,
        ),
        connect_on_startup=row.connect_on_startup,
    )


def _is_known_kind(kind: str) -> bool:
    try:
        SecretKind(kind)
    except ValueError:
        return False
    return True


def to_auth(row: Server, opened: dict[SecretKind, str]) -> AuthSpec:
    mode = AuthMode(row.auth_mode)
    return AuthSpec(
        mode=mode,
        username=row.username,
        password=opened.get(SecretKind.PASSWORD),
        token=opened.get(SecretKind.TOKEN),
        creds_text=opened.get(SecretKind.CREDS),
        creds_path=row.creds_path,
        nkey_seed=opened.get(SecretKind.NKEY_SEED),
        jwt=opened.get(SecretKind.JWT),
    )


def to_system_auth(row: Server, opened: dict[SecretKind, str]) -> AuthSpec | None:
    """Credentials for the second, `$SYS`-bound client.

    The registry has `system_username` and `system_creds_path` but no column of its
    own for a system password, and `server_secret` is unique on (server_id, kind).
    So a mounted `.creds` file always works, and a user with a password works when
    the application account is not itself using the one `password` secret. When
    neither holds, the connection is skipped and the telemetry card says why rather
    than the app account being reused against `$SYS`, which would fail confusingly.
    """
    if not row.system_account_enabled:
        return None
    if row.system_creds_path:
        return AuthSpec(mode=AuthMode.CREDS, creds_path=row.system_creds_path)
    if row.system_username:
        if AuthMode(row.auth_mode) is AuthMode.USERPASS:
            return None
        password = opened.get(SecretKind.PASSWORD)
        if password is None:
            return None
        return AuthSpec(mode=AuthMode.USERPASS, username=row.system_username, password=password)
    return None


def to_spec(row: Server, opened: dict[SecretKind, str]) -> ConnectionSpec:
    return ConnectionSpec(
        server_id=row.id,
        name=row.name,
        urls=tuple(row.urls),
        auth=to_auth(row, opened),
        tls=TlsSpec(
            enabled=row.tls_enabled,
            verify=row.tls_verify,
            ca_path=row.tls_ca_path,
            cert_path=row.tls_cert_path,
            key_path=row.tls_key_path,
        ),
        monitoring_url=row.monitoring_url,
        monitoring_poll_seconds=row.monitoring_poll_seconds,
        system_account_enabled=row.system_account_enabled,
        system_auth=to_system_auth(row, opened),
        client_name=row.client_name,
        inbox_prefix=row.inbox_prefix,
        jetstream_domain=row.jetstream_domain,
        max_reconnect_attempts=row.max_reconnect_attempts,
        connect_on_startup=row.connect_on_startup,
    )


class SqlRegistry:
    """The connection manager's `Registry`, backed by the SQLite registry.

    It opens its own short session per lookup: the manager outlives any request, so
    borrowing the request-scoped one would hand it a session that is about to close.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], vault: SecretVault):
        self._factory = session_factory
        self._vault = vault

    async def load(self, server_id: uuid.UUID) -> ConnectionSpec | None:
        async with self._factory() as session:
            repo = ServerRepository(session, self._vault)
            row = await repo.get(server_id)
            return repo.spec(row) if row is not None else None

    async def load_startup(self) -> list[ConnectionSpec]:
        async with self._factory() as session:
            repo = ServerRepository(session, self._vault)
            return [repo.spec(row) for row in await repo.startup_servers()]
