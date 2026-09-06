"""Changing a stream or a consumer without destroying it.

Editing was the largest hole in the product: everything was create-then-delete,
so tuning a stream's max age meant losing its messages. These tests hold the
parts that are easy to get subtly wrong -- a partial update that silently resets
the fields it did not mention, and the combinations NATS refuses.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _stream(client, **overrides) -> str:
    name = f"ED{uuid.uuid4().hex[:8].upper()}"
    body = {"name": name, "subjects": [f"{name.lower()}.*"], "max_age_seconds": 3600.0}
    body.update(overrides)
    created = await client.post(f"/api/servers/{client.server_id}/jetstream/streams", json=body)
    assert created.status_code == 201, created.text
    return name


async def test_a_partial_stream_update_leaves_everything_else_alone(app_client) -> None:
    """`update_stream` replaces the whole config, so anything omitted must be
    carried over rather than reset to its default."""
    name = await _stream(app_client, max_msgs=500, description="original")
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    patched = await app_client.patch(base, json={"description": "changed"})
    assert patched.status_code == 200, patched.text
    body = patched.json()

    assert body["description"] == "changed"
    assert body["limits"]["max_msgs"] == 500, "an untouched field must survive the update"
    assert body["limits"]["max_age_seconds"] == 3600.0
    assert body["subjects"] == [f"{name.lower()}.*"]

    await app_client.delete(base)


async def test_editing_a_stream_keeps_its_messages(app_client) -> None:
    """The whole point: this is the alternative to delete-and-recreate."""
    import base64

    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    await app_client.post(
        f"/api/servers/{app_client.server_id}/core/publish",
        json={
            "subject": f"{name.lower()}.one",
            "payload_b64": base64.b64encode(b'{"n":1}').decode(),
            "headers": {},
        },
    )

    patched = await app_client.patch(base, json={"max_age_seconds": 7200.0})
    assert patched.status_code == 200, patched.text
    assert patched.json()["state"]["messages"] == 1, "the message survived the edit"

    await app_client.delete(base)


async def test_a_consumer_can_be_retuned_in_place(app_client) -> None:
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"
    await app_client.post(
        f"{base}/consumers",
        json={"stream": name, "name": "worker", "durable": True, "max_deliver": 3},
    )

    patched = await app_client.patch(
        f"{base}/consumers/worker",
        json={"description": "retuned", "max_deliver": 9, "max_ack_pending": 250},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["description"] == "retuned"
    assert body["max_deliver"] == 9

    await app_client.delete(base)


async def test_a_push_consumer_can_take_a_queue_group(app_client) -> None:
    """Queue groups existed for core NATS but not for JetStream push consumers,
    so load-balanced push delivery could not be configured at all."""
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    created = await app_client.post(
        f"{base}/consumers",
        json={
            "stream": name,
            "name": "pushers",
            "push": True,
            "deliver_subject": f"deliver.{name.lower()}",
            "deliver_group": "workers",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["deliver_group"] == "workers"

    await app_client.delete(base)


async def test_a_queue_group_on_a_pull_consumer_is_refused_with_a_reason(app_client) -> None:
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    refused = await app_client.post(
        f"{base}/consumers",
        json={"stream": name, "name": "bad", "deliver_group": "workers"},
    )
    assert refused.status_code == 400
    assert "push" in refused.text.lower()

    await app_client.delete(base)


async def test_backoff_sets_the_redelivery_schedule(app_client) -> None:
    """And the server overwrites ack_wait with the first delay, which is NATS
    behaviour rather than a lost value -- pinned here so it stays visible."""
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    created = await app_client.post(
        f"{base}/consumers",
        json={
            "stream": name,
            "name": "retrier",
            "backoff_seconds": [2, 10, 30],
            "max_deliver": 4,
            "ack_wait_seconds": 45,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["backoff_seconds"] == [2.0, 10.0, 30.0]
    assert body["ack_wait_seconds"] == 2.0, "the server takes ack_wait from the first backoff"

    await app_client.delete(base)


async def test_backoff_needs_enough_deliveries_to_use_it(app_client) -> None:
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    refused = await app_client.post(
        f"{base}/consumers",
        json={"stream": name, "name": "bad", "backoff_seconds": [1, 2, 3], "max_deliver": 2},
    )
    assert refused.status_code == 400
    assert "max_deliver" in refused.text

    await app_client.delete(base)


@pytest.mark.parametrize(
    ("policy", "missing"),
    [("by_start_sequence", "opt_start_seq"), ("by_start_time", "opt_start_time")],
)
async def test_a_start_policy_without_a_start_point_is_refused(
    app_client, policy: str, missing: str
) -> None:
    """Both policies were unusable: the enum offered them and the request had no
    field to say where to start from."""
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    refused = await app_client.post(
        f"{base}/consumers", json={"stream": name, "name": "c", "deliver_policy": policy}
    )
    assert refused.status_code == 400
    assert missing in refused.text

    await app_client.delete(base)


async def test_starting_from_a_sequence_now_works(app_client) -> None:
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"

    created = await app_client.post(
        f"{base}/consumers",
        json={
            "stream": name,
            "name": "fromseq",
            "deliver_policy": "by_start_sequence",
            "opt_start_seq": 1,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["deliver_policy"] == "by_start_sequence"

    await app_client.delete(base)


async def test_a_consumer_can_be_paused_and_resumed(app_client) -> None:
    """Pausing keeps the consumer's position; there is no open-ended pause in the
    protocol, so a real deadline is reported back."""
    name = await _stream(app_client)
    base = f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"
    await app_client.post(f"{base}/consumers", json={"stream": name, "name": "sleepy"})

    paused = await app_client.post(f"{base}/consumers/sleepy/pause", json={})
    assert paused.status_code == 201, paused.text
    assert paused.json()["paused"] is True
    assert paused.json()["paused_until"] is not None, "a pause always has a deadline"

    resumed = await app_client.post(f"{base}/consumers/sleepy/resume")
    assert resumed.status_code == 201, resumed.text
    assert resumed.json()["paused"] is False

    await app_client.delete(base)
