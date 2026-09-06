"""Database access for the descriptor registry and the subject rules.

Thin on purpose. Everything the Schemas screen shows that is *not* in these
tables -- rule hit counts, when a type was last seen, which subjects nobody has
mapped -- is `sampled` provenance and lives in the process, so it is the service's
business and never appears here as a column.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from nats_lens.db.models import Descriptor, MessageType, SubjectRule


class SchemaRepository:
    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ descriptors

    async def list_descriptors(self) -> Sequence[Descriptor]:
        result = await self._session.execute(
            select(Descriptor).options(selectinload(Descriptor.types)).order_by(Descriptor.package)
        )
        return result.scalars().all()

    async def get_descriptor(self, descriptor_id: uuid.UUID) -> Descriptor | None:
        result = await self._session.execute(
            select(Descriptor)
            .options(selectinload(Descriptor.types))
            .where(Descriptor.id == descriptor_id)
        )
        return result.scalar_one_or_none()

    async def descriptor_by_package(self, package: str) -> Descriptor | None:
        result = await self._session.execute(
            select(Descriptor)
            .options(selectinload(Descriptor.types))
            .where(Descriptor.package == package)
        )
        return result.scalar_one_or_none()

    async def add_descriptor(
        self,
        *,
        package: str,
        source_filename: str,
        file_descriptor_set: bytes,
        protoc_version: str | None,
        imported_only: bool,
        note: str | None,
        types: Sequence[tuple[str, tuple[str, ...]]],
        origin: str = "upload",
        source_path: str | None = None,
        content_sha256: str | None = None,
    ) -> Descriptor:
        descriptor = Descriptor(
            package=package,
            source_filename=source_filename,
            file_descriptor_set=file_descriptor_set,
            protoc_version=protoc_version,
            imported_only=imported_only,
            note=note,
            origin=origin,
            source_path=source_path,
            content_sha256=content_sha256,
        )
        descriptor.types = [
            MessageType(full_name=full_name, field_names=list(names), field_count=len(names))
            for full_name, names in types
        ]
        self._session.add(descriptor)
        await self._session.flush()
        await self._session.refresh(descriptor, attribute_names=["types", "created_at"])
        return descriptor

    async def delete_descriptor(self, descriptor_id: uuid.UUID) -> bool:
        row = await self.get_descriptor(descriptor_id)
        if row is None:
            return False
        # Through the ORM rather than a bare DELETE, so the message_type cascade
        # runs the same way whichever backend is underneath.
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def type_owner(self, full_name: str) -> MessageType | None:
        """Which descriptor declares `full_name`, if any. Type names are unique."""
        result = await self._session.execute(
            select(MessageType)
            .options(selectinload(MessageType.descriptor))
            .where(MessageType.full_name == full_name)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------ rules

    async def list_rules(self, server_id: uuid.UUID | None = None) -> Sequence[SubjectRule]:
        """Rules in play for one server, or every rule when no server is named.

        A rule with no `server_id` applies everywhere, so a server's list is its
        own rules plus the global ones -- which is also exactly the set the chain
        is given for that server.
        """
        statement = select(SubjectRule)
        if server_id is not None:
            statement = statement.where(
                (SubjectRule.server_id == server_id) | (SubjectRule.server_id.is_(None))
            )
        # Insertion order is the last tie-break in the specificity ordering, so it
        # has to be stable across processes: created_at, then id.
        return (
            (
                await self._session.execute(
                    statement.order_by(SubjectRule.created_at, SubjectRule.id)
                )
            )
            .scalars()
            .all()
        )

    async def get_rule(self, rule_id: uuid.UUID) -> SubjectRule | None:
        result = await self._session.execute(select(SubjectRule).where(SubjectRule.id == rule_id))
        return result.scalar_one_or_none()

    async def add_rule(
        self,
        *,
        pattern: str,
        type_full_name: str,
        server_id: uuid.UUID | None,
        precedence: int,
        enabled: bool,
    ) -> SubjectRule:
        rule = SubjectRule(
            pattern=pattern,
            type_full_name=type_full_name,
            server_id=server_id,
            precedence=precedence,
            enabled=enabled,
        )
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def save_rule(self, rule: SubjectRule) -> SubjectRule:
        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def delete_rule(self, rule_id: uuid.UUID) -> bool:
        row = await self.get_rule(rule_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def rule_counts_by_type(self) -> dict[str, int]:
        """How many rules point at each message type. One query for the whole list."""
        result = await self._session.execute(
            select(SubjectRule.type_full_name, func.count()).group_by(SubjectRule.type_full_name)
        )
        return {name: count for name, count in result.all()}
