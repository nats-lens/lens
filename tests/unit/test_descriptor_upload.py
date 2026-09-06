"""What happens when a .proto will not compile.

protoc's diagnostics name the file, the line and the column, and they are already
written to be read by a person. The upload path has to carry them out to the
operator: the difference between "invoice.proto:3:1: import not found" and a 500
is the difference between a fixable mistake and a mystery.
"""

from __future__ import annotations

import base64
from typing import cast

import pytest

from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.schemas import DescriptorUpload
from nats_lens.domain.protoschemas.service import SchemaError, SchemaService

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

IMPORTS_A_MISSING_FILE = b"""
syntax = "proto3";
package acme.billing.v1;
import "common/money.proto";
message Invoice { string number = 1; acme.common.Money total = 2; }
"""

NOT_PROTO_AT_ALL = b"this is not a protocol buffer definition {{{"


def _upload(content: bytes, filename: str = "invoice.proto") -> DescriptorUpload:
    return DescriptorUpload(filename=filename, content_b64=base64.b64encode(content).decode())


def _service() -> SchemaService:
    """Compilation fails before the repository is ever reached, so there is
    nothing here for a database to do."""
    return SchemaService(cast("SchemaRepository", None))


async def test_an_unresolvable_import_is_a_schema_error_not_a_crash() -> None:
    """The regression: `compile_proto` raised outside the guard, so protoc's own
    message escaped as a 500 instead of reaching the operator as a 4xx."""
    with pytest.raises(SchemaError) as caught:
        await _service().upload_descriptor(_upload(IMPORTS_A_MISSING_FILE))

    detail = str(caught.value)
    assert "common/money.proto" in detail, "protoc named the file; so must we"
    assert "FileDescriptorSet" in detail, "and the message has to name the way out"


async def test_something_that_is_not_a_proto_is_also_a_schema_error() -> None:
    with pytest.raises(SchemaError):
        await _service().upload_descriptor(_upload(NOT_PROTO_AT_ALL))


async def test_an_empty_upload_says_so_before_reaching_protoc() -> None:
    with pytest.raises(SchemaError, match="empty"):
        await _service().upload_descriptor(_upload(b""))
