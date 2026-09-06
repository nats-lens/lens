"""Core NATS and the websocket, end to end.

This is the path nothing else covers: HTTP creates a subscription and returns a
channel, the socket joins it, and a message published over HTTP comes back down
the socket as a transcript row. Unit tests can exercise each half; only this can
show they meet.
"""

from __future__ import annotations

import base64
import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_a_published_message_arrives_on_the_socket(app_client) -> None:
    base = f"/api/servers/{app_client.server_id}/core"

    created = await app_client.post(base + "/subscriptions", json={"subject": "it.core.>"})
    assert created.status_code == 201, created.text
    subscription = created.json()

    # `websocket_connect` is awaited to obtain the session; the session itself
    # is a plain context manager.
    with await app_client.websocket_connect("/ws") as socket:
        socket.send_json({"op": "join", "channel": subscription["channel"]})
        assert socket.receive_json()["t"] == "joined"

        published = await app_client.post(
            base + "/publish",
            json={
                "subject": "it.core.new",
                "payload_b64": _b64(b'{"id":"ord_1","total":4300}'),
                "headers": {},
            },
        )
        assert published.status_code == 201, published.text

        # Both directions are reported: our own publish as OUT, the delivery as IN.
        rows = [socket.receive_json()["row"] for _ in range(2)]

    subjects = {r["subject"] for r in rows}
    assert subjects == {"it.core.new"}
    assert {r["direction"] for r in rows} == {"IN", "OUT"}

    row = rows[0]
    assert row["codec"] == "json"
    # The preview has to tell two messages apart; the first line of pretty JSON
    # is "{", which does not.
    assert "ord_1" in row["preview"]

    full = await app_client.get(f"{base}/messages/{row['capture_id']}")
    assert full.status_code == 200, full.text
    decoded = full.json()["decoded"]
    assert decoded["codec"] == "json"
    assert decoded["resolved_by"] in ("sniff", "content_type")

    await app_client.delete(f"{base}/subscriptions/{subscription['id']}")


async def test_one_subject_two_sockets_is_one_nats_subscription(app_client) -> None:
    """The refcount: two viewers must not cost the broker two subscriptions."""
    base = f"/api/servers/{app_client.server_id}/core"
    created = await app_client.post(base + "/subscriptions", json={"subject": "it.fan.>"})
    channel = created.json()["channel"]

    first_session = await app_client.websocket_connect("/ws")
    second_session = await app_client.websocket_connect("/ws")
    with first_session as first, second_session as second:
        for socket in (first, second):
            socket.send_json({"op": "join", "channel": channel})
            assert socket.receive_json()["t"] == "joined"

        await app_client.post(
            base + "/publish",
            json={"subject": "it.fan.one", "payload_b64": _b64(b"hello"), "headers": {}},
        )

        # Both sockets see it, from the single upstream subscription.
        for socket in (first, second):
            frames = [socket.receive_json() for _ in range(2)]
            assert any(f["row"]["subject"] == "it.fan.one" for f in frames)

    listed = await app_client.get(base + "/subscriptions")
    assert len([s for s in listed.json() if s["subject"] == "it.fan.>"]) == 1

    await app_client.delete(f"{base}/subscriptions/{created.json()['id']}")


async def test_a_request_nobody_answers_is_a_result_not_an_error(app_client) -> None:
    """The design shows this as an outcome on screen, so it must not be a 5xx."""
    response = await app_client.post(
        f"/api/servers/{app_client.server_id}/core/request",
        json={
            "subject": "it.nobody.listening",
            "payload_b64": _b64(b"hi"),
            "headers": {},
            "timeout_seconds": 1,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ok"] is False
    assert "NoRespondersError" in (body["error"] or "")


async def test_unmapped_protobuf_falls_through_to_the_wire_format(app_client) -> None:
    """The chain's floor, on a real message.

    These bytes are the design's own telemetry example: field 1 varint, field 2
    fixed32, field 3 length-delimited. No descriptor is registered, so the only
    honest answer is field numbers and wire types.
    """
    payload = bytes([0x08, 0xD6, 0x01, 0x15, 0x33, 0x33, 0xAB, 0x41, 0x1A, 0x0B]) + b"device-4471"

    response = await app_client.post(
        "/api/schemas/decode",
        json={"subject": "telemetry.device.4471.temp", "payload_b64": _b64(payload)},
    )
    assert response.status_code == 201, response.text
    decoded = response.json()["decoded"]

    assert decoded["resolved_by"] == "wire"
    assert decoded["unmapped_subject"] == "telemetry.device.4471.temp"
    renders = [f["render"] for f in decoded["wire_fields"]]
    assert renders == ["varint 214", "fixed32 21.4 · 0x41ab3333", 'len 11 "device-4471"']


async def test_json_needs_no_schema(app_client) -> None:
    response = await app_client.post(
        "/api/schemas/decode",
        json={"subject": "anything", "payload_b64": _b64(b'{"a":1}')},
    )
    assert response.status_code == 201
    decoded = response.json()["decoded"]
    assert decoded["codec"] == "json"
    assert json.loads(decoded["text"]) == {"a": 1}
