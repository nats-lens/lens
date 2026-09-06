"""A registry row plus its opened secrets, turned into `nats.connect(**kwargs)`.

The rule this module exists to enforce: a credential is never written to disk.
nats-py's `RawCredentials` accepts the *contents* of a `.creds` file as a string
and `nkeys_seed_str` accepts a seed the same way, so material sealed in the registry
is opened in memory, handed to nats-py, and never materialises as a temp file.
Without that, encrypting at rest would be theatre -- a decrypted copy sitting in
`/tmp` is exactly the thing the ciphertext was protecting against.

Credentials that the operator mounted as a file are the one exception, and only
because nats-lens never held them in the first place: the path is passed through
and the file stays where it was put.
"""

from __future__ import annotations

import ssl
from typing import Any

import msgspec
from nats.aio.client import RawCredentials

from nats_lens.domain.servers.schemas import AuthMode

# The layout nats-py's creds reader scans for. Building one in memory is how an
# nkey seed and its user JWT reach `user_credentials` together without a file.
_CREDS_TEMPLATE = """-----BEGIN NATS USER JWT-----
{jwt}
------END NATS USER JWT------

************************* IMPORTANT *************************
    NKEY Seed printed below can be used to sign and prove identity.
    NKEYs are sensitive and should be treated as secrets.

-----BEGIN USER NKEY SEED-----
{seed}
------END USER NKEY SEED------

*************************************************************
"""

AUTH_LABELS: dict[AuthMode, str] = {
    AuthMode.NONE: "no authentication",
    AuthMode.USERPASS: "user & password",
    AuthMode.TOKEN: "token",
    AuthMode.CREDS: "credentials file",
    AuthMode.NKEY: "NKey seed",
}
"""How each mode reads in the summary line under a server's name."""


class AuthError(Exception):
    """The saved configuration cannot produce a connection.

    Raised before any socket is opened, because "you never entered a password"
    deserves a different sentence from "the server rejected your password".
    """


class TlsSpec(msgspec.Struct, frozen=True):
    enabled: bool = False
    verify: bool = True
    ca_path: str | None = None
    cert_path: str | None = None
    key_path: str | None = None


class AuthSpec(msgspec.Struct, frozen=True):
    """Credentials, opened.

    Held in memory for the lifetime of a connection and never serialised: this is
    not a response type, and `tests/unit/test_contract.py` is what keeps it from
    quietly becoming one.
    """

    mode: AuthMode = AuthMode.NONE
    username: str | None = None
    password: str | None = None
    token: str | None = None
    creds_text: str | None = None
    """The contents of a `.creds` file, decrypted. Passed as `RawCredentials`."""
    creds_path: str | None = None
    """A `.creds` file the operator mounted. nats-lens never read or stored it."""
    nkey_seed: str | None = None
    jwt: str | None = None

    @property
    def label(self) -> str:
        return AUTH_LABELS[self.mode]


def ssl_context(tls: TlsSpec) -> ssl.SSLContext | None:
    """Build the context nats-py's `tls=` expects, or None to leave TLS alone."""
    if not tls.enabled:
        return None
    ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH, cafile=tls.ca_path)
    if not tls.verify:
        # Lab only, and the form says so. Hostname checking has to go first:
        # CPython refuses to clear verify_mode while check_hostname is on.
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if tls.cert_path:
        ctx.load_cert_chain(certfile=tls.cert_path, keyfile=tls.key_path)
    return ctx


def credentials_kwargs(auth: AuthSpec) -> dict[str, Any]:
    """Only the authentication half, so the probe and the manager cannot diverge."""
    match auth.mode:
        case AuthMode.NONE:
            return {}

        case AuthMode.USERPASS:
            if not auth.username:
                raise AuthError("User and password authentication needs a username.")
            if auth.password is None:
                raise AuthError(
                    "No password is stored for this server. Re-enter it under the "
                    "server's settings."
                )
            return {"user": auth.username, "password": auth.password}

        case AuthMode.TOKEN:
            if not auth.token:
                raise AuthError(
                    "No token is stored for this server. Re-enter it under the server's settings."
                )
            return {"token": auth.token}

        case AuthMode.CREDS:
            if auth.creds_text:
                return {"user_credentials": RawCredentials(auth.creds_text)}
            if auth.creds_path:
                return {"user_credentials": auth.creds_path}
            raise AuthError(
                "No credentials are stored for this server, and no credentials file path is set."
            )

        case AuthMode.NKEY:
            if not auth.nkey_seed:
                raise AuthError(
                    "No NKey seed is stored for this server. Re-enter it under the "
                    "server's settings."
                )
            if auth.jwt:
                # Seed plus JWT is what a .creds file holds, so give nats-py one --
                # built in memory rather than on disk.
                text = _CREDS_TEMPLATE.format(jwt=auth.jwt.strip(), seed=auth.nkey_seed.strip())
                return {"user_credentials": RawCredentials(text)}
            return {"nkeys_seed_str": auth.nkey_seed}

    raise AuthError(f"Unsupported authentication mode: {auth.mode}.")


def connect_kwargs(
    auth: AuthSpec,
    tls: TlsSpec,
    *,
    name: str = "nats-lens",
    inbox_prefix: str = "_INBOX",
    connect_timeout: float = 5.0,
    max_reconnect_attempts: int = -1,
    allow_reconnect: bool = True,
) -> dict[str, Any]:
    """Everything but `servers` and the callbacks, which differ per caller."""
    kwargs: dict[str, Any] = {
        "name": name,
        "inbox_prefix": inbox_prefix,
        # nats-py types these as int but only ever compares and sleeps on them, so
        # a float second is both accepted and more useful for a probe.
        "connect_timeout": connect_timeout,
        "allow_reconnect": allow_reconnect,
        "max_reconnect_attempts": max_reconnect_attempts if allow_reconnect else 0,
    }
    kwargs.update(credentials_kwargs(auth))
    if (ctx := ssl_context(tls)) is not None:
        kwargs["tls"] = ctx
    return kwargs
