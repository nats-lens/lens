"""Credential sealing.

The design promised the OS keychain; a container has none, so secrets are sealed
under NATS_LENS_SECRET_KEY instead. That trade is only acceptable if the sealing
is real and the plaintext genuinely cannot escape.
"""

from __future__ import annotations

import pytest

from nats_lens.crypto import SecretBox, SecretError, SecretKind, SecretRef, generate_key

pytestmark = pytest.mark.unit


def test_round_trip(secret_box: SecretBox) -> None:
    sealed = secret_box.seal("-----BEGIN NATS USER JWT-----\neyJ0eXAi...")
    assert secret_box.open_str(sealed).startswith("-----BEGIN NATS USER JWT-----")


def test_ciphertext_does_not_contain_the_plaintext(secret_box: SecretBox) -> None:
    sealed = secret_box.seal("hunter2-the-password")
    assert b"hunter2" not in sealed.ciphertext


def test_nonce_differs_per_seal(secret_box: SecretBox) -> None:
    a = secret_box.seal("same")
    b = secret_box.seal("same")
    assert a.nonce != b.nonce
    assert a.ciphertext != b.ciphertext


def test_a_different_key_cannot_open_it(secret_box: SecretBox) -> None:
    sealed = secret_box.seal("secret")
    with pytest.raises(SecretError, match="NATS_LENS_SECRET_KEY has probably changed"):
        SecretBox(generate_key()).open(sealed)


def test_tampering_is_detected(secret_box: SecretBox) -> None:
    """AES-GCM is authenticated: a flipped bit must fail, not decrypt to garbage."""
    sealed = secret_box.seal("secret")
    tampered = type(sealed)(
        ciphertext=bytes([sealed.ciphertext[0] ^ 0x01]) + sealed.ciphertext[1:],
        nonce=sealed.nonce,
        key_version=sealed.key_version,
    )
    with pytest.raises(SecretError):
        secret_box.open(tampered)


def test_missing_key_says_how_to_make_one() -> None:
    with pytest.raises(SecretError, match="generate_key"):
        SecretBox("")


def test_short_key_is_rejected() -> None:
    import base64

    with pytest.raises(SecretError, match="32 bytes"):
        SecretBox(base64.b64encode(b"too-short").decode())


def test_secret_ref_carries_no_key_material() -> None:
    """What the API is allowed to say about a secret: that it exists."""
    ref = SecretRef(kind=SecretKind.CREDS, is_set=True, hint="orders-console.creds")
    assert set(ref.__struct_fields__) == {"kind", "is_set", "hint"}


def test_the_dev_compose_key_is_actually_valid() -> None:
    """The default in docker-compose.dev.yml must decode to 32 bytes.

    It once did not, and nothing noticed until the first credential was saved --
    every other request worked fine, because nothing else opens the box.
    """
    import re
    from pathlib import Path

    compose = Path(__file__).resolve().parents[2] / "docker-compose.dev.yml"
    match = re.search(
        r"NATS_LENS_SECRET_KEY: \$\{NATS_LENS_SECRET_KEY:-([^}]+)\}", compose.read_text()
    )
    assert match, "the dev compose no longer sets a default NATS_LENS_SECRET_KEY"
    SecretBox(match.group(1))  # raises if it is not a valid 32-byte key
