"""Reading definitions off disk.

Two directories with different rules: uploads are ours to write and delete,
mounted files belong to whoever mounted them. Most of what can go wrong here is
a path -- a browser can send `../../etc/passwd` as a filename, and a mounted tree
can contain a symlink pointing anywhere -- so that is what this pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nats_lens.domain.protoschemas.schemas import Origin
from nats_lens.domain.protoschemas.store import ProtoStore, is_descriptor_set, safe_upload_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("orders.proto", "orders.proto"),
        ("../../etc/passwd", "passwd"),
        ("nested/dir/orders.proto", "orders.proto"),
        ("../orders.proto", "orders.proto"),
        (".hidden", "hidden"),
        ("", "descriptor.proto"),
        ("a b;c.proto", "a_b_c.proto"),
    ],
)
def test_an_upload_name_cannot_escape_the_directory(given: str, expected: str) -> None:
    assert safe_upload_name(given) == expected


def test_the_kind_is_read_from_the_extension() -> None:
    assert is_descriptor_set("schema.desc") is True
    assert is_descriptor_set("schema.pb") is True
    assert is_descriptor_set("orders.proto") is False
    assert is_descriptor_set("ORDERS.PROTO") is False


def test_an_upload_lands_in_the_upload_directory(tmp_path: Path) -> None:
    store = ProtoStore(tmp_path / "uploads", None)
    written = store.save_upload("../../escape.proto", b'syntax = "proto3";')

    assert written.parent == (tmp_path / "uploads")
    assert written.read_bytes() == b'syntax = "proto3";'


def test_deleting_an_upload_never_follows_a_path_out(tmp_path: Path) -> None:
    """`source_path` is stored data; a row pointing outside must not delete."""
    outside = tmp_path / "precious.proto"
    outside.write_bytes(b"keep me")
    store = ProtoStore(tmp_path / "uploads", None)

    store.delete_upload(outside)

    assert outside.exists(), "a path outside the upload directory is not ours to remove"


def test_scan_finds_both_sources_and_labels_them(tmp_path: Path) -> None:
    uploads, mounted = tmp_path / "uploads", tmp_path / "mounted"
    (uploads / "sub").mkdir(parents=True)
    (mounted / "common").mkdir(parents=True)
    (uploads / "a.proto").write_bytes(b"a")
    (mounted / "b.proto").write_bytes(b"b")
    (mounted / "common" / "c.proto").write_bytes(b"c")
    (mounted / "notes.md").write_bytes(b"ignored")
    (mounted / "schema.desc").write_bytes(b"d")

    found = ProtoStore(uploads, mounted).scan()

    # Grouped by origin then path, so a scan report reads the same way twice.
    assert [(f.origin, f.relative) for f in found] == [
        (Origin.MOUNTED, "b.proto"),
        (Origin.MOUNTED, "common/c.proto"),
        (Origin.MOUNTED, "schema.desc"),
        (Origin.UPLOAD, "a.proto"),
    ]
    # The relative path is what protoc is given, so an import inside the tree
    # resolves against its neighbours.
    assert [f.is_descriptor_set for f in found] == [False, False, True, False]


def test_a_missing_mount_is_not_an_error(tmp_path: Path) -> None:
    """The common case on a first run: the directory is simply not mounted."""
    assert ProtoStore(tmp_path / "nope", tmp_path / "also-nope").scan() == []


def test_a_symlink_out_of_the_tree_is_not_read(tmp_path: Path) -> None:
    secret = tmp_path / "secret.proto"
    secret.write_bytes(b"not yours")
    mounted = tmp_path / "mounted"
    mounted.mkdir()
    (mounted / "link.proto").symlink_to(secret)

    assert ProtoStore(tmp_path / "uploads", mounted).scan() == []
