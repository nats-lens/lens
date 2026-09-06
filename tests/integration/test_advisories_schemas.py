"""Advisories and the schema registry, against a real server.

The advisory feed can only be tested by *causing* an advisory: a consumer with a
low `max_deliver` whose messages are never acked. Anything else would be testing
a fixture.
"""

from __future__ import annotations

import asyncio
import base64
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_the_feed_starts_empty_and_says_why(app_client) -> None:
    """The screen's central caveat has to come from the API, not the copy deck."""
    state = await app_client.get(f"/api/servers/{app_client.server_id}/advisories/state")
    assert state.status_code == 200, state.text
    body = state.json()

    assert body["listening"] is True
    assert body["seen"] == 0, "nothing has happened yet on a fresh server"
    assert body["capture_stream"] is None, "nothing is being kept by default"
    assert "published once" in body["note"]


async def test_a_real_nak_becomes_a_classified_advisory(app_client, nats_full: str) -> None:
    """Cause a redelivery, then read it back through the API."""
    server = app_client.server_id
    name = f"ADV{uuid.uuid4().hex[:8].upper()}"
    prefix = name.lower()

    created = await app_client.post(
        f"/api/servers/{server}/jetstream/streams",
        json={"name": name, "subjects": [f"{prefix}.*"], "storage": "file"},
    )
    assert created.status_code == 201, created.text

    consumer = await app_client.post(
        f"/api/servers/{server}/jetstream/streams/{name}/consumers",
        json={
            "stream": name,
            "name": "gives-up",
            "durable": True,
            "ack_policy": "explicit",
            "ack_wait_seconds": 1.0,
            "max_deliver": 2,
        },
    )
    assert consumer.status_code == 201, consumer.text

    # Wake the feed before the event happens: advisories are not stored, so an
    # event published before anyone is listening is simply gone.
    await app_client.get(f"/api/servers/{server}/advisories/state")

    await app_client.post(
        f"/api/servers/{server}/core/publish",
        json={"subject": f"{prefix}.one", "payload_b64": _b64(b"{}"), "headers": {}},
    )

    # A pull consumer delivers nothing until something pulls, so nothing would
    # ever be redelivered. Drive it with a real client: fetch, refuse to ack,
    # repeat past max_deliver.
    import nats

    nc = await nats.connect(nats_full)
    try:
        sub = await nc.jetstream().pull_subscribe(f"{prefix}.one", durable="gives-up")
        for _ in range(4):
            try:
                for msg in await sub.fetch(1, timeout=1):
                    await msg.nak()
            except Exception:
                pass
    finally:
        await nc.close()

    events: list[dict] = []
    for _ in range(30):
        await asyncio.sleep(0.4)
        listed = await app_client.get(f"/api/servers/{server}/advisories")
        assert listed.status_code == 200, listed.text
        events = listed.json()
        if any(e["kind"] == "max_deliveries" for e in events):
            break

    kinds = {e["kind"] for e in events}
    assert "max_deliveries" in kinds, f"expected a give-up advisory, saw {kinds}"

    event = next(e for e in events if e["kind"] == "max_deliveries")
    assert event["severity"] == "alert"
    assert name in event["target"]
    assert event["explanation"], "an advisory with no explanation is just noise"
    assert event["body"], "the raw event the server sent is shown verbatim"

    counts = await app_client.get(f"/api/servers/{server}/advisories/counts")
    assert counts.status_code == 200
    assert any(c["kind"] == "max_deliveries" and c["count"] > 0 for c in counts.json())

    await app_client.delete(f"/api/servers/{server}/jetstream/streams/{name}")


async def test_a_capture_stream_makes_advisories_durable(app_client) -> None:
    server = app_client.server_id
    created = await app_client.post(
        f"/api/servers/{server}/advisories/capture",
        json={
            "name": f"ADVCAP{uuid.uuid4().hex[:6].upper()}",
            "subjects": ["$JS.EVENT.ADVISORY.>"],
            "max_age_seconds": 3600,
            "max_msgs": 1000,
            "replicas": 1,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["capture_stream"] is not None


async def test_a_rule_needs_a_type_that_actually_exists(app_client) -> None:
    """A rule pointing at nothing would decode nothing; refusing it early says so."""
    response = await app_client.post(
        "/api/schemas/rules",
        json={
            "pattern": "itrules.ghost",
            "type_full_name": "it.NoSuchType",
            "server_id": None,
            "precedence": 0,
            "enabled": True,
        },
    )
    assert response.status_code == 400
    assert "descriptor" in response.text.lower()


async def test_rules_are_ordered_by_specificity_not_insertion(app_client, descriptor) -> None:
    """`orders.new` must beat `orders.*` however they were added."""
    broad = await app_client.post(
        "/api/schemas/rules",
        json={
            "pattern": "itrules.>",
            "type_full_name": descriptor,
            "server_id": None,
            "precedence": 0,
            "enabled": True,
        },
    )
    assert broad.status_code == 201, broad.text

    exact = await app_client.post(
        "/api/schemas/rules",
        json={
            "pattern": "itrules.exact",
            "type_full_name": descriptor,
            "server_id": None,
            "precedence": 0,
            "enabled": True,
        },
    )
    assert exact.status_code == 201, exact.text

    listed = await app_client.get("/api/schemas/rules")
    assert listed.status_code == 200
    ours = [r for r in listed.json() if r["pattern"].startswith("itrules")]
    patterns = [r["pattern"] for r in ours]
    assert patterns.index("itrules.exact") < patterns.index("itrules.>"), (
        "the more specific pattern has to be tried first, whatever order it was added in"
    )
    assert ours[0]["specificity"] > ours[-1]["specificity"]

    for rule in ours:
        await app_client.delete(f"/api/schemas/rules/{rule['id']}")


async def test_the_resolution_order_is_published(app_client) -> None:
    """The five steps are documented by the API, not only by the screen."""
    steps = await app_client.get("/api/schemas/resolution-order")
    assert steps.status_code == 200, steps.text
    body = steps.json()
    assert [s["n"] for s in body] == [1, 2, 3, 4, 5]
    assert all(s["description"] for s in body)
