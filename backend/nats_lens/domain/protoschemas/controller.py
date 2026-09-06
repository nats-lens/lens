"""Protobuf descriptors and subject rules. OWNER: agent B4-decode-schemas."""

from __future__ import annotations

import uuid

from litestar import Controller, delete, get, patch, post
from litestar.datastructures import State
from litestar.di import NamedDependency
from litestar.exceptions import ClientException, NotFoundException, ValidationException
from litestar.params import FromPath, FromQuery
from sqlalchemy.ext.asyncio import AsyncSession

from nats_lens.codec.schemas import DecodePreview
from nats_lens.domain.protoschemas.repository import SchemaRepository
from nats_lens.domain.protoschemas.schemas import (
    DecodePreviewResult,
    DescriptorDetail,
    DescriptorSummary,
    DescriptorUpload,
    ResolutionStep,
    ScanReport,
    SubjectRuleIn,
    SubjectRuleOut,
    TypeChoice,
    UnmappedSubject,
)
from nats_lens.domain.protoschemas.service import (
    Conflict,
    NotFound,
    SchemaError,
    SchemaService,
    resolution_steps,
)


def _service(session: AsyncSession, state: State) -> SchemaService:
    """The store comes from settings, so both directories are one decision made
    at boot rather than a default repeated per call site."""
    return SchemaService(SchemaRepository(session), store=state.proto_store)


def _raise(error: SchemaError) -> None:
    """Turn a service failure into the status the UI knows how to render.

    Every one of these messages is written to be shown to the operator verbatim,
    the way `Unavailable.fix` is -- a schema that will not compile is a thing they
    can fix, not an internal error.
    """
    if isinstance(error, NotFound):
        raise NotFoundException(detail=str(error))
    if isinstance(error, Conflict):
        raise ClientException(status_code=409, detail=str(error))
    raise ValidationException(detail=str(error))


class SchemasController(Controller):
    path = "/api/schemas"
    tags = ["schemas"]

    @get("/descriptors")
    async def list_descriptors(
        self, session: NamedDependency[AsyncSession], state: State
    ) -> list[DescriptorSummary]:
        return await _service(session, state).list_descriptors()

    @post("/descriptors", status_code=201, summary="A .proto source or a FileDescriptorSet")
    async def upload_descriptor(
        self, session: NamedDependency[AsyncSession], state: State, data: DescriptorUpload
    ) -> DescriptorDetail:
        try:
            return await _service(session, state).upload_descriptor(data)
        except SchemaError as exc:
            _raise(exc)
            raise

    @get("/descriptors/{descriptor_id:uuid}")
    async def get_descriptor(
        self,
        session: NamedDependency[AsyncSession],
        state: State,
        descriptor_id: FromPath[uuid.UUID],
    ) -> DescriptorDetail:
        try:
            return await _service(session, state).get_descriptor(descriptor_id)
        except SchemaError as exc:
            _raise(exc)
            raise

    @delete("/descriptors/{descriptor_id:uuid}")
    async def delete_descriptor(
        self,
        session: NamedDependency[AsyncSession],
        state: State,
        descriptor_id: FromPath[uuid.UUID],
    ) -> None:
        try:
            await _service(session, state).delete_descriptor(descriptor_id)
        except SchemaError as exc:
            _raise(exc)
            raise

    @get("/rules")
    async def list_rules(
        self,
        session: NamedDependency[AsyncSession],
        state: State,
        server_id: FromQuery[uuid.UUID | None] = None,
    ) -> list[SubjectRuleOut]:
        """Most specific first -- the order the chain considers them, not the order added."""
        return await _service(session, state).list_rules(server_id)

    @post("/rules", status_code=201)
    async def create_rule(
        self, session: NamedDependency[AsyncSession], state: State, data: SubjectRuleIn
    ) -> SubjectRuleOut:
        try:
            return await _service(session, state).create_rule(data)
        except SchemaError as exc:
            _raise(exc)
            raise

    @patch("/rules/{rule_id:uuid}")
    async def update_rule(
        self,
        session: NamedDependency[AsyncSession],
        state: State,
        rule_id: FromPath[uuid.UUID],
        data: SubjectRuleIn,
    ) -> SubjectRuleOut:
        try:
            return await _service(session, state).update_rule(rule_id, data)
        except SchemaError as exc:
            _raise(exc)
            raise

    @delete("/rules/{rule_id:uuid}")
    async def delete_rule(
        self, session: NamedDependency[AsyncSession], state: State, rule_id: FromPath[uuid.UUID]
    ) -> None:
        try:
            await _service(session, state).delete_rule(rule_id)
        except SchemaError as exc:
            _raise(exc)
            raise

    @get("/unmapped", summary="Subjects seen on the wire that no rule claims")
    async def unmapped(
        self,
        session: NamedDependency[AsyncSession],
        state: State,
        server_id: FromQuery[uuid.UUID | None] = None,
    ) -> list[UnmappedSubject]:
        """`sampled`: what this process watched go past, not a server-side total."""
        return _service(session, state).unmapped(server_id)

    @get("/types", summary="Every registered message type, flat, for the rule picker")
    async def list_types(
        self, session: NamedDependency[AsyncSession], state: State
    ) -> list[TypeChoice]:
        """One list rather than per-descriptor, because a rule is written by
        knowing the type name, not the file it happened to arrive in."""
        return await _service(session, state).list_types()

    @post("/scan", summary="Re-read the upload and mounted directories")
    async def scan(self, session: NamedDependency[AsyncSession], state: State) -> ScanReport:
        """Runs on start too. Idempotent: a file whose bytes have not changed is
        left alone, so this is cheap enough to press whenever the mount changes."""
        return await _service(session, state).scan_sources()

    @get("/resolution-order", summary="The five steps, as the Schemas screen lists them")
    async def resolution_order(self) -> list[ResolutionStep]:
        return resolution_steps()

    @post("/decode", summary="Run the chain on arbitrary bytes without publishing anything")
    async def decode(
        self, session: NamedDependency[AsyncSession], state: State, data: DecodePreview
    ) -> DecodePreviewResult:
        try:
            return await _service(session, state).decode_preview(data)
        except SchemaError as exc:
            _raise(exc)
            raise
