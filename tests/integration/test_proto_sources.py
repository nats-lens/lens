"""Definitions from a mounted directory.

The behaviour worth a real protoc: a mounted file is compiled *inside its own
tree*, so `import "common/money.proto"` resolves against the files beside it.
That is the whole reason mounting exists alongside uploading -- an upload arrives
on its own and has nowhere to resolve an import to.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.schemas import Origin, ScanStatus
from nats_lens.domain.protoschemas.service import Conflict, SchemaService
from nats_lens.domain.protoschemas.store import ProtoStore

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

MONEY = b"""
syntax = "proto3";
package acme.common;
message Money { string currency = 1; int64 cents = 2; }
"""

INVOICE = b"""
syntax = "proto3";
package acme.billing.v1;
import "common/money.proto";
message Invoice { string number = 1; acme.common.Money total = 2; }
"""

BROKEN = b'syntax = "proto3"; package acme.bad; message {{{'


def _tree(root: Path) -> Path:
    mounted = root / "mounted"
    (mounted / "common").mkdir(parents=True)
    (mounted / "common" / "money.proto").write_bytes(MONEY)
    (mounted / "invoice.proto").write_bytes(INVOICE)
    return mounted


@pytest.fixture
async def scanner(database_url: str, tmp_path: Path):
    """A service over a real migrated database and a temp pair of directories.

    The registry is emptied first. `database_url` is session-scoped, and a
    descriptor left by a neighbouring test would be a mounted row whose file is
    in some other test's temp directory -- which this suite's own logic would
    then correctly report as removed, drowning the assertion.
    """
    from sqlalchemy import delete

    from nats_lens.db.models import Descriptor
    from nats_lens.db.session import make_engine, make_session_factory

    engine = make_engine(database_url)
    factory = make_session_factory(engine)
    async with factory() as session:
        await session.execute(delete(Descriptor))
        await session.commit()

    upload = tmp_path / "uploads"
    upload.mkdir()
    sessions = []

    async def build(mounted: Path | None):
        session = factory()
        await session.__aenter__()
        sessions.append(session)
        return SchemaService(SchemaRepository(session), store=ProtoStore(upload, mounted)), session

    yield build, upload

    for session in sessions:
        await session.close()
    await engine.dispose()


async def test_a_mounted_file_compiles_against_its_neighbours(scanner, tmp_path: Path) -> None:
    build, _ = scanner
    service, session = await build(_tree(tmp_path))

    report = await service.scan_sources()
    await session.commit()

    packages = {e.package for e in report.entries if e.status is ScanStatus.REGISTERED}
    assert {"acme.common", "acme.billing.v1"} <= packages, (
        "the import resolved against the tree; compiled alone it could not have"
    )
    assert report.mount_dir_present is True
    assert all(e.origin is Origin.MOUNTED for e in report.entries)

    types = {t.full_name for t in await service.list_types()}
    assert {"acme.billing.v1.Invoice", "acme.common.Money"} <= types


async def test_a_second_scan_changes_nothing(scanner, tmp_path: Path) -> None:
    """Idempotent by digest, which is what makes scanning on every boot cheap."""
    build, _ = scanner
    service, session = await build(_tree(tmp_path))
    await service.scan_sources()
    await session.commit()

    again = await service.scan_sources()
    await session.commit()

    assert {e.status for e in again.entries} == {ScanStatus.UNCHANGED}


async def test_one_bad_file_does_not_stop_the_others(scanner, tmp_path: Path) -> None:
    build, _ = scanner
    mounted = _tree(tmp_path)
    (mounted / "broken.proto").write_bytes(BROKEN)
    service, session = await build(mounted)

    report = await service.scan_sources()
    await session.commit()

    failed = [e for e in report.entries if e.status is ScanStatus.FAILED]
    assert [e.path for e in failed] == ["broken.proto"]
    assert failed[0].detail and "protoc" in failed[0].detail
    assert any(e.package == "acme.billing.v1" for e in report.entries)


async def test_a_mounted_descriptor_cannot_be_deleted_from_the_ui(scanner, tmp_path: Path) -> None:
    """The tree is the source of truth there; the row would come back next scan."""
    build, _ = scanner
    service, session = await build(_tree(tmp_path))
    await service.scan_sources()
    await session.commit()

    row = await service._repo.descriptor_by_package("acme.common")
    assert row is not None
    with pytest.raises(Conflict, match="mounted"):
        await service.delete_descriptor(row.id)


async def test_a_file_removed_from_the_mount_is_dropped(scanner, tmp_path: Path) -> None:
    build, _ = scanner
    mounted = _tree(tmp_path)
    service, session = await build(mounted)
    await service.scan_sources()
    await session.commit()

    (mounted / "invoice.proto").unlink()
    report = await service.scan_sources()
    await session.commit()

    removed = [e for e in report.entries if e.status is ScanStatus.REMOVED]
    assert [e.package for e in removed] == ["acme.billing.v1"]
    assert await service._repo.descriptor_by_package("acme.billing.v1") is None


async def test_an_upload_is_written_to_the_upload_directory(scanner, tmp_path: Path) -> None:
    import base64

    from nats_lens.domain.protoschemas.schemas import DescriptorUpload

    build, upload_dir = scanner
    service, session = await build(None)

    detail = await service.upload_descriptor(
        DescriptorUpload(filename="money.proto", content_b64=base64.b64encode(MONEY).decode())
    )
    await session.commit()

    assert detail.origin is Origin.UPLOAD
    assert (upload_dir / "money.proto").read_bytes() == MONEY, (
        "an upload has to survive as a file, not only as a row"
    )


async def test_an_upload_cannot_shadow_a_mounted_package(scanner, tmp_path: Path) -> None:
    """It would not hold anyway: the next scan takes the package back."""
    import base64

    from nats_lens.domain.protoschemas.schemas import DescriptorUpload

    build, _ = scanner
    service, session = await build(_tree(tmp_path))
    await service.scan_sources()
    await session.commit()

    with pytest.raises(Conflict, match="mounted directory"):
        await service.upload_descriptor(
            DescriptorUpload(filename="money.proto", content_b64=base64.b64encode(MONEY).decode())
        )


async def test_an_upload_whose_file_is_gone_is_dropped_too(scanner, tmp_path: Path) -> None:
    """The two directories are the source of truth, uploads included."""
    import base64

    from nats_lens.domain.protoschemas.schemas import DescriptorUpload

    build, upload_dir = scanner
    service, session = await build(None)
    await service.upload_descriptor(
        DescriptorUpload(filename="money.proto", content_b64=base64.b64encode(MONEY).decode())
    )
    await session.commit()

    (upload_dir / "money.proto").unlink()
    report = await service.scan_sources()
    await session.commit()

    removed = [e for e in report.entries if e.status is ScanStatus.REMOVED]
    assert [(e.package, e.origin) for e in removed] == [("acme.common", Origin.UPLOAD)]
    assert "upload directory" in (removed[0].detail or "")


async def test_a_descriptor_with_no_recorded_path_survives_a_scan(scanner) -> None:
    """Registered before uploads were written to disk. Dropping these on the
    first scan after an upgrade would delete a working registry."""
    import base64

    from nats_lens.domain.protoschemas.schemas import DescriptorUpload

    build, _ = scanner
    service, session = await build(None)
    detail = await service.upload_descriptor(
        DescriptorUpload(filename="money.proto", content_b64=base64.b64encode(MONEY).decode())
    )
    row = await service._repo.get_descriptor(detail.id)
    assert row is not None
    row.source_path = None
    await session.commit()

    report = await service.scan_sources()
    await session.commit()

    assert not [e for e in report.entries if e.status is ScanStatus.REMOVED]
    assert await service._repo.descriptor_by_package("acme.common") is not None


async def test_deleting_an_upload_removes_its_file(scanner, tmp_path: Path) -> None:
    import base64

    from nats_lens.domain.protoschemas.schemas import DescriptorUpload

    build, upload_dir = scanner
    service, session = await build(None)
    detail = await service.upload_descriptor(
        DescriptorUpload(filename="money.proto", content_b64=base64.b64encode(MONEY).decode())
    )
    await session.commit()

    await service.delete_descriptor(detail.id)
    await session.commit()

    assert not (upload_dir / "money.proto").exists()
    assert uuid.UUID(str(detail.id))
