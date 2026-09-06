"""Protobuf descriptors, and the only file in nats-lens that imports the runtime.

Everything else in `codec/` works on bytes. Confining `google.protobuf` here means
the chain, the wire walker and the sniffer stay importable and testable without
it, and the narrow surface the rest of the codebase sees is `DescriptorIndex.get`
and `DescriptorIndex.decode_as`.

Two ways in, because both are how teams actually hold their schemas:

  a `.proto` source, compiled here by the protoc that ships inside grpcio-tools,
  so nothing has to be installed on the host; or

  a pre-compiled `FileDescriptorSet`, which is what a buf or protoc build already
  produces in CI.

Both end up as the same bytes in `descriptor.file_descriptor_set`, and a pool is
rebuilt from those bytes whenever the registry changes. The pool is never mutated
in place -- a descriptor pool cannot forget a file, so re-registering a changed
`.proto` would otherwise mean a restart.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Final

import msgspec
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.descriptor import FieldDescriptor
from google.protobuf.message import Message

from nats_lens.codec import wire
from nats_lens.codec.schemas import DecodedField

# protobuf builds its generated message classes at import time and ships no type
# stubs, so `descriptor_pb2.FileDescriptorSet` is invisible to a static checker
# even though it is there. Alias the two this module needs, once.
FileDescriptorSet: Any = descriptor_pb2.FileDescriptorSet  # ty: ignore[unresolved-attribute]
FileDescriptorProto: Any = descriptor_pb2.FileDescriptorProto  # ty: ignore[unresolved-attribute]

_MAX_VALUE_CHARS: Final = 512
_ELLIPSIS: Final = "…"
_MIDDOT: Final = "·"

# protoc is a C library called in-process, so it writes to the real file
# descriptors and there is exactly one of it. Serialise access and capture the
# descriptors around each call; otherwise its diagnostics land in the server log
# instead of in the 400 the operator needs to read.
_PROTOC_LOCK: Final = threading.Lock()

_SCALAR_TYPE_NAMES: Final[dict[int, str]] = {
    FieldDescriptor.TYPE_DOUBLE: "double",
    FieldDescriptor.TYPE_FLOAT: "float",
    FieldDescriptor.TYPE_INT64: "int64",
    FieldDescriptor.TYPE_UINT64: "uint64",
    FieldDescriptor.TYPE_INT32: "int32",
    FieldDescriptor.TYPE_FIXED64: "fixed64",
    FieldDescriptor.TYPE_FIXED32: "fixed32",
    FieldDescriptor.TYPE_BOOL: "bool",
    FieldDescriptor.TYPE_STRING: "string",
    FieldDescriptor.TYPE_GROUP: "group",
    FieldDescriptor.TYPE_MESSAGE: "message",
    FieldDescriptor.TYPE_BYTES: "bytes",
    FieldDescriptor.TYPE_UINT32: "uint32",
    FieldDescriptor.TYPE_ENUM: "enum",
    FieldDescriptor.TYPE_SFIXED32: "sfixed32",
    FieldDescriptor.TYPE_SFIXED64: "sfixed64",
    FieldDescriptor.TYPE_SINT32: "sint32",
    FieldDescriptor.TYPE_SINT64: "sint64",
}


class DescriptorError(Exception):
    """A descriptor could not be compiled, parsed or registered. Carries the fix."""


class TypeMismatch(Exception):
    """The bytes are not a message of the type that was asked for."""


class TypeInfo(msgspec.Struct, frozen=True):
    full_name: str
    field_names: tuple[str, ...]


class CompiledDescriptor(msgspec.Struct, frozen=True):
    """What an upload turned into: the stored bytes and what they declare."""

    file_descriptor_set: bytes
    package: str
    source_filename: str
    protoc_version: str | None
    types: tuple[TypeInfo, ...]


class MessageDecode(msgspec.Struct, frozen=True):
    fields: tuple[DecodedField, ...] = ()
    warnings: tuple[str, ...] = ()


# --------------------------------------------------------------------- compile


def protoc_version() -> str | None:
    """The version string of the bundled compiler, for the descriptor's provenance."""
    code, output = _run_protoc(["protoc", "--version"])
    if code != 0:
        return None
    # `libprotoc 27.3` -- the number is what the design's descriptor card shows.
    parts = output.strip().split()
    return parts[-1] if parts else None


def compile_proto(filename: str, source: bytes) -> bytes:
    """Compile one `.proto` into a self-contained FileDescriptorSet.

    `--include_imports` is not optional here. A descriptor set without its
    dependencies cannot build a pool on a machine that never saw the imports, and
    "it worked in CI" is exactly the failure this tool exists to prevent.
    """
    name = _safe_filename(filename)
    with tempfile.TemporaryDirectory(prefix="nats-lens-proto-") as tmp:
        root = Path(tmp)
        (root / name).write_bytes(source)
        out = root / "descriptor.pb"
        code, output = _run_protoc(
            [
                "protoc",
                f"--proto_path={root}",
                f"--proto_path={_well_known_types_path()}",
                f"--descriptor_set_out={out}",
                "--include_imports",
                name,
            ]
        )
        if code != 0 or not out.exists():
            # protoc reports absolute paths, and the absolute path here is a temp
            # directory that means nothing to whoever uploaded the file.
            reported = output.replace(f"{root}/", "").strip()
            raise DescriptorError(
                f"protoc could not compile {name}. It reported:\n{reported or 'no output'}\n"
                "Uploads are compiled on their own, so a .proto that imports another file of "
                "yours has to be uploaded as a FileDescriptorSet built with --include_imports."
            )
        return out.read_bytes()


def compile_proto_in_tree(root: Path, relative: str) -> bytes:
    """Compile a `.proto` that sits inside a directory of its own imports.

    The difference from `compile_proto` is the proto path: protoc is pointed at
    the whole tree, so `import "common/money.proto"` resolves against the files
    beside it. This is what a mounted directory buys over an upload -- there, one
    file arrives on its own and an import has nowhere to resolve to, which is why
    that path asks for a pre-built descriptor set instead.
    """
    out_dir = tempfile.TemporaryDirectory(prefix="nats-lens-proto-")
    try:
        out = Path(out_dir.name) / "descriptor.pb"
        code, output = _run_protoc(
            [
                "protoc",
                f"--proto_path={root}",
                f"--proto_path={_well_known_types_path()}",
                f"--descriptor_set_out={out}",
                "--include_imports",
                relative,
            ]
        )
        if code != 0 or not out.exists():
            reported = output.replace(f"{root}/", "").strip()
            raise DescriptorError(
                f"protoc could not compile {relative}. It reported:\n{reported or 'no output'}\n"
                "Imports are resolved against the mounted directory, so a missing one means the "
                "file it names is not in the tree."
            )
        return out.read_bytes()
    finally:
        out_dir.cleanup()


def inspect_descriptor_set(data: bytes, *, source_filename: str) -> CompiledDescriptor:
    """Read a FileDescriptorSet and say which package and types it registers.

    Only the primary file's messages are reported. A set built with
    `--include_imports` also carries `google/protobuf/timestamp.proto` and every
    other dependency, and listing those as this descriptor's types would both
    misrepresent the upload and collide with the next descriptor that imports the
    same file -- `message_type.full_name` is unique.
    """
    file_set = FileDescriptorSet()
    try:
        file_set.ParseFromString(data)
    except Exception as exc:
        raise DescriptorError(
            "This file is not a FileDescriptorSet. Compile it with "
            "`protoc --descriptor_set_out=... --include_imports`, or upload the .proto source "
            "and let nats-lens compile it."
        ) from exc

    if not file_set.file:
        raise DescriptorError("The FileDescriptorSet is empty -- it declares no files.")

    primary = _primary_file(file_set, source_filename)
    types = tuple(
        TypeInfo(full_name=full_name, field_names=field_names)
        for full_name, field_names in _walk_messages(primary.package, primary.message_type)
    )
    if not types:
        raise DescriptorError(
            f"{primary.name} declares no message types, so nothing could ever be decoded with it."
        )

    return CompiledDescriptor(
        file_descriptor_set=data,
        package=primary.package or primary.name,
        source_filename=source_filename,
        protoc_version=None,
        types=types,
    )


# ----------------------------------------------------------------------- index


class DescriptorIndex:
    """Every registered descriptor, in one pool, keyed by message full name.

    Built from stored bytes and thrown away when they change. Cheap enough to
    rebuild on every registry edit, which is what keeps a re-uploaded `.proto`
    from needing a restart.
    """

    __slots__ = ("_borrowed", "_classes", "_declared", "_files", "_imported_only", "_pool")

    def __init__(self) -> None:
        self._pool = descriptor_pool.DescriptorPool()  # ty: ignore[possibly-missing-implicit-call]
        self._files: dict[str, bytes] = {}
        self._borrowed: set[str] = set()
        self._declared: set[str] = set()
        self._classes: dict[str, type[Message]] = {}
        self._imported_only: set[str] = set()

    def add(self, data: bytes, *, imported_only: bool = False, label: str = "descriptor") -> None:
        """Register a FileDescriptorSet. Files already present are left alone."""
        file_set = FileDescriptorSet()
        try:
            file_set.ParseFromString(data)
        except Exception as exc:
            raise DescriptorError(f"{label} is not a readable FileDescriptorSet.") from exc

        pending = [f for f in file_set.file if not self._already_have(f)]
        # Dependencies must go in before the files that import them, and a set
        # merged from several uploads is not necessarily in that order.
        while pending:
            ready = [f for f in pending if all(dep in self._files for dep in f.dependency)]
            if not ready:
                for f in pending:
                    for dep in f.dependency:
                        if dep not in self._files and not self._borrow_from_runtime(dep):
                            raise DescriptorError(
                                f"{label} imports {dep}, which is not in the descriptor set and "
                                "is not a well-known type. Rebuild it with --include_imports."
                            )
                continue
            for f in ready:
                self._add_file(f, label=label)
                pending.remove(f)

        if imported_only:
            for f in file_set.file:
                self._imported_only.update(
                    name for name, _ in _walk_messages(f.package, f.message_type)
                )

    def get(self, full_name: str) -> type[Message] | None:
        """The generated message class for `full_name`, or None if nothing declares it."""
        if (cached := self._classes.get(full_name)) is not None:
            return cached
        try:
            descriptor = self._pool.FindMessageTypeByName(full_name)
        except KeyError:
            return None
        message_class = message_factory.GetMessageClass(descriptor)
        self._classes[full_name] = message_class
        return message_class

    def decode_as(self, full_name: str, payload: bytes) -> MessageDecode | None:
        """Decode `payload` as `full_name`.

        None means no descriptor declares that type -- the chain moves on. A
        `TypeMismatch` means the type is known and the bytes are not it, which is
        a different thing and worth telling the operator about.
        """
        message_class = self.get(full_name)
        if message_class is None:
            return None
        return decode_message(message_class, payload)

    @property
    def type_names(self) -> tuple[str, ...]:
        """Every message type the pool can resolve, whether or not it has been used."""
        return tuple(sorted(self._declared))

    def is_imported_only(self, full_name: str) -> bool:
        return full_name in self._imported_only

    def _already_have(self, file_proto: Any) -> bool:
        existing = self._files.get(file_proto.name)
        if existing is None:
            return False
        if file_proto.name in self._borrowed:
            # The runtime's own copy of a well-known type is already in the pool
            # and cannot be replaced. It is also the same file, so nothing is lost.
            return True
        if existing != file_proto.SerializeToString():
            raise DescriptorError(
                f"Two descriptors define {file_proto.name} differently. Remove the older one, or "
                "upload a single descriptor set that contains both."
            )
        return True

    def _add_file(self, file_proto: Any, *, label: str) -> None:
        try:
            self._pool.Add(file_proto)
        except Exception as exc:
            raise DescriptorError(f"{label} could not be registered: {exc}") from exc
        self._files[file_proto.name] = file_proto.SerializeToString()
        self._declared.update(
            name for name, _ in _walk_messages(file_proto.package, file_proto.message_type)
        )

    def _borrow_from_runtime(self, name: str) -> bool:
        """Pull a well-known type out of the runtime's own pool.

        A descriptor set built without `--include_imports` still usually only
        misses `google/protobuf/*.proto`, which this process already has compiled
        in. Taking it from there turns a hard failure into a working upload.
        """
        _import_well_known(name)
        try:
            file_descriptor = descriptor_pool.Default().FindFileByName(name)
        except KeyError:
            return False
        proto = FileDescriptorProto()
        file_descriptor.CopyToProto(proto)
        for dep in proto.dependency:
            if dep not in self._files and not self._borrow_from_runtime(dep):
                return False
        self._add_file(proto, label="the protobuf runtime")
        self._borrowed.add(name)
        return True


# ---------------------------------------------------------------------- decode


def decode_message(message_class: type[Message], payload: bytes) -> MessageDecode:
    """Parse `payload` and render the fields it actually carried."""
    message = message_class()
    descriptor: Any = message_class.DESCRIPTOR
    try:
        consumed = message.MergeFromString(payload)
    except Exception as exc:
        raise TypeMismatch(f"{descriptor.full_name}: {exc}") from exc
    if consumed != len(payload):
        raise TypeMismatch(f"{descriptor.full_name}: {len(payload) - consumed} trailing bytes")

    present = wire.field_numbers(payload) | {f.number for f, _ in message.ListFields()}
    if payload and not present & {f.number for f in descriptor.fields}:
        # Every byte parsed, and not one of them belonged to this schema. That is
        # not a decode, it is a coincidence, and announcing it as the named type
        # would put a confident wrong answer on the screen.
        raise TypeMismatch(
            f"{descriptor.full_name}: none of the fields on the wire are declared by this type"
        )

    fields = tuple(
        _render_field(field, message)
        for field in sorted(descriptor.fields, key=lambda f: f.number)
        if field.number in present
    )
    return MessageDecode(fields=fields, warnings=_unknown_field_warnings(descriptor, present))


def _render_field(field: FieldDescriptor, message: Message) -> DecodedField:
    value = getattr(message, field.name)
    repeated = field.is_repeated

    if repeated and field.message_type is not None and field.message_type.GetOptions().map_entry:
        entry = field.message_type.fields_by_name["value"]
        rendered = "{" + ", ".join(f"{k!r}: {_value_of(entry, v)}" for k, v in value.items()) + "}"
    elif repeated:
        rendered = "[" + ", ".join(_value_of(field, item) for item in value) + "]"
    else:
        rendered = _value_of(field, value)

    return DecodedField(
        name=field.name,
        field_number=field.number,
        type_name=_type_name(field),
        value=_clip(rendered),
        repeated=repeated,
    )


def _type_name(field: FieldDescriptor) -> str:
    if field.type == FieldDescriptor.TYPE_MESSAGE and field.message_type is not None:
        if field.message_type.GetOptions().map_entry:
            # protoc rewrites `map<string, int64>` into a repeated synthetic entry
            # message. Showing `LabelsEntry` would be showing the compiler's
            # bookkeeping instead of what the author wrote.
            key = field.message_type.fields_by_name["key"]
            value = field.message_type.fields_by_name["value"]
            return f"map<{_type_name(key)}, {_type_name(value)}>"
        return field.message_type.full_name
    return _SCALAR_TYPE_NAMES.get(field.type, "unknown")


def _value_of(field: FieldDescriptor, value: Any) -> str:
    if field.type == FieldDescriptor.TYPE_ENUM and field.enum_type is not None:
        name = field.enum_type.values_by_number.get(value)
        # The design renders enums as `SHIPPED (3)`: the label an operator reads
        # plus the number that is actually on the wire.
        return f"{name.name} ({value})" if name is not None else str(value)
    if field.type == FieldDescriptor.TYPE_MESSAGE:
        return _nested(value)
    return _scalar(value)


def _nested(message: Any) -> str:
    from google.protobuf import text_format

    try:
        rendered = text_format.MessageToString(message, as_one_line=True)
    except Exception:
        return "{...}"
    return f"{{ {rendered} }}" if rendered else "{}"


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bytes):
        text = wire.as_text(value)
        return f'"{text}"' if text is not None else f"{len(value)} bytes {_MIDDOT} 0x{value.hex()}"
    return str(value)


def _unknown_field_warnings(descriptor: Any, present: frozenset[int] | set[int]) -> tuple[str, ...]:
    declared = {f.number for f in descriptor.fields}
    unknown = sorted(present - declared)
    if not unknown:
        return ()
    numbers = ", ".join(str(n) for n in unknown[:8])
    more = f" and {len(unknown) - 8} more" if len(unknown) > 8 else ""
    return (
        f"The message carries field{'s' if len(unknown) > 1 else ''} {numbers}{more}, which "
        f"{descriptor.full_name} does not declare. The publisher is probably ahead of the "
        "descriptor registered here.",
    )


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_VALUE_CHARS else text[:_MAX_VALUE_CHARS] + _ELLIPSIS


# ----------------------------------------------------------------------- protoc


def _run_protoc(args: list[str]) -> tuple[int, str]:
    """Run the bundled protoc, capturing what it writes to the real descriptors."""
    from grpc_tools import protoc

    with _PROTOC_LOCK, tempfile.TemporaryFile() as sink:
        sys.stdout.flush()
        sys.stderr.flush()
        saved_out, saved_err = os.dup(1), os.dup(2)
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            code = protoc.main(args)
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)
        sink.seek(0)
        return code, sink.read().decode(errors="replace")


def _import_well_known(name: str) -> None:
    """Make the runtime's default pool aware of `google/protobuf/x.proto`.

    That pool is populated as a side effect of importing generated modules, so a
    well-known type this process has never touched is simply not in it yet.
    """
    if not name.startswith("google/protobuf/") or not name.endswith(".proto"):
        return
    module = "google.protobuf." + Path(name).stem + "_pb2"
    try:
        importlib.import_module(module)
    except ImportError:
        return


def _well_known_types_path() -> str:
    from grpc_tools import protoc

    return protoc._get_resource_file_name("grpc_tools", "_proto")


def _safe_filename(filename: str) -> str:
    """A basename protoc will accept, because the upload is written to a temp dir."""
    name = Path(filename).name.strip() or "schema.proto"
    if not name.endswith(".proto"):
        name = f"{name}.proto"
    return name


def _primary_file(file_set: Any, source_filename: str) -> Any:
    """The file the upload is about, as opposed to the imports carried alongside it."""
    wanted = Path(source_filename).name
    for file_proto in file_set.file:
        if Path(file_proto.name).name == wanted:
            return file_proto
    stem = Path(wanted).stem
    for file_proto in file_set.file:
        if Path(file_proto.name).stem == stem:
            return file_proto
    # protoc emits dependencies first, so the file that was compiled is last.
    return file_set.file[-1]


def _walk_messages(
    package: str, messages: Any, prefix: str = ""
) -> list[tuple[str, tuple[str, ...]]]:
    """Every message a file declares, nested types included, as dotted full names."""
    found: list[tuple[str, tuple[str, ...]]] = []
    for message in messages:
        parts = [part for part in (package, prefix, message.name) if part]
        full_name = ".".join(parts)
        if message.options.map_entry:
            # A map field's synthetic entry type is an implementation detail.
            continue
        found.append((full_name, tuple(field.name for field in message.field)))
        nested_prefix = ".".join(part for part in (prefix, message.name) if part)
        found.extend(_walk_messages(package, message.nested_type, nested_prefix))
    return found
