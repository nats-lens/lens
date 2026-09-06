"""JetStream against a real server, through the HTTP API.

The unit suite covers the mapping from nats-py's types to ours. What it cannot
cover is that those types are what a real server actually sends -- which is how
`config.discard.value` shipped: an enum in the type stubs, a plain string on the
wire, and an AttributeError the moment anything listed a stream.
"""

from __future__ import annotations

import base64
import json
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _stream(client, name: str, subjects: list[str]) -> None:
    created = await client.post(
        f"/api/servers/{client.server_id}/jetstream/streams",
        json={"name": name, "subjects": subjects, "storage": "file", "retention": "limits"},
    )
    assert created.status_code == 201, created.text


async def test_a_stream_round_trips(app_client) -> None:
    name = f"IT_{uuid.uuid4().hex[:8].upper()}"
    await _stream(app_client, name, [f"{name.lower()}.*"])

    listed = await app_client.get(f"/api/servers/{app_client.server_id}/jetstream/streams")
    assert listed.status_code == 200, listed.text
    assert name in [s["name"] for s in listed.json()]

    detail = await app_client.get(f"/api/servers/{app_client.server_id}/jetstream/streams/{name}")
    assert detail.status_code == 200
    body = detail.json()
    # The fields that broke on real data: enums that arrive as strings.
    assert body["storage"] in ("file", "memory")
    assert body["retention"] in ("limits", "interest", "workqueue")
    assert body["limits"]["discard"] in ("old", "new")

    deleted = await app_client.delete(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}"
    )
    assert deleted.status_code in (200, 204)


async def test_per_subject_counts_come_back(app_client) -> None:
    """`stream_info(subjects_filter=...)` -- the singular call, not the plural one."""
    name = f"IT_{uuid.uuid4().hex[:8].upper()}"
    prefix = name.lower()
    await _stream(app_client, name, [f"{prefix}.*"])

    for i in range(3):
        published = await app_client.post(
            f"/api/servers/{app_client.server_id}/core/publish",
            json={
                "subject": f"{prefix}.created",
                "payload_b64": base64.b64encode(f'{{"n":{i}}}'.encode()).decode(),
                "headers": {},
            },
        )
        assert published.status_code == 201, published.text

    subjects = await app_client.get(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/subjects"
    )
    assert subjects.status_code == 200, subjects.text
    counts = {row["subject"]: row["count"] for row in subjects.json()}
    assert counts.get(f"{prefix}.created") == 3

    await app_client.delete(f"/api/servers/{app_client.server_id}/jetstream/streams/{name}")


async def test_consumer_lag_is_reported(app_client) -> None:
    """num_pending is the lag, and it is the number the screen colours a row by."""
    name = f"IT_{uuid.uuid4().hex[:8].upper()}"
    prefix = name.lower()
    await _stream(app_client, name, [f"{prefix}.*"])

    created = await app_client.post(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/consumers",
        json={"stream": name, "name": "reader", "durable": True, "ack_policy": "explicit"},
    )
    assert created.status_code == 201, created.text

    for i in range(5):
        await app_client.post(
            f"/api/servers/{app_client.server_id}/core/publish",
            json={
                "subject": f"{prefix}.created",
                "payload_b64": base64.b64encode(f'{{"n":{i}}}'.encode()).decode(),
                "headers": {},
            },
        )

    consumers = await app_client.get(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/consumers"
    )
    assert consumers.status_code == 200, consumers.text
    reader = next(c for c in consumers.json() if c["name"] == "reader")
    assert reader["num_pending"] == 5, "nothing has been consumed, so all five are pending"
    assert reader["health"] in ("healthy", "degraded", "failing")

    await app_client.delete(f"/api/servers/{app_client.server_id}/jetstream/streams/{name}")


async def test_stored_messages_are_decoded(app_client) -> None:
    name = f"IT_{uuid.uuid4().hex[:8].upper()}"
    prefix = name.lower()
    await _stream(app_client, name, [f"{prefix}.*"])
    await app_client.post(
        f"/api/servers/{app_client.server_id}/core/publish",
        json={
            "subject": f"{prefix}.created",
            "payload_b64": base64.b64encode(b'{"id":"ord_1"}').decode(),
            "headers": {},
        },
    )

    read = await app_client.post(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/messages",
        json={"seq": 1},
    )
    assert read.status_code == 201, read.text
    messages = read.json()
    assert messages, "sequence 1 should exist after one publish"
    assert messages[0]["decoded"]["codec"] == "json"

    await app_client.delete(f"/api/servers/{app_client.server_id}/jetstream/streams/{name}")


async def test_reading_by_subject_walks_forward(app_client) -> None:
    """The regression: every row came back as the same message.

    `get_msg(next=True)` takes the starting sequence in `seq`, and the walk
    tracked a cursor but passed None -- so the server answered with the first
    match every time and a page of twenty was one message repeated twenty times.
    """
    name = f"IT_{uuid.uuid4().hex[:8].upper()}"
    prefix = name.lower()
    await _stream(app_client, name, [f"{prefix}.*"])

    # Interleaved, so a walk that ignores the filter is as visible as one that
    # does not advance.
    for i in range(5):
        for leaf in ("wanted", "other"):
            await app_client.post(
                f"/api/servers/{app_client.server_id}/core/publish",
                json={
                    "subject": f"{prefix}.{leaf}",
                    "payload_b64": base64.b64encode(f'{{"n":{i}}}'.encode()).decode(),
                    "headers": {},
                },
            )

    read = await app_client.post(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/messages",
        json={"subject": f"{prefix}.wanted", "limit": 5},
    )
    assert read.status_code == 201, read.text
    rows = read.json()

    seqs = [m["seq"] for m in rows]
    assert len(seqs) == 5
    assert len(set(seqs)) == 5, f"the same message came back more than once: {seqs}"
    assert seqs == sorted(seqs), "the walk goes forward"
    assert {m["subject"] for m in rows} == {f"{prefix}.wanted"}
    assert [json.loads(base64.b64decode(m["payload_b64"]))["n"] for m in rows] == [0, 1, 2, 3, 4]

    # And the next page resumes rather than starting over.
    nxt = await app_client.post(
        f"/api/servers/{app_client.server_id}/jetstream/streams/{name}/messages",
        json={"subject": f"{prefix}.wanted", "seq": seqs[-1] + 1, "limit": 5},
    )
    assert nxt.status_code == 201, nxt.text
    assert [m["seq"] for m in nxt.json()] == [], "there is no sixth match"

    await app_client.delete(f"/api/servers/{app_client.server_id}/jetstream/streams/{name}")
