"""The one line the transcript shows for each message.

The firehose never carries a payload, only this. So it has to be the line that
tells two messages apart -- which the first line of pretty-printed JSON is not.
"""

from __future__ import annotations

import pytest

from nats_lens.codec.schemas import Codec, Decoded, DecodedField, ResolvedBy
from nats_lens.conn.multiplex import _preview

pytestmark = pytest.mark.unit


def test_pretty_printed_json_previews_as_its_content_not_its_first_brace() -> None:
    decoded = Decoded(
        codec=Codec.JSON,
        resolved_by=ResolvedBy.SNIFF,
        text='{\n  "id": "ord_8813",\n  "total": 4300\n}',
    )
    preview, _ = _preview(decoded, 30)
    assert preview != "{"
    assert "ord_8813" in preview
    assert "\n" not in preview


def test_a_decoded_message_previews_its_fields() -> None:
    decoded = Decoded(
        codec=Codec.PROTOBUF,
        resolved_by=ResolvedBy.SUBJECT_RULE,
        type_name="acme.orders.v1.OrderCreated",
        fields=(DecodedField(name="id", field_number=1, type_name="string", value="ord_8813"),),
    )
    preview, _ = _preview(decoded, 30)
    assert "OrderCreated" in preview
    assert "id=ord_8813" in preview


def test_an_empty_payload_says_so() -> None:
    preview, _ = _preview(Decoded(codec=Codec.EMPTY, resolved_by=ResolvedBy.SNIFF), 0)
    assert preview == "(empty)"


def test_a_long_payload_is_clipped() -> None:
    decoded = Decoded(codec=Codec.TEXT, resolved_by=ResolvedBy.SNIFF, text="x" * 500)
    preview, clipped = _preview(decoded, 500)
    assert clipped is True
    assert len(preview) <= 121
