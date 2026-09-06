"""Envelope encryption for NATS credentials.

The design promises the OS keychain. A container has no keychain, so secrets are
sealed with AES-GCM under a key supplied in the environment and stored as
ciphertext. They are opened only to hand to nats-py at connect time, and no API
response ever carries one -- see `SecretRef`, which is all the frontend sees.
"""

from __future__ import annotations

import base64
import os
from enum import StrEnum

import msgspec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_VERSION = 1
_NONCE_BYTES = 12


class SecretKind(StrEnum):
    PASSWORD = "password"
    TOKEN = "token"
    CREDS = "creds"
    NKEY_SEED = "nkey_seed"
    JWT = "jwt"
    TLS_KEY = "tls_key"


class SecretError(Exception):
    pass


class Sealed(msgspec.Struct, frozen=True):
    """Ciphertext as stored. Never leaves the process."""

    ciphertext: bytes
    nonce: bytes
    key_version: int = KEY_VERSION


class SecretRef(msgspec.Struct, frozen=True):
    """What the API returns in place of a secret: that it exists, and nothing more."""

    kind: SecretKind
    is_set: bool
    hint: str | None = None
    """A safe fragment, e.g. the last 4 characters of a filename. Never key material."""


def generate_key() -> str:
    """A fresh base64 key, for `.env` and for tests."""
    return base64.b64encode(AESGCM.generate_key(bit_length=256)).decode()


class SecretBox:
    """Seals and opens secrets under one key."""

    def __init__(self, key_b64: str) -> None:
        if not key_b64:
            raise SecretError(
                "NATS_LENS_SECRET_KEY is not set. Generate one with "
                "`python -c 'from nats_lens.crypto import generate_key; print(generate_key())'`."
            )
        try:
            key = base64.b64decode(key_b64, validate=True)
        except Exception as exc:
            raise SecretError("NATS_LENS_SECRET_KEY is not valid base64.") from exc
        if len(key) != 32:
            raise SecretError(f"NATS_LENS_SECRET_KEY must decode to 32 bytes, got {len(key)}.")
        self._aead = AESGCM(key)

    def seal(self, plaintext: str | bytes, *, aad: bytes | None = None) -> Sealed:
        data = plaintext.encode() if isinstance(plaintext, str) else plaintext
        nonce = os.urandom(_NONCE_BYTES)
        return Sealed(ciphertext=self._aead.encrypt(nonce, data, aad), nonce=nonce)

    def open(self, sealed: Sealed, *, aad: bytes | None = None) -> bytes:
        if sealed.key_version != KEY_VERSION:
            raise SecretError(
                f"Secret was sealed with key version {sealed.key_version}, "
                f"this build understands {KEY_VERSION}."
            )
        try:
            return self._aead.decrypt(sealed.nonce, sealed.ciphertext, aad)
        except Exception as exc:
            raise SecretError(
                "Could not open a stored secret. NATS_LENS_SECRET_KEY has probably changed; "
                "the affected credentials must be re-entered."
            ) from exc

    def open_str(self, sealed: Sealed, *, aad: bytes | None = None) -> str:
        return self.open(sealed, aad=aad).decode()
