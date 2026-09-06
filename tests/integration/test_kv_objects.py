"""Key-Value and the object store, against a real server.

Two behaviours here exist only because the real library does something the type
stubs do not admit to, so only an integration test can hold them:

  * `ObjectStore.list()` raises `NotFoundError` for an *empty* bucket, which
    would surface as a 404 for a bucket that plainly exists.
  * A KV write with a stale revision must be a 409 the UI can explain, not a
    silent overwrite.
"""

from __future__ import annotations

import base64
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


async def test_kv_compare_and_set(app_client) -> None:
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/kv"

    created = await app_client.post(base, json={"name": bucket, "history": 5})
    assert created.status_code == 201, created.text

    first = await app_client.put(
        f"{base}/{bucket}/keys/config.limits", json={"value_b64": _b64('{"max":10}')}
    )
    assert first.status_code == 200, first.text
    revision = first.json()["revision"]

    # The right revision wins.
    ok = await app_client.put(
        f"{base}/{bucket}/keys/config.limits",
        json={"value_b64": _b64('{"max":20}'), "last_revision": revision},
    )
    assert ok.status_code == 200, ok.text

    # The same revision again is now stale, and must be refused rather than
    # silently clobbering whatever the other writer put there.
    stale = await app_client.put(
        f"{base}/{bucket}/keys/config.limits",
        json={"value_b64": _b64('{"max":30}'), "last_revision": revision},
    )
    assert stale.status_code == 409, stale.text
    assert "revision" in stale.text.lower()

    history = await app_client.get(f"{base}/{bucket}/history/config.limits")
    assert history.status_code == 200
    assert len(history.json()) >= 2

    await app_client.delete(f"{base}/{bucket}")


async def test_kv_key_page_carries_its_own_warning(app_client) -> None:
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/kv"
    await app_client.post(base, json={"name": bucket, "history": 1})
    await app_client.put(f"{base}/{bucket}/keys/a", json={"value_b64": _b64("1")})

    keys = await app_client.get(f"{base}/{bucket}/keys")
    assert keys.status_code == 200, keys.text
    body = keys.json()
    assert [k["key"] for k in body["keys"]] == ["a"]
    assert body["note"], "the page always says how big the bucket is"

    await app_client.delete(f"{base}/{bucket}")


async def test_an_empty_object_bucket_is_empty_not_missing(app_client) -> None:
    """The nats-py trap: `list()` raises NotFoundError when there is nothing in it."""
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"

    created = await app_client.post(base, json={"name": bucket})
    assert created.status_code == 201, created.text

    listed = await app_client.get(f"{base}/{bucket}/objects")
    assert listed.status_code == 200, "an empty bucket is not a missing one"
    assert listed.json() == []


async def test_a_missing_object_bucket_really_is_a_404(app_client) -> None:
    """The other half of the pair: absent must stay distinguishable from empty."""
    missing = await app_client.get(
        f"/api/servers/{app_client.server_id}/objects/NO_SUCH_BUCKET/objects"
    )
    assert missing.status_code == 404


async def test_an_object_bucket_reports_what_the_server_says_about_it(app_client) -> None:
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"
    await app_client.post(base, json={"name": bucket})

    listed = await app_client.get(base)
    assert listed.status_code == 200
    summary = next(b for b in listed.json() if b["name"] == bucket)

    # `objects` is counted, not inferred from the stream's message count -- chunks
    # and metadata share the stream, so the message count would be wrong.
    assert summary["objects"] == 0
    assert summary["sealed"] is False
    assert summary["max_chunk_size"] > 0
    assert summary["stream_name"] == f"OBJ_{bucket}"


async def test_an_object_round_trips_byte_for_byte(app_client) -> None:
    """Upload, then download, and compare.

    Exercises the streaming path at both ends: nats-py reads the upload through
    `readinto` in chunks, and the download is handed back as a stream, so a large
    object is never held whole in memory at either side.
    """
    import os

    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"
    await app_client.post(base, json={"name": bucket})

    # Larger than one chunk, so the chunking is actually exercised.
    blob = os.urandom(300_000)
    uploaded = await app_client.post(
        f"{base}/{bucket}/objects",
        files={"data": ("blob.bin", blob, "application/octet-stream")},
    )
    assert uploaded.status_code == 201, uploaded.text
    info = uploaded.json()
    assert info["name"] == "blob.bin"
    assert info["size"] == len(blob)
    assert info["chunks"] > 1, "a 300 KB object should span several chunks"
    assert info["digest"].startswith("SHA-256=")

    downloaded = await app_client.get(f"{base}/{bucket}/download/blob.bin")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == blob, "what comes back must be what went in"


async def test_download_has_its_own_path_segment(app_client) -> None:
    """The routing trap that made download 404 for every object.

    Object names may contain slashes, so the name parameter has to be greedy --
    which meant `.../objects/{name}/content` resolved to an object literally
    named `<name>/content`. Download therefore lives under its own segment, and
    the old shape must not quietly come back.
    """
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"
    await app_client.post(base, json={"name": bucket})
    await app_client.post(
        f"{base}/{bucket}/objects", files={"data": ("a.txt", b"hello", "text/plain")}
    )

    assert (await app_client.get(f"{base}/{bucket}/download/a.txt")).content == b"hello"

    stale = await app_client.get(f"{base}/{bucket}/objects/a.txt/content")
    assert stale.status_code == 404, "the ambiguous shape should not resolve at all"


async def test_object_metadata_can_be_changed_without_rewriting_it(app_client) -> None:
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"
    await app_client.post(base, json={"name": bucket})
    await app_client.post(
        f"{base}/{bucket}/objects", files={"data": ("notes.txt", b"body", "text/plain")}
    )

    patched = await app_client.patch(
        f"{base}/{bucket}/objects/notes.txt",
        json={"description": "release notes", "headers": {"Git-Sha": "4bf92f3a"}},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "release notes"
    assert patched.json()["headers"]["Git-Sha"] == "4bf92f3a"

    # The bytes are untouched by a metadata edit.
    assert (await app_client.get(f"{base}/{bucket}/download/notes.txt")).content == b"body"


async def test_a_sealed_bucket_refuses_writes(app_client) -> None:
    """Sealing is permanent, so the refusal has to be clear rather than a 500."""
    bucket = f"IT{uuid.uuid4().hex[:8].upper()}"
    base = f"/api/servers/{app_client.server_id}/objects"
    await app_client.post(base, json={"name": bucket})
    await app_client.post(
        f"{base}/{bucket}/objects", files={"data": ("a.txt", b"before", "text/plain")}
    )

    sealed = await app_client.post(f"{base}/{bucket}/seal")
    assert sealed.status_code == 201, sealed.text
    assert sealed.json()["sealed"] is True

    refused = await app_client.post(
        f"{base}/{bucket}/objects", files={"data": ("b.txt", b"after", "text/plain")}
    )
    assert refused.status_code in (400, 409), refused.text
    assert "sealed" in refused.text.lower()

    # Reading still works: sealed means read-only, not gone.
    assert (await app_client.get(f"{base}/{bucket}/download/a.txt")).content == b"before"
