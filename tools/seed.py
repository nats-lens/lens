"""Fill the dev NATS server with the data the design shows.

    docker compose -f docker-compose.dev.yml --profile demo run --rm seed

Creates the streams, consumers, KV buckets and object buckets the artboards
depict, then publishes traffic -- including a consumer that repeatedly naks, so
the Advisories screen has real MAX_DELIVERIES events rather than invented ones.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import string

import nats
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DiscardPolicy,
    KeyValueConfig,
    ObjectStoreConfig,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)

NATS_URL = os.environ.get("NATS_URL", "nats://app:app@localhost:4222")


def _rid(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


async def seed_streams(js, jsm) -> None:
    streams = [
        StreamConfig(
            name="ORDERS",
            subjects=["orders.*"],
            description="Customer order lifecycle",
            storage=StorageType.FILE,
            retention=RetentionPolicy.LIMITS,
            discard=DiscardPolicy.OLD,
            max_age=720 * 3600,
            duplicate_window=120,
        ),
        StreamConfig(
            name="EVENTS",
            subjects=["events.>"],
            description="Domain event log",
            storage=StorageType.FILE,
            max_age=168 * 3600,
        ),
        StreamConfig(
            name="TELEMETRY",
            subjects=["telemetry.>"],
            description="Device telemetry, no schema registered on purpose",
            storage=StorageType.FILE,
            max_msgs=500_000,
        ),
        StreamConfig(
            name="ORDERS-DLQ",
            subjects=["dlq.orders.>"],
            description="Where give-ups land",
            storage=StorageType.FILE,
        ),
    ]
    for cfg in streams:
        try:
            await jsm.add_stream(cfg)
            print(f"stream {cfg.name}")
        except Exception as exc:
            print(f"stream {cfg.name}: {exc}")

    consumers = [
        ("ORDERS", "order-picker", "orders.new", 30.0, -1),
        ("ORDERS", "fulfilment-svc", "orders.*", 30.0, -1),
        ("ORDERS", "analytics-tap", "orders.*", 30.0, -1),
        # max_deliver=5 with a handler that never acks is what produces the
        # MAX_DELIVERIES advisories the Advisories screen is built to show.
        ("ORDERS", "search-index", "orders.new", 2.0, 5),
        ("EVENTS", "projection-users", "events.user.>", 30.0, -1),
        ("EVENTS", "warehouse-etl", "events.>", 300.0, -1),
    ]
    for stream, name, filt, ack_wait, max_deliver in consumers:
        try:
            await jsm.add_consumer(
                stream,
                ConsumerConfig(
                    durable_name=name,
                    filter_subject=filt,
                    ack_policy=AckPolicy.EXPLICIT,
                    ack_wait=ack_wait,
                    max_deliver=max_deliver,
                ),
            )
            print(f"consumer {stream}/{name}")
        except Exception as exc:
            print(f"consumer {stream}/{name}: {exc}")


async def seed_kv(js) -> None:
    for name, history, ttl in (
        ("CONFIG", 10, None),
        ("SESSIONS", 1, 1800),
        ("FEATURE_FLAGS", 20, None),
    ):
        try:
            kv = await js.create_key_value(KeyValueConfig(bucket=name, history=history, ttl=ttl))
            print(f"kv {name}")
        except Exception:
            kv = await js.key_value(name)
        seeds = {
            "CONFIG": {
                "tenant.acme.limits": {"max_orders_per_min": 600, "tier": "enterprise"},
                "tenant.globex.limits": {"max_orders_per_min": 120, "tier": "standard"},
                "billing.retry.policy": {"attempts": 5, "backoff_ms": 2000},
            },
            "SESSIONS": {f"sess.{_rid()}": {"user": f"usr_{_rid(4)}"} for _ in range(4)},
            "FEATURE_FLAGS": {
                "checkout.express": {"enabled": True, "rollout_pct": 25},
                "orders.protobuf": {"enabled": True, "rollout_pct": 100},
            },
        }[name]
        for key, value in seeds.items():
            await kv.put(key, json.dumps(value).encode())


async def seed_objects(js) -> None:
    for name in ("ARTIFACTS", "MODELS"):
        try:
            store = await js.create_object_store(name, ObjectStoreConfig(bucket=name))
            print(f"object bucket {name}")
        except Exception:
            store = await js.object_store(name)
        await store.put(
            "readme.txt", b"Seeded by tools/seed.py for the nats-lens dev environment.\n"
        )


async def publish_traffic(nc, js, seconds: int = 20) -> None:
    """Traffic on three subjects, one of which has no schema on purpose.

    `telemetry.device.*.temp` is deliberately left unmapped so the Core screen's
    inspector falls through to the raw wire-format view, which is the fallback the
    design exists to demonstrate.
    """
    print(f"publishing for {seconds}s")
    end = asyncio.get_running_loop().time() + seconds
    n = 0
    while asyncio.get_running_loop().time() < end:
        await js.publish(
            "orders.new",
            json.dumps({"id": f"ord_{_rid()}", "total_cents": random.randint(500, 9000)}).encode(),
        )
        await js.publish(
            "events.user.signup",
            json.dumps({"user": f"usr_{_rid(4)}", "plan": "team"}).encode(),
        )
        # A bare protobuf message with no registered descriptor: field 1 varint,
        # field 2 fixed32, field 3 length-delimited string.
        temp = random.randint(180, 260)
        device = f"device-{random.randint(1000, 9999)}".encode()
        payload = (
            bytes(
                [
                    0x08,
                    temp & 0x7F | 0x80,
                    temp >> 7,
                    0x15,
                    0x33,
                    0x33,
                    0xAB,
                    0x41,
                    0x1A,
                    len(device),
                ]
            )
            + device
        )
        await nc.publish(f"telemetry.device.{random.randint(1000, 9999)}.temp", payload)
        n += 3
        await asyncio.sleep(0.05)
    print(f"published {n} messages")


async def nak_forever(js) -> None:
    """Pull from search-index and never ack, so real advisories are generated."""
    try:
        sub = await js.pull_subscribe("orders.new", durable="search-index")
    except Exception as exc:
        print(f"nak loop: {exc}")
        return
    for _ in range(30):
        try:
            for msg in await sub.fetch(5, timeout=1):
                await msg.nak()
        except Exception:
            pass


async def main() -> None:
    nc = await nats.connect(NATS_URL)
    js, jsm = nc.jetstream(), nc.jsm()
    try:
        await seed_streams(js, jsm)
        await seed_kv(js)
        await seed_objects(js)
        await asyncio.gather(publish_traffic(nc, js), nak_forever(js))
        print("seed complete")
    finally:
        await nc.drain()


if __name__ == "__main__":
    asyncio.run(main())
