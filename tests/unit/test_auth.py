"""Credentials, and the promise that none of them touch the disk.

The encrypt-at-rest decision only pays for itself if the decrypted copy exists
nowhere but memory. nats-py's `RawCredentials` and `nkeys_seed_str` are what make
that possible, so these tests assert on the *type* handed to nats-py, not merely
that a connection could be made -- a temp-file fallback would still connect, and
would still be the bug.
"""

from __future__ import annotations

import ssl
from pathlib import Path

import pytest
from nats.aio.client import Client as NATS
from nats.aio.client import RawCredentials

from nats_lens.conn.auth import (
    AUTH_LABELS,
    AuthError,
    AuthSpec,
    TlsSpec,
    connect_kwargs,
    credentials_kwargs,
    ssl_context,
)
from nats_lens.domain.servers.schemas import AuthMode

pytestmark = pytest.mark.unit

JWT = "eyJ0eXAiOiJKV1QiLCJhbGciOiJlZDI1NTE5LW5rZXkifQ.fake-user-jwt-body.sig"
SEED = "SUAGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG"

CREDS_FILE = f"""-----BEGIN NATS USER JWT-----
{JWT}
------END NATS USER JWT------

-----BEGIN USER NKEY SEED-----
{SEED}
------END USER NKEY SEED------
"""


def test_no_authentication_sends_nothing() -> None:
    assert credentials_kwargs(AuthSpec(mode=AuthMode.NONE)) == {}


def test_user_and_password() -> None:
    spec = AuthSpec(mode=AuthMode.USERPASS, username="orders-console", password="hunter2")
    assert credentials_kwargs(spec) == {"user": "orders-console", "password": "hunter2"}


def test_token() -> None:
    assert credentials_kwargs(AuthSpec(mode=AuthMode.TOKEN, token="s3cret")) == {"token": "s3cret"}


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        (AuthSpec(mode=AuthMode.USERPASS, username="u"), "No password is stored"),
        (AuthSpec(mode=AuthMode.USERPASS, password="p"), "needs a username"),
        (AuthSpec(mode=AuthMode.TOKEN), "No token is stored"),
        (AuthSpec(mode=AuthMode.CREDS), "No credentials are stored"),
        (AuthSpec(mode=AuthMode.NKEY), "No NKey seed is stored"),
    ],
)
def test_a_missing_credential_fails_before_any_socket_opens(spec: AuthSpec, fragment: str) -> None:
    """ "You never entered a password" deserves a different sentence from
    "the server rejected your password", and it can only be said before connecting."""
    with pytest.raises(AuthError, match=fragment):
        credentials_kwargs(spec)


def test_stored_credentials_are_passed_in_memory_never_as_a_file() -> None:
    kwargs = credentials_kwargs(AuthSpec(mode=AuthMode.CREDS, creds_text=CREDS_FILE))
    creds = kwargs["user_credentials"]
    assert isinstance(creds, RawCredentials), "a .creds file must never be written to disk"
    assert str(creds) == CREDS_FILE


def test_nats_py_can_read_the_credentials_we_hand_it() -> None:
    """The in-memory path is only safe if nats-py's own reader accepts it."""
    raw = credentials_kwargs(AuthSpec(mode=AuthMode.CREDS, creds_text=CREDS_FILE))[
        "user_credentials"
    ]
    client = NATS()
    assert bytes(client._read_creds_user_jwt(raw)).decode() == JWT
    assert bytes(client._read_creds_user_nkey(raw)).decode() == SEED


def test_a_mounted_credentials_file_is_passed_through_untouched() -> None:
    """nats-lens never held this one, so the path stays a path."""
    kwargs = credentials_kwargs(AuthSpec(mode=AuthMode.CREDS, creds_path="/run/secrets/app.creds"))
    assert kwargs == {"user_credentials": "/run/secrets/app.creds"}


def test_a_seed_alone_goes_in_as_a_string() -> None:
    kwargs = credentials_kwargs(AuthSpec(mode=AuthMode.NKEY, nkey_seed=SEED))
    assert kwargs == {"nkeys_seed_str": SEED}
    assert "nkeys_seed" not in kwargs, "nkeys_seed is the file-path form and must not be used"


def test_a_seed_with_a_jwt_becomes_credentials_built_in_memory() -> None:
    """Seed plus JWT is what a .creds file holds, so nats-py gets one -- without a file."""
    kwargs = credentials_kwargs(AuthSpec(mode=AuthMode.NKEY, nkey_seed=SEED, jwt=JWT))
    raw = kwargs["user_credentials"]
    assert isinstance(raw, RawCredentials)
    client = NATS()
    assert bytes(client._read_creds_user_jwt(raw)).decode() == JWT
    assert bytes(client._read_creds_user_nkey(raw)).decode() == SEED


def test_no_credential_mode_writes_a_temporary_file(tmp_path: Path) -> None:
    """A blunt guard on the whole module: nothing here creates a file anywhere."""
    before = set(tmp_path.iterdir())
    for spec in (
        AuthSpec(mode=AuthMode.NONE),
        AuthSpec(mode=AuthMode.USERPASS, username="u", password="p"),
        AuthSpec(mode=AuthMode.TOKEN, token="t"),
        AuthSpec(mode=AuthMode.CREDS, creds_text=CREDS_FILE),
        AuthSpec(mode=AuthMode.NKEY, nkey_seed=SEED),
        AuthSpec(mode=AuthMode.NKEY, nkey_seed=SEED, jwt=JWT),
    ):
        credentials_kwargs(spec)
    assert set(tmp_path.iterdir()) == before


def test_tls_is_off_unless_asked_for() -> None:
    assert ssl_context(TlsSpec()) is None


def test_tls_verification_can_be_turned_off_for_a_lab() -> None:
    ctx = ssl_context(TlsSpec(enabled=True, verify=False))
    assert ctx is not None
    assert ctx.check_hostname is False
    assert ctx.verify_mode is ssl.CERT_NONE


def test_tls_verifies_by_default() -> None:
    ctx = ssl_context(TlsSpec(enabled=True))
    assert ctx is not None
    assert ctx.check_hostname is True
    assert ctx.verify_mode is ssl.CERT_REQUIRED


def test_connect_kwargs_carries_the_advanced_options() -> None:
    kwargs = connect_kwargs(
        AuthSpec(mode=AuthMode.TOKEN, token="t"),
        TlsSpec(),
        name="nats-lens",
        inbox_prefix="_LENS",
        connect_timeout=2.5,
        max_reconnect_attempts=7,
    )
    assert kwargs["name"] == "nats-lens"
    assert kwargs["inbox_prefix"] == "_LENS"
    assert kwargs["connect_timeout"] == 2.5
    assert kwargs["max_reconnect_attempts"] == 7
    assert kwargs["token"] == "t"


def test_a_probe_never_reconnects() -> None:
    """One question, one answer. A probe that retried would report a lie about latency."""
    kwargs = connect_kwargs(AuthSpec(), TlsSpec(), allow_reconnect=False)
    assert kwargs["allow_reconnect"] is False
    assert kwargs["max_reconnect_attempts"] == 0


def test_every_mode_has_a_label_for_the_summary_line() -> None:
    assert set(AUTH_LABELS) == set(AuthMode)
    assert AUTH_LABELS[AuthMode.NONE] == "no authentication"
