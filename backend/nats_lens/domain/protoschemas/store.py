"""Where proto definitions come from.

Two sources, one registry:

  * **uploaded** -- a file sent through the UI, written under `proto_upload_dir`
    (`/data/uploads/protos` in the image). It lives on disk rather than only in
    the database so it survives, backs up by being copied, and can be read by a
    person.
  * **mounted** -- a directory the operator mounts, scanned read-only. nats-lens
    never writes there, and never deletes from there: the files are the source of
    truth and the registry is a view of them.

The distinction is not cosmetic. A mounted file is compiled *inside its own
tree*, so `import "common/money.proto"` resolves against the files beside it. An
upload arrives alone and has nowhere to resolve an import to, which is why that
path asks for a descriptor set built with `--include_imports`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# The contract module owns `Origin`: it appears in API responses, and two enums
# spelling the same values is how they drift apart.
from nats_lens.domain.protoschemas.schemas import Origin

PROTO_SUFFIX = ".proto"
DESCRIPTOR_SUFFIXES = frozenset({".desc", ".pb", ".protoset", ".bin"})
READABLE_SUFFIXES = DESCRIPTOR_SUFFIXES | {PROTO_SUFFIX}

MAX_FILE_BYTES = 8 * 1024 * 1024
"""A descriptor set is kilobytes. Anything this size is a mistake, and reading it
into memory to find that out is the mistake repeated."""

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True, slots=True)
class ProtoFile:
    """One definition on disk, with everything needed to decide if it is new."""

    origin: Origin
    path: Path
    relative: str
    """The path as protoc should see it -- relative to the tree it was found in,
    so an import inside the tree resolves."""
    content: bytes
    is_descriptor_set: bool

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def safe_upload_name(filename: str) -> str:
    """A filename that cannot escape the upload directory or collide with a path.

    Names arrive from a browser, so `../../etc/passwd` and `a/b.proto` both have
    to become something that lands where it is meant to.
    """
    name = Path(filename).name.strip() or "descriptor.proto"
    cleaned = _SAFE_NAME.sub("_", name).lstrip(".")
    return cleaned or "descriptor.proto"


def is_descriptor_set(filename: str) -> bool:
    return Path(filename).suffix.lower() in DESCRIPTOR_SUFFIXES


class ProtoStore:
    """The two directories, and reading them."""

    def __init__(self, upload_dir: Path, mount_dir: Path | None) -> None:
        self.upload_dir = upload_dir
        self.mount_dir = mount_dir

    # ------------------------------------------------------------------ writing

    def save_upload(self, filename: str, content: bytes) -> Path:
        """Write an upload, replacing a file of the same name.

        Replacing rather than versioning: re-uploading a recompiled schema is the
        ordinary way one changes, and a directory that accumulates
        `orders.proto.1` teaches nobody anything.
        """
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        target = self.upload_dir / safe_upload_name(filename)
        target.write_bytes(content)
        return target

    def delete_upload(self, path: Path | None) -> None:
        """Forget an uploaded file. A missing file is not an error -- the registry
        row is what the caller is really removing."""
        if path is None:
            return
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.upload_dir / candidate
        # Never follow a stored path out of the upload directory.
        if self._within(candidate, self.upload_dir):
            candidate.unlink(missing_ok=True)

    # ------------------------------------------------------------------ reading

    def scan(self) -> list[ProtoFile]:
        """Every readable definition in both directories.

        Grouped by origin and then by path, so a scan report reads the same way
        twice and a diff between two runs is about the files, not the order.
        """
        found = [*self._walk(self.upload_dir, Origin.UPLOAD)]
        if self.mount_dir is not None:
            found += [*self._walk(self.mount_dir, Origin.MOUNTED)]
        return sorted(found, key=lambda f: (f.origin.value, f.relative))

    def _walk(self, root: Path | None, origin: Origin) -> Iterator[ProtoFile]:
        if root is None or not root.is_dir():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in READABLE_SUFFIXES:
                continue
            # A symlink out of the tree is not ours to read.
            if not self._within(path.resolve(), root.resolve()):
                continue
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            yield ProtoFile(
                origin=origin,
                path=path,
                relative=str(path.relative_to(root)),
                content=path.read_bytes(),
                is_descriptor_set=path.suffix.lower() in DESCRIPTOR_SUFFIXES,
            )

    @staticmethod
    def _within(candidate: Path, root: Path) -> bool:
        try:
            candidate.resolve().relative_to(root.resolve())
        except ValueError, OSError:
            return False
        return True
