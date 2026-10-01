"""Agent_Tools Dashboard: baca state dan ubah Dashboard lewat Dashboard_Store.

Semua tool dibuat oleh :func:`make_dashboard_tools` yang menutup satu
:class:`~studio.agents.tools.context.ToolServices` (wajib ``dashboard_store``).
Workspace dibaca dari session state ``workspace_id``; Dashboard aktif dari
``dashboard_id`` (bila kosong/tidak valid: Dashboard Workspace yang terakhir
diubah; tool mutasi membuat Dashboard baru versi 0 bila Workspace belum punya).

Kontrak tool mutasi (``add_chart``, ``update_chart``, ``remove_chart``,
``update_layout``, ``add_insight``, ``update_insight``, ``undo_last``):

1. **Approval gate lebih dulu** (Req 21.3, 21.4): tanpa permintaan/persetujuan
   eksplisit di giliran ini → ``APPROVAL_REQUIRED`` tanpa menyentuh Dashboard.
2. Jatah retry dicek sebelum eksekusi: ``version_conflict`` (1 + 2, Req 20.4)
   untuk semua tool mutasi, ``chart_spec`` (1 + 3, Req 13.5) untuk
   ``add_chart``/``update_chart`` → ``RETRY_EXHAUSTED`` beserta error terakhir.
3. Wajib ``base_version`` (Req 20.3); perubahan diterapkan hanya melalui
   ``DashboardStore.apply``/``undo`` dengan ``source="agent"`` (Req 8.3, 17.5).
4. ``VERSION_CONFLICT`` → error + ``retries_left`` + ``dashboard`` (state terbaru)
   agar agent dapat merencanakan ulang (Req 20.4).
5. Error validasi chart (Chart_Spec_Validator, kolom tidak dikenal, query
   tidak dikenal, argumen tidak valid) dihitung pada retry ``chart_spec``.
6. Sukses → ``patch_id``, ``dashboard_version`` baru, ``item_id``, ringkasan;
   counter retry terkait direset.

Insight (Req 14.1, 14.2, 14.4–14.6): agent hanya memberi tipe, judul, teks, dan
``query_id``; SQL, bukti (≤ 200 baris snapshot query), filter saat eksekusi,
waktu perhitungan, dan Dataset/versinya diambil backend dari query tersimpan.
Angka pada teks diverifikasi Dashboard_Store (``INSIGHT_NUMBER_MISMATCH``
menyebut angka yang tidak cocok); ``cross_dataset_correlation`` hanya diterima
bila query menggabungkan tabel melalui relasi yang saat ini berstatus confirmed.

``patch.applied`` disiarkan Dashboard_Store ke EventBus Workspace; runner
(18.10) dapat memakai ``patch_id`` hasil tool untuk event stream chat.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    STATE_DASHBOARD_ID,
    STATE_LAST_CHART_ID,
    STATE_LAST_INSIGHT_ID,
    STATE_LAST_QUERY_ID,
    ToolServices,
    chat_event,
    workspace_id_of,
)
from studio.agents.tools.guard import error_result, exhausted_result, llm_output, ok_result
from studio.agents.turn_policy import (
    BLUEPRINT_ACTIVE_KEY,
    BLUEPRINT_CURRENT_SLOT_KEY,
    USER_MESSAGE_KEY,
    StateLike,
    bind_tool_context,
    mutation_gate_error,
    record_success,
)
from studio.core.semantic import verified_query_key
from studio.core.status import item_query_id
from studio.api.errors import StudioError
from studio.core.chart_convert import convert_chart_type
from studio.core.models import (
    MAX_EVIDENCE_ROWS,
    AddChartCommand,
    AddInsightCommand,
    AddItemOp,
    AddKpiCommand,
    DesignBrief,
    KpiSpec,
    NumberFormat,
    SetBriefCommand,
    UpdateKpiCommand,
    ChartItem,
    ChartSpec,
    Command,
    DashboardItem,
    EvidenceTable,
    InsightChanges,
    InsightDraft,
    InsightItem,
    KpiItem,
    LayoutRect,
    PatchEvent,
    RemoveItemCommand,
    SetItemOp,
    SetLayoutCommand,
    UpdateChartCommand,
    UpdateInsightCommand,
)
from studio.core.numbers import InvalidInsightType, check_insight_type
from studio.store.dashboard_store import (
    DashboardSnapshot,
    DashboardStore,
    VersionConflict,
    describe_ops,
)
from studio.store.repos import DashboardRecord, QueryRecord

log = logging.getLogger(__name__)

__all__ = [
    "CHART_SPEC_TOOLS",
    "DASHBOARD_TOOL_NAMES",
    "DEFAULT_DASHBOARD_TITLE",
    "dashboard_state_for_llm",
    "make_dashboard_tools",
]

DASHBOARD_TOOL_NAMES: tuple[str, ...] = (
    "get_dashboard_state",
    "add_chart",
    "update_chart",
    "remove_chart",
    "update_layout",
    "add_insight",
    "update_insight",
    "undo_last",
    "add_kpi",
    "update_kpi",
    "update_brief",
)

#: Tool yang kegagalan validasinya memakan jatah retry ``chart_spec`` (Req 13.5).
CHART_SPEC_TOOLS: frozenset[str] = frozenset({"add_chart", "update_chart", "add_kpi", "update_kpi"})

DEFAULT_DASHBOARD_TITLE = "Dashboard"

#: ``build(record) -> Command`` dijalankan setelah pengecekan versi.
CommandBuilder = Callable[[DashboardRecord], Awaitable[Command]]


# ---------------------------------------------------------------------------
# Helper murni
# ---------------------------------------------------------------------------


def _validation_error(exc: ValidationError, what: str) -> StudioError:
    return StudioError(
        "VALIDATION_ERROR",
        f"{what} tidak valid: {exc.error_count()} kesalahan.",
        {"errors": exc.errors(include_url=False, include_context=False, include_input=False)},
        http_status=422,
    )


def _coerce_base_version(value: Any) -> int:
    """``base_version`` wajib bilangan bulat ≥ 0 (menerima ``3.0``/``"3"`` dari LLM)."""
    number: int | None = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None or number < 0:
        raise StudioError(
            "INVALID_BASE_VERSION",
            "base_version wajib berupa bilangan bulat >= 0 (Dashboard_Version yang diketahui; "
            "lihat get_dashboard_state).",
            {"base_version": repr(value)},
            http_status=422,
        )
    return number


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _layout(value: Mapping[str, Any] | None) -> LayoutRect | None:
    if value is None or (isinstance(value, Mapping) and not value):
        return None
    try:
        return LayoutRect.model_validate(value)
    except ValidationError as exc:
        raise _validation_error(exc, "layout") from exc


async def blueprint_progress(
    repos: Any,
    tool_context: Any,
    slot_id: str,
    status: str,
    *,
    item_id: str | None = None,
    error: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Perbarui status slot Blueprint aktif dan kembalikan event ``blueprint.progress``."""
    bp_id = tool_context.state.get(BLUEPRINT_ACTIVE_KEY)
    if not isinstance(bp_id, str):
        return []
    await repos.blueprints.update_slot(bp_id, slot_id, status, item_id=item_id, error=error)
    if status in ("done", "failed", "skipped"):
        current = tool_context.state.get(BLUEPRINT_CURRENT_SLOT_KEY)
        if isinstance(current, Mapping) and current.get("slot_id") == slot_id:
            tool_context.state[BLUEPRINT_CURRENT_SLOT_KEY] = None
    data: dict[str, Any] = {"blueprint_id": bp_id, "slot_id": slot_id, "status": status}
    if item_id is not None:
        data["item_id"] = item_id
    if error is not None:
        data["error"] = dict(error)
    return [chat_event("blueprint.progress", data)]


def _evidence(query: QueryRecord) -> EvidenceTable:
    """Bukti insight: skema output + ≤ 200 baris snapshot query tersimpan (Req 14.2)."""
    return EvidenceTable(
        columns=list(query.output_schema),
        rows=[list(r) for r in query.rows[:MAX_EVIDENCE_ROWS]],
        row_count=query.row_count,
    )


def _item_summary(
    item: DashboardItem, layout: LayoutRect | None, status: Any | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "layout": layout.model_dump() if layout is not None else None,
        "status": status.model_dump() if status is not None else {"invalid": False, "stale": False},
    }
    if isinstance(item, ChartItem):
        out.update(
            chart_type=item.spec.chart_type,
            query_id=item.spec.query_id,
            cross_filter_column=item.spec.cross_filter_column,
            option=item.spec.option,
        )
    elif isinstance(item, KpiItem):
        out.update(
            query_id=item.spec.query_id,
            value_column=item.spec.value_column,
            comparison_column=item.spec.comparison_column,
            format=item.spec.format.model_dump(),
            good_direction=item.spec.good_direction,
            metric_name=item.spec.metric_name,
        )
    else:
        assert isinstance(item, InsightItem)
        out.update(
            insight_type=item.insight_type,
            text=item.text,
            query_id=item.query_id,
            evidence_columns=[c.name for c in item.evidence.columns],
            evidence_row_count=item.evidence.row_count,
            filters_snapshot=[p.model_dump(mode="json") for p in item.filters_snapshot],
            dataset_ids=list(item.dataset_ids),
            computed_at=item.computed_at.isoformat(),
        )
    return out


def dashboard_state_for_llm(snapshot: DashboardSnapshot) -> dict[str, Any]:
    """Ringkasan state Dashboard untuk LLM (tanpa baris bukti insight).

    Item diurutkan menurut posisi grid (``y``, lalu ``x``).
    """
    content = snapshot.content

    def order(item_id: str) -> tuple[int, int, str]:
        rect = content.layout.get(item_id)
        return (rect.y, rect.x, item_id) if rect is not None else (1 << 30, 0, item_id)

    items = [
        _item_summary(content.items[i], content.layout.get(i), snapshot.item_status.get(i))
        for i in sorted(content.items, key=order)
    ]
    return {
        "dashboard_id": snapshot.id,
        "title": content.title,
        "version": snapshot.version,
        "can_undo": snapshot.can_undo,
        "can_redo": snapshot.can_redo,
        "global_filters": [p.model_dump(mode="json") for p in content.global_filters],
        "brief": content.brief.model_dump(mode="json") if content.brief else None,
        "items": items,
    }


def _touched_item(event: PatchEvent) -> DashboardItem | None:
    """Item yang ditambah/diubah patch (untuk ``item_id`` hasil tool)."""
    for op in event.ops:
        if isinstance(op, AddItemOp):
            return op.item
        if isinstance(op, SetItemOp):
            return op.after
    return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_dashboard_tools(services: ToolServices) -> dict[str, Callable[..., Any]]:
    """Buat tool Dashboard yang terikat ke ``services``; kunci dict = nama tool."""
    repos = services.repos
    guard = llm_output(max_sample_rows=services.sample_rows)

    # -- helper -------------------------------------------------------------

    def store_of() -> DashboardStore:
        store = services.dashboard_store
        if store is None:
            raise StudioError(
                "DASHBOARD_STORE_UNAVAILABLE",
                "Dashboard_Store tidak tersedia untuk Agent_Tools.",
                http_status=500,
            )
        return store

    async def resolve_dashboard(tool_context: ToolContext, *, create: bool) -> str | None:
        """``dashboard_id`` aktif Workspace (disimpan ke state); buat baru bila ``create``."""
        ws_id = workspace_id_of(tool_context)
        state = tool_context.state
        bound = state.get(STATE_DASHBOARD_ID)
        if isinstance(bound, str) and bound:
            record = await repos.dashboards.get_or_none(bound)
            if record is not None and record.workspace_id == ws_id:
                return bound
        dashboards = await repos.dashboards.list_by_workspace(ws_id)
        if dashboards:
            chosen = max(dashboards, key=lambda d: (d.updated_at, d.created_at, d.id)).id
        elif create:
            chosen = (await store_of().create_dashboard(ws_id, DEFAULT_DASHBOARD_TITLE)).id
        else:
            return None
        state[STATE_DASHBOARD_ID] = chosen
        return chosen

    async def state_of(dashboard_id: str) -> dict[str, Any]:
        return dashboard_state_for_llm(await store_of().get(dashboard_id))

    async def load_query(ws_id: str, tool_context: ToolContext, query_id: str | None) -> QueryRecord:
        qid = (query_id or "").strip() or tool_context.state.get(STATE_LAST_QUERY_ID)
        if not qid:
            raise StudioError(
                "NO_QUERY",
                "Belum ada query. Jalankan run_sql terlebih dahulu atau berikan query_id.",
                http_status=422,
            )
        record = await repos.queries.get_or_none(str(qid))
        if record is None or record.workspace_id != ws_id:
            raise StudioError(
                "UNKNOWN_QUERY",
                f"Query '{qid}' tidak ditemukan di Workspace ini; jalankan query terlebih dahulu "
                "dan gunakan query_id hasilnya.",
                {"query_id": str(qid)},
                http_status=422,
            )
        return record

    async def ensure_confirmed_relations(ws_id: str, insight_type: str, query: QueryRecord) -> None:
        """``cross_dataset_correlation`` hanya lewat relasi yang saat ini confirmed (Req 14.6)."""
        check_insight_type(insight_type, query.tables_used, query.relations_used)
        if insight_type != "cross_dataset_correlation":
            return
        confirmed = await repos.relations.confirmed_ids(ws_id)
        missing = sorted(set(query.relations_used) - set(confirmed))
        if missing:
            raise InvalidInsightType(
                "Insight 'cross_dataset_correlation' membutuhkan query yang menggabungkan tabel "
                "hanya melalui Confirmed_Relation; relasi berikut tidak lagi berstatus confirmed.",
                {
                    "insight_type": insight_type,
                    "query_id": query.id,
                    "relations_not_confirmed": missing,
                },
            )

    async def insight_source_fields(ws_id: str, query: QueryRecord) -> dict[str, Any]:
        """Field Insight_Card yang berasal dari query tersimpan (Req 14.2, 14.5)."""
        versions = await repos.datasets.data_versions(ws_id)
        dataset_ids = list(query.dataset_ids)
        return {
            "query_id": query.id,
            "sql": query.sql,
            "evidence": _evidence(query),
            "filters_snapshot": list(query.filters),
            "dataset_ids": dataset_ids,
            "computed_at": query.executed_at,
            "dataset_versions": {d: versions[d] for d in dataset_ids if d in versions},
        }

    def remember(state: StateLike, event: PatchEvent) -> str | None:
        item = _touched_item(event)
        if item is None:
            return None
        key = STATE_LAST_CHART_ID if isinstance(item, (ChartItem, KpiItem)) else STATE_LAST_INSIGHT_ID
        state[key] = item.id
        return item.id

    async def mutate(
        tool_context: ToolContext,
        tool_name: str,
        base_version: Any,
        build: CommandBuilder | None,
    ) -> dict[str, Any]:
        """Jalur bersama tool mutasi (gate → retry → versi → apply/undo)."""
        state = bind_tool_context(tool_context)
        if (gate := mutation_gate_error(state, tool_name)) is not None:
            return gate
        chart = tool_name in CHART_SPEC_TOOLS
        kinds = ("version_conflict", "chart_spec") if chart else ("version_conflict",)
        for kind in kinds:
            if (exhausted := exhausted_result(state, kind)) is not None:
                return exhausted

        store = store_of()
        base = _coerce_base_version(base_version)
        dashboard_id = await resolve_dashboard(tool_context, create=True)
        assert dashboard_id is not None
        actor = getattr(tool_context, "invocation_id", None)
        record = await repos.dashboards.get(dashboard_id)

        try:
            if base != record.version:
                raise VersionConflict(record.version, base)
            if build is None:
                event = await store.undo(
                    dashboard_id, base, source="agent", actor_run_id=actor
                )
            else:
                try:
                    command = await build(record)
                except ValidationError as exc:
                    raise _validation_error(exc, "Argumen tool") from exc
                event = await store.apply(
                    dashboard_id, command, base, "agent", actor_run_id=actor
                )
        except VersionConflict as exc:
            result = error_result(exc, state=state, kind="version_conflict")
            result["dashboard"] = await state_of(dashboard_id)
            return result
        except StudioError as exc:
            if chart:
                return error_result(exc, state=state, kind="chart_spec")
            return error_result(exc)

        record_success(state, "version_conflict")
        if chart:
            record_success(state, "chart_spec")
        item_id = remember(state, event)
        return ok_result(
            dashboard_id=dashboard_id,
            patch_id=event.id,
            kind=event.kind,
            base_version=event.base_version,
            dashboard_version=event.version,
            item_id=item_id,
            summary=describe_ops(event.ops, record.content),
            # Event chat `patch.applied` dititipkan agar runner menyiramkannya ke
            # stream chat (FE Canvas mengaplikasikan patch dari sini, Req 18.3).
            **{
                CHAT_EVENTS_KEY: [
                    chat_event("patch.applied", event.model_dump(mode="json"))
                ]
            },
        )

    # -- Blueprint & Verified_Query --------------------------------------------

    def current_slot(tool_context: ToolContext) -> dict[str, Any] | None:
        slot = tool_context.state.get(BLUEPRINT_CURRENT_SLOT_KEY)
        return slot if isinstance(slot, Mapping) else None  # type: ignore[return-value]

    def slot_layout(tool_context: ToolContext, slot_id: str | None) -> LayoutRect | None:
        """Layout Blueprint_Slot aktif bila ``slot_id`` cocok (Req 37.7)."""
        slot = current_slot(tool_context)
        if not slot_id or slot is None or slot.get("slot_id") != slot_id:
            return None
        raw = slot.get("layout")
        return LayoutRect.model_validate(raw) if isinstance(raw, Mapping) else None

    def build_with(build: CommandBuilder, layout: LayoutRect | None) -> CommandBuilder:
        """Sisipkan ``layout`` ke command ``add_*`` hasil ``build``."""

        async def wrapped(record: DashboardRecord) -> Command:
            command = await build(record)
            if layout is not None and hasattr(command, "layout"):
                return command.model_copy(update={"layout": layout})
            return command

        return wrapped

    async def after_add(
        tool_context: ToolContext,
        result: dict[str, Any],
        slot_id: str | None,
        *,
        verified: bool = True,
    ) -> dict[str, Any]:
        """Setelah ``add_*`` sukses: Verified_Query candidate (Req 34.1) + tandai slot selesai."""
        if not result.get("ok") or not result.get("item_id"):
            return result
        state = tool_context.state
        slot = current_slot(tool_context)
        in_slot = bool(slot_id) and slot is not None and slot.get("slot_id") == slot_id
        ws_id = workspace_id_of(tool_context)
        if verified:
            try:
                record = await repos.dashboards.get(result["dashboard_id"])
                item = record.content.items.get(result["item_id"])
                query = await repos.queries.get_or_none(item_query_id(item)) if item else None
                if query is not None:
                    key = verified_query_key(query.sql)
                    if await repos.semantic.get_by_key(ws_id, key) is None:
                        question = (slot or {}).get("purpose") if in_slot else None
                        question = question or state.get(USER_MESSAGE_KEY) or (item.title if item else "")
                        await repos.semantic.upsert(
                            ws_id,
                            kind="verified_query",
                            entry_key=key,
                            body={
                                "question": str(question),
                                "sql": query.sql,
                                "query_id": query.id,
                                "item_id": result["item_id"],
                            },
                        )
            except Exception:  # noqa: BLE001 — Verified_Query opsional, jangan gagalkan tool
                log.exception("Gagal membuat Verified_Query candidate")
        if in_slot:
            events = await blueprint_progress(
                repos, tool_context, str(slot_id), "done", item_id=result["item_id"]
            )
            result.setdefault(CHAT_EVENTS_KEY, []).extend(events)
            result["slot_done"] = slot_id
        return result

    # -- baca ---------------------------------------------------------------

    @guard
    async def get_dashboard_state(tool_context: ToolContext) -> dict[str, Any]:
        """State Dashboard aktif terbaru beserta Dashboard_Version.

        Berisi judul, ``version`` (pakai sebagai ``base_version`` tool mutasi),
        filter global, dan daftar item (id, jenis, judul, posisi grid, status
        invalid/stale; chart: tipe, query_id, opsi ECharts; insight: tipe, teks,
        query_id, kolom bukti). Panggil ulang setelah VERSION_CONFLICT.
        """
        dashboard_id = await resolve_dashboard(tool_context, create=False)
        if dashboard_id is None:
            return ok_result(
                dashboard=None,
                message="Workspace belum memiliki Dashboard; tool mutasi dengan base_version=0 "
                "akan membuatnya.",
            )
        return ok_result(dashboard=await state_of(dashboard_id))

    # -- chart --------------------------------------------------------------

    @guard
    async def add_chart(
        base_version: int,
        title: str,
        chart_type: str,
        option: dict[str, Any],
        tool_context: ToolContext,
        query_id: str | None = None,
        layout: dict[str, int] | None = None,
        cross_filter_column: str | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:
        """Tambahkan chart ECharts ke Dashboard.

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            title: judul chart.
            chart_type: line, bar, pie, scatter, atau heatmap.
            option: opsi ECharts TANPA data inline; series memakai ``encode`` dengan
                nama kolom hasil query (mis. {"x": "bulan", "y": "revenue"}); sertakan
                title, label sumbu, dan legend sesuai tipe chart.
            query_id: id dari run_sql; kosong = query terakhir sesi ini.
            layout: opsional {x, y, w, h} pada grid 12 kolom; kosong = baris kosong pertama.
            cross_filter_column: opsional kolom output untuk Cross_Filter.
            slot_id: id Blueprint_Slot yang sedang dibangun; layout slot dipakai otomatis.

        Gagal validasi → error + retries_left; perbaiki opsi lalu panggil lagi.
        """
        ws_id = workspace_id_of(tool_context)

        async def build(record: DashboardRecord) -> Command:
            query = await load_query(ws_id, tool_context, query_id)
            spec = ChartSpec(
                query_id=query.id,
                chart_type=chart_type,  # type: ignore[arg-type]
                option=option,
                cross_filter_column=_optional_text(cross_filter_column),
            )
            return AddChartCommand(title=str(title), spec=spec)

        layout_rect = _layout(layout) or slot_layout(tool_context, slot_id)
        result = await mutate(tool_context, "add_chart", base_version, build_with(build, layout_rect))
        return await after_add(tool_context, result, slot_id)

    @guard
    async def add_kpi(
        base_version: int,
        title: str,
        value_column: str,
        tool_context: ToolContext,
        query_id: str | None = None,
        comparison_column: str | None = None,
        comparison_label: str | None = None,
        format_style: str = "number",
        decimals: int = 0,
        good_direction: str = "up",
        metric_name: str | None = None,
        layout: dict[str, int] | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:
        """Tambahkan KPI_Card (angka besar + pembanding) ke Dashboard.

        Query sumber (run_sql) WAJIB menghasilkan tepat satu baris berisi kolom nilai
        dan, sebaiknya, kolom pembanding (periode sebelumnya atau target).

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            title: label KPI, mis. "Revenue".
            value_column: kolom numerik nilai pada hasil query.
            query_id: id dari run_sql; kosong = query terakhir sesi ini.
            comparison_column: kolom numerik pembanding (opsional, dianjurkan).
            comparison_label: mis. "vs bulan lalu".
            format_style: number, currency (Rupiah), atau percent (nilai rasio 0–1).
            decimals: jumlah desimal tampilan (0–4).
            good_direction: up (naik = baik), down (turun = baik), atau neutral.
            metric_name: nama Business_Metric terkait (opsional).
            layout: opsional {x, y, w, h}; kosong = layout slot Blueprint atau baris kosong pertama.
            slot_id: id Blueprint_Slot yang sedang dibangun (opsional).
        """
        ws_id = workspace_id_of(tool_context)

        async def build(record: DashboardRecord) -> Command:
            query = await load_query(ws_id, tool_context, query_id)
            spec = KpiSpec(
                query_id=query.id,
                value_column=str(value_column),
                comparison_column=_optional_text(comparison_column),
                comparison_label=_optional_text(comparison_label),
                format=NumberFormat(style=format_style, decimals=int(decimals)),  # type: ignore[arg-type]
                good_direction=good_direction,  # type: ignore[arg-type]
                metric_name=_optional_text(metric_name),
            )
            return AddKpiCommand(title=str(title), spec=spec)

        layout_rect = _layout(layout) or slot_layout(tool_context, slot_id)
        result = await mutate(tool_context, "add_kpi", base_version, build_with(build, layout_rect))
        return await after_add(tool_context, result, slot_id)

    @guard
    async def update_kpi(
        base_version: int,
        kpi_id: str,
        tool_context: ToolContext,
        title: str | None = None,
        query_id: str | None = None,
        value_column: str | None = None,
        comparison_column: str | None = None,
        comparison_label: str | None = None,
        format_style: str | None = None,
        decimals: int | None = None,
        good_direction: str | None = None,
    ) -> dict[str, Any]:
        """Ubah KPI_Card yang sudah ada.

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            kpi_id: id item KPI.
            title: label baru.
            query_id: query sumber baru (tepat satu baris).
            value_column: kolom nilai baru.
            comparison_column: kolom pembanding baru; string kosong = hapus.
            comparison_label: label pembanding baru.
            format_style: number, currency, atau percent.
            decimals: jumlah desimal (0–4).
            good_direction: up, down, atau neutral.
        """
        ws_id = workspace_id_of(tool_context)

        async def build(record: DashboardRecord) -> Command:
            current = record.content.items.get(kpi_id)
            if not isinstance(current, KpiItem):
                raise StudioError(
                    "NOT_FOUND" if current is None else "NOT_KPI",
                    f"Item '{kpi_id}' "
                    + ("tidak ditemukan di Dashboard." if current is None else "bukan KPI_Card."),
                    {"item_id": kpi_id},
                    http_status=404 if current is None else 422,
                )
            data = current.spec.model_dump()
            if query_id is not None and query_id.strip():
                data["query_id"] = (await load_query(ws_id, tool_context, query_id)).id
            if value_column:
                data["value_column"] = value_column
            if comparison_column is not None:
                data["comparison_column"] = _optional_text(comparison_column)
            if comparison_label is not None:
                data["comparison_label"] = _optional_text(comparison_label)
            if format_style is not None:
                data["format"]["style"] = format_style
            if decimals is not None:
                data["format"]["decimals"] = int(decimals)
            if good_direction is not None:
                data["good_direction"] = good_direction
            return UpdateKpiCommand(id=kpi_id, spec=KpiSpec.model_validate(data), title=_optional_text(title))

        return await mutate(tool_context, "update_kpi", base_version, build)

    @guard
    async def update_chart(
        base_version: int,
        chart_id: str,
        tool_context: ToolContext,
        option: dict[str, Any] | None = None,
        chart_type: str | None = None,
        title: str | None = None,
        query_id: str | None = None,
        cross_filter_column: str | None = None,
    ) -> dict[str, Any]:
        """Ubah chart yang sudah ada (tipe, opsi ECharts, judul, atau query sumber).

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            chart_id: id item chart.
            option: opsi ECharts pengganti penuh (tanpa data inline). Bila hanya
                chart_type yang diberikan, opsi dikonversi otomatis ke tipe baru.
            chart_type: tipe baru (line, bar, pie, scatter, heatmap).
            title: judul item baru.
            query_id: query sumber baru (dari run_sql).
            cross_filter_column: kolom Cross_Filter baru; string kosong = hapus.
        """
        ws_id = workspace_id_of(tool_context)
        new_title = _optional_text(title)

        async def build(record: DashboardRecord) -> Command:
            current = record.content.items.get(chart_id)
            if not isinstance(current, ChartItem):
                chart_ids = sorted(
                    i for i, it in record.content.items.items() if isinstance(it, ChartItem)
                )
                raise StudioError(
                    "NOT_FOUND" if current is None else "NOT_CHART",
                    f"Item '{chart_id}' "
                    + ("tidak ditemukan di Dashboard." if current is None else "bukan chart."),
                    {"item_id": chart_id, "chart_ids": chart_ids},
                    http_status=404 if current is None else 422,
                )
            if all(v is None for v in (option, chart_type, new_title, query_id, cross_filter_column)):
                raise StudioError(
                    "NO_CHANGES",
                    "Tidak ada perubahan: berikan option, chart_type, title, query_id, atau "
                    "cross_filter_column.",
                    {"item_id": chart_id},
                    http_status=422,
                )
            spec = current.spec
            updates: dict[str, Any] = {}
            if query_id is not None and query_id.strip():
                updates["query_id"] = (await load_query(ws_id, tool_context, query_id)).id
            if cross_filter_column is not None:
                updates["cross_filter_column"] = _optional_text(cross_filter_column)
            if option is not None:
                updates["option"] = option
                if chart_type is not None:
                    updates["chart_type"] = chart_type
            spec = ChartSpec.model_validate({**spec.model_dump(), **updates})
            if option is None and chart_type is not None and chart_type != spec.chart_type:
                # Konversi otomatis (Req 17.3 / design convert_chart_type).
                query = await load_query(ws_id, tool_context, spec.query_id)
                new_type = ChartSpec.model_validate(
                    {**spec.model_dump(), "chart_type": chart_type}
                ).chart_type
                spec = convert_chart_type(spec, new_type, query.output_schema)
            return UpdateChartCommand(id=chart_id, spec=spec, title=new_title)

        return await mutate(tool_context, "update_chart", base_version, build)

    @guard
    async def remove_chart(
        base_version: int, item_id: str, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Hapus satu item (chart atau Insight_Card) dari Dashboard.

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            item_id: id item yang dihapus.
        """

        async def build(record: DashboardRecord) -> Command:
            return RemoveItemCommand(id=item_id)

        return await mutate(tool_context, "remove_chart", base_version, build)

    @guard
    async def update_layout(
        base_version: int, changes: dict[str, dict[str, int]], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Pindahkan/ubah ukuran item pada grid 12 kolom.

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            changes: pemetaan id item → {x, y, w, h} (x + w <= 12, w dan h >= 1).
        """

        async def build(record: DashboardRecord) -> Command:
            if not isinstance(changes, Mapping) or not changes:
                raise StudioError(
                    "NO_CHANGES", "changes harus objek {item_id: {x, y, w, h}} yang tidak kosong.",
                    http_status=422,
                )
            return SetLayoutCommand.model_validate({"changes": dict(changes)})

        return await mutate(tool_context, "update_layout", base_version, build)

    # -- insight ------------------------------------------------------------

    @guard
    async def add_insight(
        base_version: int,
        insight_type: str,
        title: str,
        text: str,
        tool_context: ToolContext,
        query_id: str | None = None,
        layout: dict[str, int] | None = None,
        slot_id: str | None = None,
    ) -> dict[str, Any]:
        """Tambahkan Insight_Card berbasis bukti dari query tersimpan.

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            insight_type: trend, anomaly, comparison, top_bottom_contributors, atau
                cross_dataset_correlation (hanya untuk query yang JOIN melalui
                Confirmed_Relation).
            title: judul insight.
            text: teks insight; setiap angka harus berasal dari hasil query tertaut
                (format dengan format_number).
            query_id: id dari run_sql; kosong = query terakhir sesi ini.
            layout: opsional {x, y, w, h}; kosong = layout slot Blueprint atau baris kosong pertama.
            slot_id: id Blueprint_Slot yang sedang dibangun (opsional).

        SQL, tabel bukti, filter aktif, waktu perhitungan, dan Dataset diambil
        otomatis dari query. INSIGHT_NUMBER_MISMATCH menyebut angka yang tidak cocok.
        """
        ws_id = workspace_id_of(tool_context)

        async def build(record: DashboardRecord) -> Command:
            query = await load_query(ws_id, tool_context, query_id)
            await ensure_confirmed_relations(ws_id, insight_type, query)
            draft = InsightDraft(
                insight_type=insight_type,  # type: ignore[arg-type]
                title=str(title),
                text=str(text),
                **await insight_source_fields(ws_id, query),
            )
            return AddInsightCommand(insight=draft)

        layout_rect = _layout(layout) or slot_layout(tool_context, slot_id)
        result = await mutate(tool_context, "add_insight", base_version, build_with(build, layout_rect))
        return await after_add(tool_context, result, slot_id, verified=False)

    @guard
    async def update_insight(
        base_version: int,
        insight_id: str,
        tool_context: ToolContext,
        text: str | None = None,
        title: str | None = None,
        insight_type: str | None = None,
        query_id: str | None = None,
    ) -> dict[str, Any]:
        """Ubah Insight_Card (teks, judul, tipe, atau query bukti).

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            insight_id: id Insight_Card.
            text: teks baru; angka diverifikasi terhadap bukti (baru) insight.
            title: judul baru.
            insight_type: tipe baru.
            query_id: query bukti baru dari run_sql; SQL, bukti, filter, waktu, dan
                Dataset ikut diperbarui dari query tersebut.
        """
        ws_id = workspace_id_of(tool_context)

        async def build(record: DashboardRecord) -> Command:
            current = record.content.items.get(insight_id)
            if not isinstance(current, InsightItem):
                raise StudioError(
                    "NOT_FOUND" if current is None else "NOT_INSIGHT",
                    f"Item '{insight_id}' "
                    + ("tidak ditemukan di Dashboard." if current is None else "bukan Insight_Card."),
                    {"item_id": insight_id},
                    http_status=404 if current is None else 422,
                )
            fields: dict[str, Any] = {}
            if (t := _optional_text(text)) is not None:
                fields["text"] = t
            if (t := _optional_text(title)) is not None:
                fields["title"] = t
            if (t := _optional_text(insight_type)) is not None:
                fields["insight_type"] = t
            new_query = None
            if query_id is not None and query_id.strip():
                new_query = await load_query(ws_id, tool_context, query_id)
                fields.update(await insight_source_fields(ws_id, new_query))
            if not fields:
                raise StudioError(
                    "NO_CHANGES",
                    "Tidak ada perubahan: berikan text, title, insight_type, atau query_id.",
                    {"item_id": insight_id},
                    http_status=422,
                )
            effective_type = fields.get("insight_type", current.insight_type)
            if effective_type == "cross_dataset_correlation":
                query = new_query or await load_query(ws_id, tool_context, current.query_id)
                await ensure_confirmed_relations(ws_id, effective_type, query)
            return UpdateInsightCommand(id=insight_id, changes=InsightChanges(**fields))

        return await mutate(tool_context, "update_insight", base_version, build)

    # -- Design_Brief ---------------------------------------------------------

    @guard
    async def update_brief(
        base_version: int, brief: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Simpan/ubah Design_Brief Dashboard (berversi, dapat di-undo; Req 36.2, 36.4).

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
            brief: {purpose, audience, key_questions[], kpis[{metric, compare}],
                sections[], time_grain, assumptions[]}; field yang tidak diberikan
                dipertahankan dari brief saat ini.
        """

        async def build(record: DashboardRecord) -> Command:
            if not isinstance(brief, Mapping):
                raise StudioError("INVALID_ARGUMENT", "brief harus berupa objek.", http_status=422)
            current = record.content.brief.model_dump() if record.content.brief else {}
            return SetBriefCommand(brief=DesignBrief.model_validate({**current, **dict(brief)}))

        return await mutate(tool_context, "update_brief", base_version, build)

    # -- undo ---------------------------------------------------------------

    @guard
    async def undo_last(base_version: int, tool_context: ToolContext) -> dict[str, Any]:
        """Batalkan perubahan Dashboard terakhir (dari agent maupun pengguna).

        Args:
            base_version: Dashboard_Version yang diketahui (dari get_dashboard_state).
        """
        return await mutate(tool_context, "undo_last", base_version, None)

    tools = {
        "get_dashboard_state": get_dashboard_state,
        "add_chart": add_chart,
        "update_chart": update_chart,
        "remove_chart": remove_chart,
        "update_layout": update_layout,
        "add_insight": add_insight,
        "update_insight": update_insight,
        "undo_last": undo_last,
        "add_kpi": add_kpi,
        "update_kpi": update_kpi,
        "update_brief": update_brief,
    }
    assert tuple(tools) == DASHBOARD_TOOL_NAMES
    return tools
