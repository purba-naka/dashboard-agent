"""Model domain Pydantic bersama (murni; tanpa FastAPI/ADK).

Bentuk JSON model di sini adalah kontrak lintas-bahasa dengan
``frontend/src/lib/types.ts``: nama field snake_case, ``Op`` didiskriminasi
oleh field ``op``, ``Command`` oleh field ``type``, ``Predicate`` oleh ``kind``,
dan item Dashboard oleh ``kind``.

Serialisasi JSON memakai ``model.model_dump(mode="json")`` /
``model.model_dump_json()``; untuk koleksi union gunakan adapter
(``OP_LIST_ADAPTER``, ``COMMAND_ADAPTER``, ``FILTER_SET_ADAPTER``,
``DASHBOARD_ITEM_ADAPTER``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

# ---------------------------------------------------------------------------
# Primitif
# ---------------------------------------------------------------------------

#: Nilai skalar JSON. ``bool`` ditaruh sebelum ``int`` agar mode smart-union
#: Pydantic mempertahankan ``True``/``False`` sebagai boolean.
Scalar = Union[bool, int, float, str, None]

#: Sel hasil query. Selain skalar JSON, menerima ``date``/``datetime`` dari
#: Data_Engine; keduanya diserialisasi ke string ISO-8601 pada JSON.
Cell = Union[bool, int, float, str, datetime, date, None]

LogicalType = Literal["integer", "float", "string", "boolean", "date", "datetime"]
ColumnRole = Literal["dimension", "measure", "time", "identifier"]
ChartType = Literal["line", "bar", "pie", "scatter", "heatmap"]
InsightType = Literal[
    "trend",
    "anomaly",
    "comparison",
    "top_bottom_contributors",
    "cross_dataset_correlation",
]
PatchSource = Literal["agent", "user"]
PatchKind = Literal["normal", "undo", "redo"]

#: Jumlah kolom grid Canvas_Editor (react-grid-layout).
GRID_COLUMNS = 12
#: Batas baris bukti yang disimpan pada Insight_Card.
MAX_EVIDENCE_ROWS = 200
#: Batas pasangan top value pada Column_Profile.
MAX_TOP_VALUES = 5


class StudioModel(BaseModel):
    """Basis semua model domain: field tak dikenal ditolak."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Skema & profil kolom
# ---------------------------------------------------------------------------


class ColumnInfo(StudioModel):
    name: str
    type: LogicalType


class ColumnProfile(StudioModel):
    name: str
    type: LogicalType
    role: ColumnRole
    null_count: int = Field(ge=0)
    null_pct: float = Field(ge=0.0, le=100.0)
    distinct_count: int = Field(ge=0)
    min: Scalar = None
    max: Scalar = None
    mean: float | None = None
    #: Maks 5 pasangan ``[nilai, jumlah]``.
    top_values: list[tuple[Scalar, int]] = Field(
        default_factory=list, max_length=MAX_TOP_VALUES
    )


# ---------------------------------------------------------------------------
# Filter (model serialisasi; ``core/filters.py`` mengonversi ke bentuk frozen)
# ---------------------------------------------------------------------------


class _PredicateBase(StudioModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str
    column: str


class DateRangePredicate(_PredicateBase):
    """Rentang tanggal inklusif; salah satu ujung boleh ``None``."""

    kind: Literal["date_range"] = "date_range"
    start: date | None = None
    end: date | None = None


class InPredicate(_PredicateBase):
    """Keanggotaan himpunan; ``values`` diserialisasi sebagai array JSON."""

    kind: Literal["in"] = "in"
    values: tuple[Scalar, ...] = ()


Predicate = Annotated[Union[DateRangePredicate, InPredicate], Field(discriminator="kind")]

#: Himpunan predikat (konjungsi); normalisasi dilakukan di ``core/filters.py``.
FilterSet = list[Predicate]


# ---------------------------------------------------------------------------
# Hasil query & bukti
# ---------------------------------------------------------------------------


class QueryResult(StudioModel):
    query_id: str
    columns: list[ColumnInfo]
    rows: list[list[Cell]]
    row_count: int = Field(ge=0)
    truncated_for_storage: bool = False


class EvidenceTable(StudioModel):
    columns: list[ColumnInfo]
    rows: list[list[Cell]] = Field(max_length=MAX_EVIDENCE_ROWS)
    row_count: int = Field(ge=0)


class NumberMatch(StudioModel):
    token: str
    value: float
    row: int | None = None
    column: str | None = None


# ---------------------------------------------------------------------------
# Item Dashboard
# ---------------------------------------------------------------------------


class ChartSpec(StudioModel):
    spec_version: Literal[1] = 1
    query_id: str
    chart_type: ChartType
    #: Opsi ECharts tanpa data inline; divalidasi ``validate_chart_spec``.
    option: dict[str, Any]
    #: Kolom output query; di-resolve via lineage.
    cross_filter_column: str | None = None


class ChartItem(StudioModel):
    id: str
    kind: Literal["chart"] = "chart"
    title: str
    spec: ChartSpec


class InsightDraft(StudioModel):
    """Field Insight_Card tanpa ``id``/``kind`` (payload command ``add_insight``)."""

    insight_type: InsightType
    title: str
    text: str
    query_id: str
    sql: str
    evidence: EvidenceTable
    matched_numbers: list[NumberMatch] = Field(default_factory=list)
    #: Filter aktif saat insight dihitung (Req 14.5).
    filters_snapshot: FilterSet = Field(default_factory=list)
    dataset_ids: list[str]
    computed_at: datetime
    #: ``dataset_id -> data_version`` untuk status stale (Req 26.2).
    dataset_versions: dict[str, int] = Field(default_factory=dict)


class InsightItem(InsightDraft):
    id: str
    kind: Literal["insight"] = "insight"


class InsightChanges(StudioModel):
    """Perubahan parsial Insight_Card (command ``update_insight``)."""

    insight_type: InsightType | None = None
    title: str | None = None
    text: str | None = None
    query_id: str | None = None
    sql: str | None = None
    evidence: EvidenceTable | None = None
    matched_numbers: list[NumberMatch] | None = None
    filters_snapshot: FilterSet | None = None
    dataset_ids: list[str] | None = None
    computed_at: datetime | None = None
    dataset_versions: dict[str, int] | None = None


NumberStyle = Literal["number", "currency", "percent"]
GoodDirection = Literal["up", "down", "neutral"]


class NumberFormat(StudioModel):
    """Format angka tampilan (KPI_Card, Semantic_Column, Business_Metric)."""

    style: NumberStyle = "number"
    #: Kode mata uang ISO bila ``style == "currency"``.
    currency: str | None = "IDR"
    decimals: int = Field(default=0, ge=0, le=4)
    #: Skala ringkas id-ID (rb/jt/M/T) dipilih otomatis.
    compact: bool = True


class KpiSpec(StudioModel):
    """Spesifikasi KPI_Card (Req 38.1); query wajib menghasilkan tepat satu baris."""

    spec_version: Literal[1] = 1
    query_id: str
    value_column: str
    comparison_column: str | None = None
    comparison_label: str | None = None
    format: NumberFormat = Field(default_factory=NumberFormat)
    good_direction: GoodDirection = "up"
    #: Business_Metric terkait (dipakai review & konteks).
    metric_name: str | None = None


class KpiItem(StudioModel):
    id: str
    kind: Literal["kpi"] = "kpi"
    title: str
    spec: KpiSpec


DashboardItem = Annotated[
    Union[ChartItem, InsightItem, KpiItem], Field(discriminator="kind")
]


# ---------------------------------------------------------------------------
# Design_Brief & Dashboard_Blueprint (Req 36, 37)
# ---------------------------------------------------------------------------

SectionRole = Literal[
    "kpi_row", "trend", "breakdown", "composition", "distribution", "detail", "other"
]
VisualType = Literal[
    "kpi", "line", "bar", "pie", "scatter", "heatmap", "waterfall", "insight"
]
TimeGrain = Literal["day", "week", "month", "quarter", "year"]


class BriefKpi(StudioModel):
    #: Nama Business_Metric atau kolom.
    metric: str
    compare: Literal["previous_period", "target", "none"] = "previous_period"


class DesignBrief(StudioModel):
    """Maksud desain Dashboard (Req 36.1)."""

    purpose: str = ""
    audience: str = ""
    key_questions: list[str] = Field(default_factory=list, max_length=10)
    kpis: list[BriefKpi] = Field(default_factory=list, max_length=8)
    sections: list[SectionRole] = Field(default_factory=list)
    time_grain: TimeGrain | None = None
    assumptions: list[str] = Field(default_factory=list, max_length=10)


class LayoutRect(StudioModel):
    """Posisi item pada grid 12 kolom; ``w, h >= 1`` dan ``x + w <= 12``."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=GRID_COLUMNS)
    h: int = Field(ge=1)

    @model_validator(mode="after")
    def _fits_grid(self) -> LayoutRect:
        if self.x + self.w > GRID_COLUMNS:
            raise ValueError(
                f"x + w harus <= {GRID_COLUMNS} (x={self.x}, w={self.w})"
            )
        return self


class DashboardContent(StudioModel):
    """Konten Dashboard. Invariant: ``keys(layout) == keys(items)``."""

    title: str
    items: dict[str, DashboardItem] = Field(default_factory=dict)
    layout: dict[str, LayoutRect] = Field(default_factory=dict)
    global_filters: FilterSet = Field(default_factory=list)
    #: Design_Brief opsional (Req 36.1); konten lama tanpa brief tetap valid.
    brief: DesignBrief | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> DashboardContent:
        item_keys, layout_keys = set(self.items), set(self.layout)
        if item_keys != layout_keys:
            raise ValueError(
                "keys(layout) harus sama dengan keys(items): "
                f"tanpa layout={sorted(item_keys - layout_keys)}, "
                f"layout tanpa item={sorted(layout_keys - item_keys)}"
            )
        for key, item in self.items.items():
            if item.id != key:
                raise ValueError(f"key item '{key}' tidak sama dengan item.id '{item.id}'")
        return self


class BlueprintSlot(StudioModel):
    """Satu rencana elemen Dashboard pada Dashboard_Blueprint (Req 37.1)."""

    slot_id: str = Field(pattern=r"^[a-z0-9_]{1,32}$")
    section: SectionRole
    purpose: str
    visual: VisualType
    metrics: list[str] = Field(min_length=1, max_length=4)
    dimension: str | None = None
    layout: LayoutRect | None = None
    cross_filter_column: str | None = None


class DashboardBlueprint(StudioModel):
    brief: DesignBrief = Field(default_factory=DesignBrief)
    #: Batas 16 slot diperiksa ``validate_blueprint`` (kode ``TOO_MANY_SLOTS``).
    slots: list[BlueprintSlot] = Field(min_length=1)
    default_filters: FilterSet = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Ops (hasil resolve command, self-contained dengan snapshot ``before``)
# ---------------------------------------------------------------------------


class AddItemOp(StudioModel):
    op: Literal["add_item"] = "add_item"
    item: DashboardItem
    layout: LayoutRect


class RemoveItemOp(StudioModel):
    op: Literal["remove_item"] = "remove_item"
    item: DashboardItem
    layout: LayoutRect


class SetItemOp(StudioModel):
    op: Literal["set_item"] = "set_item"
    id: str
    before: DashboardItem
    after: DashboardItem

    @model_validator(mode="after")
    def _same_id(self) -> SetItemOp:
        if self.before.id != self.id or self.after.id != self.id:
            raise ValueError("set_item: before.id dan after.id harus sama dengan id")
        return self


class LayoutChange(StudioModel):
    id: str
    before: LayoutRect
    after: LayoutRect


class SetLayoutOp(StudioModel):
    op: Literal["set_layout"] = "set_layout"
    changes: list[LayoutChange]

    @model_validator(mode="after")
    def _unique_ids(self) -> SetLayoutOp:
        ids = [c.id for c in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError("set_layout: id pada changes harus unik")
        return self


class SetFiltersOp(StudioModel):
    op: Literal["set_filters"] = "set_filters"
    before: FilterSet
    after: FilterSet


class SetTitleOp(StudioModel):
    op: Literal["set_title"] = "set_title"
    before: str
    after: str


class SetBriefOp(StudioModel):
    op: Literal["set_brief"] = "set_brief"
    before: DesignBrief | None = None
    after: DesignBrief | None = None


Op = Annotated[
    Union[
        AddItemOp,
        RemoveItemOp,
        SetItemOp,
        SetLayoutOp,
        SetFiltersOp,
        SetTitleOp,
        SetBriefOp,
    ],
    Field(discriminator="op"),
]


# ---------------------------------------------------------------------------
# Command (masukan Agent_Tools / Canvas_Editor; di-resolve di core/patches.py)
# ---------------------------------------------------------------------------


class AddChartCommand(StudioModel):
    type: Literal["add_chart"] = "add_chart"
    title: str
    spec: ChartSpec
    layout: LayoutRect | None = None


class AddInsightCommand(StudioModel):
    type: Literal["add_insight"] = "add_insight"
    insight: InsightDraft
    layout: LayoutRect | None = None


class UpdateChartCommand(StudioModel):
    type: Literal["update_chart"] = "update_chart"
    id: str
    spec: ChartSpec
    title: str | None = None


class ChangeChartTypeCommand(StudioModel):
    type: Literal["change_chart_type"] = "change_chart_type"
    id: str
    chart_type: ChartType


class UpdateInsightCommand(StudioModel):
    type: Literal["update_insight"] = "update_insight"
    id: str
    changes: InsightChanges


class RemoveItemCommand(StudioModel):
    type: Literal["remove_item"] = "remove_item"
    id: str


class SetLayoutCommand(StudioModel):
    type: Literal["set_layout"] = "set_layout"
    changes: dict[str, LayoutRect]


class SetGlobalFiltersCommand(StudioModel):
    type: Literal["set_global_filters"] = "set_global_filters"
    filters: FilterSet


class SetTitleCommand(StudioModel):
    type: Literal["set_title"] = "set_title"
    title: str


class AddKpiCommand(StudioModel):
    type: Literal["add_kpi"] = "add_kpi"
    title: str
    spec: KpiSpec
    layout: LayoutRect | None = None


class UpdateKpiCommand(StudioModel):
    type: Literal["update_kpi"] = "update_kpi"
    id: str
    spec: KpiSpec
    title: str | None = None


class SetBriefCommand(StudioModel):
    type: Literal["set_brief"] = "set_brief"
    brief: DesignBrief | None = None


Command = Annotated[
    Union[
        AddChartCommand,
        AddInsightCommand,
        UpdateChartCommand,
        ChangeChartTypeCommand,
        UpdateInsightCommand,
        RemoveItemCommand,
        SetLayoutCommand,
        SetGlobalFiltersCommand,
        SetTitleCommand,
        AddKpiCommand,
        UpdateKpiCommand,
        SetBriefCommand,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Patch_Event
# ---------------------------------------------------------------------------


class PatchEvent(StudioModel):
    id: str
    dashboard_id: str
    #: Versi SETELAH patch; selalu ``base_version + 1``.
    version: int = Field(ge=1)
    base_version: int = Field(ge=0)
    source: PatchSource
    kind: PatchKind = "normal"
    #: Patch yang dibatalkan/diulang (hanya untuk ``undo``/``redo``).
    target_patch_id: str | None = None
    ops: list[Op]
    inverse_ops: list[Op]
    created_at: datetime

    @model_validator(mode="after")
    def _check_invariants(self) -> PatchEvent:
        if self.version != self.base_version + 1:
            raise ValueError("version harus sama dengan base_version + 1")
        if self.kind == "normal" and self.target_patch_id is not None:
            raise ValueError("patch normal tidak boleh memiliki target_patch_id")
        if self.kind != "normal" and self.target_patch_id is None:
            raise ValueError(f"patch {self.kind} wajib memiliki target_patch_id")
        return self


# ---------------------------------------------------------------------------
# Semantic_Model (Req 31)
# ---------------------------------------------------------------------------

SemanticKind = Literal["column", "metric", "term", "instruction", "verified_query"]
SemanticStatus = Literal["candidate", "confirmed", "rejected"]
SemanticSource = Literal["auto", "user"]
Aggregation = Literal["sum", "avg", "count", "count_distinct", "min", "max", "none"]


class SemanticColumn(StudioModel):
    table: str
    column: str
    label: str = ""
    description: str = ""
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    default_aggregation: Aggregation = "none"
    format: NumberFormat = Field(default_factory=NumberFormat)
    is_enum: bool = False
    #: Nilai enum (dari top values profil); dihapus untuk ``privacy_no_samples``.
    enum_values: list[Scalar] = Field(default_factory=list, max_length=50)


class BusinessMetric(StudioModel):
    name: str = Field(min_length=1, max_length=64)
    label: str = ""
    description: str = ""
    #: Ekspresi agregat SQL, mis. ``SUM(amt_net)``.
    expr: str = Field(min_length=1)
    base_table: str
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    format: NumberFormat = Field(default_factory=NumberFormat)
    good_direction: GoodDirection = "up"
    time_column: str | None = None


class GlossaryTerm(StudioModel):
    term: str = Field(min_length=1)
    description: str
    synonyms: list[str] = Field(default_factory=list, max_length=20)


class WorkspaceInstruction(StudioModel):
    text: str = Field(min_length=1, max_length=500)


class VerifiedQuery(StudioModel):
    question: str
    sql: str
    query_id: str | None = None
    item_id: str | None = None


SemanticBody = Union[
    SemanticColumn, BusinessMetric, GlossaryTerm, WorkspaceInstruction, VerifiedQuery
]


class SemanticEntry(StudioModel):
    """Satu Semantic_Entry tersimpan (baris ``semantic_entries``)."""

    id: str
    kind: SemanticKind
    entry_key: str
    status: SemanticStatus
    source: SemanticSource
    dataset_id: str | None = None
    body: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    decided_at: datetime | None = None


class SemanticModel(StudioModel):
    domain: str | None = None
    domain_confidence: float | None = None
    assumptions: list[str] = Field(default_factory=list)
    columns: list[SemanticColumn] = Field(default_factory=list)
    metrics: list[BusinessMetric] = Field(default_factory=list)
    terms: list[GlossaryTerm] = Field(default_factory=list)
    instructions: list[WorkspaceInstruction] = Field(default_factory=list)
    verified_queries: list[VerifiedQuery] = Field(default_factory=list)


SEMANTIC_BODY_MODELS: dict[str, type[StudioModel]] = {
    "column": SemanticColumn,
    "metric": BusinessMetric,
    "term": GlossaryTerm,
    "instruction": WorkspaceInstruction,
    "verified_query": VerifiedQuery,
}


# ---------------------------------------------------------------------------
# Adapter untuk tipe union / koleksi
# ---------------------------------------------------------------------------

OP_LIST_ADAPTER: TypeAdapter[list[Op]] = TypeAdapter(list[Op])
COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)
FILTER_SET_ADAPTER: TypeAdapter[FilterSet] = TypeAdapter(FilterSet)
DASHBOARD_ITEM_ADAPTER: TypeAdapter[DashboardItem] = TypeAdapter(DashboardItem)

__all__ = [
    "Scalar",
    "Cell",
    "LogicalType",
    "ColumnRole",
    "ChartType",
    "InsightType",
    "PatchSource",
    "PatchKind",
    "GRID_COLUMNS",
    "MAX_EVIDENCE_ROWS",
    "MAX_TOP_VALUES",
    "StudioModel",
    "ColumnInfo",
    "ColumnProfile",
    "DateRangePredicate",
    "InPredicate",
    "Predicate",
    "FilterSet",
    "QueryResult",
    "EvidenceTable",
    "NumberMatch",
    "ChartSpec",
    "ChartItem",
    "InsightDraft",
    "InsightItem",
    "InsightChanges",
    "DashboardItem",
    "NumberStyle",
    "GoodDirection",
    "NumberFormat",
    "KpiSpec",
    "KpiItem",
    "SectionRole",
    "VisualType",
    "TimeGrain",
    "BriefKpi",
    "DesignBrief",
    "BlueprintSlot",
    "DashboardBlueprint",
    "SetBriefOp",
    "AddKpiCommand",
    "UpdateKpiCommand",
    "SetBriefCommand",
    "SemanticKind",
    "SemanticStatus",
    "SemanticSource",
    "Aggregation",
    "SemanticColumn",
    "BusinessMetric",
    "GlossaryTerm",
    "WorkspaceInstruction",
    "VerifiedQuery",
    "SemanticBody",
    "SemanticEntry",
    "SemanticModel",
    "SEMANTIC_BODY_MODELS",
    "LayoutRect",
    "DashboardContent",
    "AddItemOp",
    "RemoveItemOp",
    "SetItemOp",
    "LayoutChange",
    "SetLayoutOp",
    "SetFiltersOp",
    "SetTitleOp",
    "Op",
    "AddChartCommand",
    "AddInsightCommand",
    "UpdateChartCommand",
    "ChangeChartTypeCommand",
    "UpdateInsightCommand",
    "RemoveItemCommand",
    "SetLayoutCommand",
    "SetGlobalFiltersCommand",
    "SetTitleCommand",
    "Command",
    "PatchEvent",
    "OP_LIST_ADAPTER",
    "COMMAND_ADAPTER",
    "FILTER_SET_ADAPTER",
    "DASHBOARD_ITEM_ADAPTER",
]
