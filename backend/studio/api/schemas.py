"""Model request/response REST (design "REST & SSE API contracts").

Bentuk JSON setiap model adalah kontrak lintas-bahasa dengan
``frontend/src/lib/types.ts`` (nama field snake_case, timestamp string
ISO-8601 UTC). Model domain dipakai ulang langsung dari ``core/models.py``
(``Command``, ``PatchEvent``, ``Predicate``, ``ColumnInfo``, ``ColumnProfile``,
...), ``store/dashboard_store.py`` (``DashboardSnapshot``) dan
``core/status.py`` (``ItemStatus``).

Pemetaan nama model → interface TypeScript:

========================  =============================
Python                    ``types.ts``
========================  =============================
``WorkspaceOut``          ``Workspace``
``DatasetOut``            ``Dataset``
``ColumnMappingOut``      ``ColumnMapping``
``RelationOut``           ``Relation``
``ChatSessionOut``        ``ChatSession``
``ErrorBody``             ``ErrorEnvelope["error"]``
lainnya                   nama yang sama
========================  =============================

Konverter ``from_record(...)`` mengubah record repositori
(``store/repos.py``) menjadi model API; field internal (``parquet_path``,
stack undo/redo, lineage) tidak pernah diekspos.

Field opsional TypeScript (``x?: T``) dihilangkan dari JSON bila bernilai
``None`` (lihat :class:`_OmitNoneModel`), sehingga tidak muncul sebagai
``null``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (
    AfterValidator,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from studio.core.models import (
    COMMAND_ADAPTER,
    Command,
    ColumnInfo,
    ColumnProfile,
    FilterSet,
    LogicalType,
    PatchEvent,
    Predicate,
    Scalar,
    StudioModel,
)
from studio.core.status import ItemStatus
from studio.store.dashboard_store import DashboardSnapshot
from studio.store.repos import (
    ChatSessionRecord,
    DashboardRecord,
    DatasetProfileRecord,
    DatasetRecord,
    QueryRecord,
    RelationRecord,
    WorkspaceRecord,
)

# ---------------------------------------------------------------------------
# Primitif
# ---------------------------------------------------------------------------


def _to_utc(value: datetime) -> datetime:
    """Normalisasi ke UTC aware (naive dianggap UTC); JSON → ``...Z``."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


#: Timestamp UTC; ``model_dump(mode="json")`` menghasilkan string ISO-8601.
UtcDateTime = Annotated[datetime, AfterValidator(_to_utc)]

JobState = Literal["queued", "running", "done", "failed"]
RelationCardinality = Literal["one_to_one", "one_to_many", "many_to_many"]
RelationStatus = Literal["candidate", "confirmed", "rejected", "deleted"]
RenderItemStatus = Literal["ok", "invalid", "stale", "error"]

#: Batas panjang nama Workspace / judul Dashboard.
MAX_NAME_LENGTH = 200


class _OmitNoneModel(StudioModel):
    """Model dengan field opsional TS (``x?: T``): key ``None`` dihilangkan saat serialisasi."""

    #: Nama field yang dihilangkan dari output bila nilainya ``None``.
    omit_if_none: ClassVar[frozenset[str]] = frozenset()

    @model_serializer(mode="wrap")
    def _omit_none(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        data = handler(self)
        for name in self.omit_if_none:
            key = name
            if info.by_alias:
                field = type(self).model_fields.get(name)
                if field is not None and field.serialization_alias:
                    key = field.serialization_alias
            if key in data and data[key] is None:
                del data[key]
        return data


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class ErrorBody(StudioModel):
    """Isi ``error`` pada envelope ``{"error": {code, message, details}}``."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(StudioModel):
    error: ErrorBody


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class WorkspaceOut(StudioModel):
    """``Workspace`` (Req 1.1)."""

    id: str
    owner_id: str
    name: str
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @classmethod
    def from_record(cls, record: WorkspaceRecord) -> WorkspaceOut:
        return cls(
            id=record.id,
            owner_id=record.owner_id,
            name=record.name,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class CreateWorkspaceRequest(StudioModel):
    """``POST /workspaces``; nama di-trim dan tidak boleh kosong."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class RenameWorkspaceRequest(CreateWorkspaceRequest):
    """``PATCH /workspaces/{ws}``."""


class DeleteWorkspaceRequest(StudioModel):
    """``DELETE /workspaces/{ws}``; ``confirm_name`` dibandingkan apa adanya dengan nama."""

    confirm_name: str


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


class ColumnMappingOut(StudioModel):
    original: str
    normalized: str


class DatasetOut(StudioModel):
    """``Dataset``; ``parquet_path`` sengaja tidak diekspos."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, serialize_by_alias=True)

    id: str
    workspace_id: str
    owner_id: str
    upload_id: str | None
    table_name: str
    source_name: str
    sheet_name: str | None
    #: ``schema`` membayangi atribut ``BaseModel``; dipetakan lewat alias.
    schema_: list[ColumnInfo] = Field(alias="schema")
    column_mapping: list[ColumnMappingOut]
    row_count: int = Field(ge=0)
    data_version: int = Field(ge=1)
    data_updated_at: UtcDateTime
    privacy_no_samples: bool
    created_at: UtcDateTime

    @classmethod
    def from_record(cls, record: DatasetRecord) -> DatasetOut:
        return cls(
            id=record.id,
            workspace_id=record.workspace_id,
            owner_id=record.owner_id,
            upload_id=record.upload_id,
            table_name=record.table_name,
            source_name=record.source_name,
            sheet_name=record.sheet_name,
            schema_=list(record.schema),
            column_mapping=[
                ColumnMappingOut(original=m.original, normalized=m.normalized)
                for m in record.column_mapping
            ],
            row_count=record.row_count,
            data_version=record.data_version,
            data_updated_at=record.data_updated_at,
            privacy_no_samples=record.privacy_no_samples,
            created_at=record.created_at,
        )


class DatasetQuality(StudioModel):
    """Masalah kualitas data (Req 6.2)."""

    duplicate_rows: int = Field(default=0, ge=0)
    mixed_type_columns: list[str] = Field(default_factory=list)
    null_pct: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, quality: Mapping[str, Any] | None) -> DatasetQuality:
        """Dari ``DatasetProfileRecord.quality``; key yang tidak dikenal diabaikan."""
        q = dict(quality or {})
        return cls(
            duplicate_rows=int(q.get("duplicate_rows") or 0),
            mixed_type_columns=list(q.get("mixed_type_columns") or []),
            null_pct=dict(q.get("null_pct") or {}),
        )


class DatasetDetail(StudioModel):
    """``GET /workspaces/{ws}/datasets/{id}``; profil kosong bila profiling belum selesai."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, serialize_by_alias=True)

    dataset: DatasetOut
    schema_: list[ColumnInfo] = Field(alias="schema")
    column_profiles: list[ColumnProfile]
    quality: DatasetQuality
    column_mapping: list[ColumnMappingOut]

    @classmethod
    def from_records(
        cls, dataset: DatasetRecord, profile: DatasetProfileRecord | None
    ) -> DatasetDetail:
        out = DatasetOut.from_record(dataset)
        return cls(
            dataset=out,
            schema_=list(out.schema_),
            column_profiles=list(profile.columns) if profile is not None else [],
            quality=DatasetQuality.from_dict(profile.quality if profile is not None else None),
            column_mapping=list(out.column_mapping),
        )


class UpdateDatasetRequest(StudioModel):
    """``PATCH /workspaces/{ws}/datasets/{id}``."""

    privacy_no_samples: bool | None = None


class SchemaTypeChange(StudioModel):
    name: str
    old_type: LogicalType
    new_type: LogicalType


class SchemaMismatchDetails(StudioModel):
    """``details`` error ``SCHEMA_MISMATCH`` (``core/schema_diff.SchemaDiff.as_details``)."""

    missing: list[str] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    changed: list[SchemaTypeChange] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Upload & job
# ---------------------------------------------------------------------------


class SheetInfo(StudioModel):
    name: str
    rows_hint: int | None = None


class CsvUploadResponse(StudioModel):
    """``POST /workspaces/{ws}/uploads`` — CSV (202)."""

    upload_id: str
    job_id: str


class XlsxUploadResponse(StudioModel):
    """``POST /workspaces/{ws}/uploads`` — XLSX (200)."""

    upload_id: str
    sheets: list[SheetInfo]


class SelectSheetsRequest(StudioModel):
    """``POST /workspaces/{ws}/uploads/{upload_id}/sheets``."""

    sheets: list[str] = Field(min_length=1)


class SheetJob(StudioModel):
    sheet: str
    job_id: str


class SelectSheetsResponse(StudioModel):
    jobs: list[SheetJob]


class ReuploadResponse(StudioModel):
    """``POST /workspaces/{ws}/datasets/{id}/reupload`` (202)."""

    job_id: str


class JobStatus(_OmitNoneModel):
    """``GET /jobs/{job_id}``; ``dataset_id``/``error`` hanya ada bila terisi."""

    omit_if_none: ClassVar[frozenset[str]] = frozenset({"dataset_id", "error"})

    status: JobState
    progress: float = Field(ge=0.0, le=100.0)
    dataset_id: str | None = None
    error: ErrorBody | None = None

    @classmethod
    def from_job(cls, job: Any) -> JobStatus:
        """Dari ``data.ingestion.Job`` (atau objek dengan atribut yang sama)."""
        return cls(
            status=job.status,
            progress=round(float(job.progress), 1),
            dataset_id=job.dataset_id,
            error=ErrorBody.model_validate(job.error) if job.error is not None else None,
        )


# ---------------------------------------------------------------------------
# Relation, chat session, dashboard summary, workspace detail
# ---------------------------------------------------------------------------


class RelationOut(StudioModel):
    """``Relation`` (Req 7.2) + nama tabel SQL kedua sisi (``from_table``/``to_table``)."""

    id: str
    workspace_id: str
    #: ``"tA.cA|tB.cB"`` kanonik.
    candidate_key: str
    from_dataset_id: str
    from_table: str
    from_column: str
    to_dataset_id: str
    to_table: str
    to_column: str
    cardinality: RelationCardinality
    overlap_pct: float = Field(ge=0.0, le=100.0)
    status: RelationStatus
    created_at: UtcDateTime
    decided_at: UtcDateTime | None

    @classmethod
    def from_record(cls, record: RelationRecord) -> RelationOut:
        return cls(
            id=record.id,
            workspace_id=record.workspace_id,
            candidate_key=record.candidate_key,
            from_dataset_id=record.from_dataset_id,
            from_table=record.from_table,
            from_column=record.from_column,
            to_dataset_id=record.to_dataset_id,
            to_table=record.to_table,
            to_column=record.to_column,
            cardinality=record.cardinality,
            overlap_pct=record.overlap_pct,
            status=record.status,
            created_at=record.created_at,
            decided_at=record.decided_at,
        )


class ChatSessionOut(StudioModel):
    id: str
    workspace_id: str
    title: str
    created_at: UtcDateTime
    last_agent_version: int = Field(ge=0)

    @classmethod
    def from_record(cls, record: ChatSessionRecord) -> ChatSessionOut:
        return cls(
            id=record.id,
            workspace_id=record.workspace_id,
            title=record.title,
            created_at=record.created_at,
            last_agent_version=record.last_agent_version,
        )


class DashboardSummary(StudioModel):
    id: str
    workspace_id: str
    title: str
    version: int = Field(ge=0)
    created_at: UtcDateTime
    updated_at: UtcDateTime

    @classmethod
    def from_record(cls, record: DashboardRecord) -> DashboardSummary:
        return cls(
            id=record.id,
            workspace_id=record.workspace_id,
            title=record.title,
            version=record.version,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class WorkspaceDetail(StudioModel):
    """``GET /workspaces/{ws}`` (Req 1.2)."""

    workspace: WorkspaceOut
    datasets: list[DatasetOut]
    dashboards: list[DashboardSummary]
    chat_sessions: list[ChatSessionOut]

    @classmethod
    def from_records(
        cls,
        workspace: WorkspaceRecord,
        datasets: Iterable[DatasetRecord] = (),
        dashboards: Iterable[DashboardRecord] = (),
        chat_sessions: Iterable[ChatSessionRecord] = (),
    ) -> WorkspaceDetail:
        return cls(
            workspace=WorkspaceOut.from_record(workspace),
            datasets=[DatasetOut.from_record(d) for d in datasets],
            dashboards=[DashboardSummary.from_record(d) for d in dashboards],
            chat_sessions=[ChatSessionOut.from_record(s) for s in chat_sessions],
        )


# ---------------------------------------------------------------------------
# Dashboard: snapshot, patch, undo/redo
# ---------------------------------------------------------------------------


class CreateDashboardRequest(StudioModel):
    """``POST /workspaces/{ws}/dashboards``."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)


class PatchesSinceResponse(_OmitNoneModel):
    """``GET /dashboards/{id}/patches?since_version=n``.

    Normal: ``{patches, version}``; bila riwayat tidak tersedia: ``{snapshot}``.
    """

    omit_if_none: ClassVar[frozenset[str]] = frozenset({"patches", "version", "snapshot"})

    patches: list[PatchEvent] | None = None
    version: int | None = None
    snapshot: DashboardSnapshot | None = None

    @classmethod
    def from_patches(cls, patches: Iterable[PatchEvent], version: int) -> PatchesSinceResponse:
        return cls(patches=list(patches), version=version)

    @classmethod
    def from_snapshot(cls, snapshot: DashboardSnapshot) -> PatchesSinceResponse:
        return cls(snapshot=snapshot)


class ApplyPatchRequest(StudioModel):
    """``POST /dashboards/{id}/patches`` (Req 18.4); ``command`` didiskriminasi ``type``."""

    base_version: int = Field(ge=0)
    command: Command


class UndoRedoRequest(StudioModel):
    """``POST /dashboards/{id}/undo`` / ``redo``."""

    base_version: int = Field(ge=0)


class VersionConflictDetails(StudioModel):
    """``details`` error ``VERSION_CONFLICT`` (409)."""

    model_config = ConfigDict(extra="ignore")

    current_version: int


# ---------------------------------------------------------------------------
# Render & insight refresh
# ---------------------------------------------------------------------------


class RenderRequest(StudioModel):
    """``POST /dashboards/{id}/render``; ``item_ids`` ``None`` = semua item.

    Global_Filter diambil dari konten Dashboard; ``cross_filters`` hanya
    predikat cross-filter sementara dari Canvas_Editor.
    """

    item_ids: list[str] | None = None
    cross_filters: list[Predicate] = Field(default_factory=list)


class RenderedItem(_OmitNoneModel):
    """Hasil render satu item; ``option`` berisi opsi ECharts dengan data terikat."""

    omit_if_none: ClassVar[frozenset[str]] = frozenset({"option", "error", "kpi"})

    option: dict[str, Any] | None = None
    #: KPI_Card: nilai, pembanding, delta, sentimen, string terformat (Req 38.4).
    kpi: dict[str, Any] | None = None
    status: RenderItemStatus
    #: ``True`` bila filter aktif tidak dapat diterapkan ke item (Req 22.4).
    filter_unaffected: bool = False
    error: ErrorBody | None = None


class RenderResponse(StudioModel):
    version: int = Field(ge=0)
    items: dict[str, RenderedItem]


class RefreshInsightRequest(StudioModel):
    """``POST /dashboards/{id}/insights/{item_id}/refresh``."""

    base_version: int = Field(ge=0)


# ---------------------------------------------------------------------------
# Query detail
# ---------------------------------------------------------------------------


class QueryDetail(StudioModel):
    """``GET /queries/{query_id}`` (Req 14.7); ``rows`` = snapshot ≤ 1.000 baris."""

    sql: str
    columns: list[ColumnInfo]
    rows: list[list[Scalar]]
    row_count: int = Field(ge=0)
    executed_at: UtcDateTime
    filters: FilterSet

    @classmethod
    def from_record(cls, record: QueryRecord) -> QueryDetail:
        return cls(
            sql=record.sql,
            columns=list(record.output_schema),
            rows=[list(row) for row in record.rows],
            row_count=record.row_count,
            executed_at=record.executed_at,
            filters=list(record.filters),
        )


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ApprovalRef(StudioModel):
    proposal_id: str
    #: Persetujuan Blueprint per slot (Req 37.6); kosong = semua slot.
    selected_slot_ids: list[str] | None = None


class ChatRequest(StudioModel):
    """``POST /workspaces/{ws}/chat`` (respons ``text/event-stream``)."""

    session_id: str | None = None
    message: str = Field(min_length=1)
    approval: ApprovalRef | None = None


__all__ = [
    "UtcDateTime",
    "JobState",
    "RelationCardinality",
    "RelationStatus",
    "RenderItemStatus",
    "MAX_NAME_LENGTH",
    "ErrorBody",
    "ErrorEnvelope",
    "WorkspaceOut",
    "CreateWorkspaceRequest",
    "RenameWorkspaceRequest",
    "DeleteWorkspaceRequest",
    "ColumnMappingOut",
    "DatasetOut",
    "DatasetQuality",
    "DatasetDetail",
    "UpdateDatasetRequest",
    "SchemaTypeChange",
    "SchemaMismatchDetails",
    "SheetInfo",
    "CsvUploadResponse",
    "XlsxUploadResponse",
    "SelectSheetsRequest",
    "SheetJob",
    "SelectSheetsResponse",
    "ReuploadResponse",
    "JobStatus",
    "RelationOut",
    "ChatSessionOut",
    "DashboardSummary",
    "WorkspaceDetail",
    "CreateDashboardRequest",
    "PatchesSinceResponse",
    "ApplyPatchRequest",
    "UndoRedoRequest",
    "VersionConflictDetails",
    "RenderRequest",
    "RenderedItem",
    "RenderResponse",
    "RefreshInsightRequest",
    "QueryDetail",
    "ApprovalRef",
    "ChatRequest",
    # Re-export model domain yang dipakai langsung sebagai response.
    "COMMAND_ADAPTER",
    "Command",
    "PatchEvent",
    "DashboardSnapshot",
    "ItemStatus",
    "ColumnInfo",
    "ColumnProfile",
    "Predicate",
    "FilterSet",
]
