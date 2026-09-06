"""Shared fixtures.

Unit tests need nothing. Integration tests get a migrated SQLite file and real NATS from
testcontainers -- two NATS servers, in fact: one with monitoring and $SYS enabled
and one with neither, because the honest empty state deserves a test and not a
promise. See tests/integration/conftest.py.
"""

from __future__ import annotations

import pytest

from nats_lens.config import Settings
from nats_lens.crypto import SecretBox, generate_key


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def secret_key() -> str:
    return generate_key()


@pytest.fixture
def secret_box(secret_key: str) -> SecretBox:
    return SecretBox(secret_key)


@pytest.fixture
def settings(secret_key: str) -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        secret_key=secret_key,
        debug=True,
    )
