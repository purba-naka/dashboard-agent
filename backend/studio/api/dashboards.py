"""Router REST Dashboard (Req 17.2–17.5, 18.5, 19.1, 19.2, 15.1, 22.3, 24.2).

Endpoint (prefix ``/api``):

* ``POST /workspaces/{ws}/dashboards`` ``{title}`` → ``201 DashboardSnapshot``
* ``GET  /workspaces/{ws}/dashboards`` → ``DashboardSummary[]``
* ``GET  /dashboards/{id}`` → ``DashboardSnapshot``
* ``GET  /dashboards/{id}/patches?since_version=n`` → ``{patches, version}``; bila
  riwayat tidak dapat menyambung dari ``n`` (mis. klien lebih maju dari server
  atau ada celah versi) → ``{snapshot}``
* ``POST /dashboards/{id}/patches`` ``{base_version, command}`` → ``PatchEvent``
  (``source="user"``, jalur yang sama dengan Agent_Tools, Req 17.5)
* ``POST /dashboards/{id}/undo`` / ``redo`` ``{base_version}`` → ``PatchEvent``
  (``400 NOTHING_TO_UNDO/REDO``)
* ``POST /dashboards/{id}/render`` → ``RenderResponse`` (``data/render.py``; tanpa LLM)
* ``POST /dashboards/{id}/insights/{item_id}/refresh`` ``{base_version}`` → ``PatchEvent``

Pemetaan error: ``VERSION_CONFLICT`` → ``409`` dengan ``details.current_version``
(``DashboardStore``), validasi request/command/Chart_Spec/insight → ``422``.

Refresh insight (Req 15.1, 15.2): SQL sumber dieksekusi ulang dengan Global_Filter
yang sedang aktif (Cross_Filter adalah state tampilan dan tidak ikut), lalu
``evidence`` (≤ 200 baris), ``filters_snapshot``, ``computed_at``, dan
``dataset_versions`` diperbarui lewat ``DashboardStore.apply`` (satu Patch_Event).
Bila hasil numerik berubah dan ``app.state.insight_rewriter`` terpasang
(``rewriter(item, new_evidence) -> str | None``, sync/async; dihubungkan ke
Insight_Agent di task 18.11), teks baru dari rewriter ikut disimpan. Tanpa
rewriter, teks lama dipertahankan; bila angka pada teks lama tidak lagi cocok
dengan bukti baru, Dashboard_Store menolak (``INSIGHT_NUMBER_MISMATCH``) dan
endpoint mengembalikan ``409 INSIGHT_TEXT_OUTDATED`` (details: ``item_id``,
``query_id``, ``unmatched``) tanpa mengubah Dashboard, agar Frontend meminta
agent menyusun ulang teks.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from fastapi import APIRouter, Depends, Query, Request

from studio.api.errors import StudioError
from studio.api.schemas import (
    ApplyPatchRequest,
    CreateDashboardRequest,
    DashboardSnapshot,
    DashboardSummary,
    PatchesSinceResponse,
    PatchEvent,
    RefreshInsightRequest,
    RenderRequest,
    RenderResponse,
    UndoRedoRequest,
)
from studio.core.models import (
    MAX_EVIDENCE_ROWS,
    EvidenceTable,
    InsightChanges,
    InsightItem,
    PatchSource,
    UpdateInsightCommand,
)
from studio.core.privacy import truncate_result
from studio.data.engine import DataEngine
from studio.data.render import render_items
from studio.data.worker import DEFAULT_TIMEOUT_S
from studio.store.dashboard_store import DashboardStore, VersionConflict
from studio.store.repos import Repositories

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboards"])

#: ``rewriter(item, new_evidence)`` → teks insight baru (``None`` = pertahankan teks lama).
InsightRewriter = Callable[[InsightItem, EvidenceTable], Awaitable[str | None] | str | None]


# ---------------------------------------------------------------------------
# Dependencies (dari ``request.app.state``)
# ---------------------------------------------------------------------------


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


def _store(request: Request) -> DashboardStore:
    return request.app.state.dashboard_store


def _engine(request: Request) -> DataEngine:
    return request.app.state.engine


def _timeout_s(request: Request) -> float:
    settings = getattr(request.app.state, "settings", None)
    value = getattr(settings, "query_timeout_s", None)
    return float(value) if value else DEFAULT_TIMEOUT_S


def _rewriter(request: Request) -> InsightRewriter | None:
    return getattr(request.app.state, "insight_rewriter", None)


# ---------------------------------------------------------------------------
# Dashboard CRUD & patch
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{ws}/dashboards", response_model=DashboardSnapshot, status_code=201
)
async def create_dashboard(
    ws: str,
    body: CreateDashboardRequest,
    store: DashboardStore = Depends(_store),
) -> DashboardSnapshot:
    """Buat Dashboard kosong versi 0; ``404`` bila Workspace tidak ada."""
    return await store.create_dashboard(ws, body.title)


@router.get("/workspaces/{ws}/dashboards", response_model=list[DashboardSummary])
async def list_dashboards(
    ws: str, repos: Repositories = Depends(_repos)
) -> list[DashboardSummary]:
    await repos.workspaces.get(ws)
    records = await repos.dashboards.list_by_workspace(ws)
    return [DashboardSummary.from_record(r) for r in records]


@router.get("/dashboards/{dashboard_id}", response_model=DashboardSnapshot)
async def get_dashboard(
    dashboard_id: str, store: DashboardStore = Depends(_store)
) -> DashboardSnapshot:
    return await store.get(dashboard_id)


@router.get("/dashboards/{dashboard_id}/patches", response_model=PatchesSinceResponse)
async def patches_since(
    dashboard_id: str,
    since_version: int = Query(default=0, ge=0),
    store: DashboardStore = Depends(_store),
    repos: Repositories = Depends(_repos),
) -> PatchesSinceResponse:
    """Patch_Event ``version > since_version`` untuk resync SSE (Req 16.5)."""
    patches = await store.patches_since(dashboard_id, since_version)
    current = await repos.dashboards.get_version(dashboard_id)
    expected = list(range(since_version + 1, current + 1))
    if since_version > current or [p.version for p in patches] != expected:
        # Riwayat tidak dapat menyambung dari versi klien → kirim snapshot penuh.
        return PatchesSinceResponse.from_snapshot(await store.get(dashboard_id))
    return PatchesSinceResponse.from_patches(patches, current)


@router.post("/dashboards/{dashboard_id}/patches", response_model=PatchEvent)
async def apply_patch(
    dashboard_id: str,
    body: ApplyPatchRequest,
    store: DashboardStore = Depends(_store),
) -> PatchEvent:
    """User_Edit_Event → Patch_Event lewat API yang sama dengan Agent_Tools (Req 17.5)."""
    return await store.apply(dashboard_id, body.command, body.base_version, "user")


@router.post("/dashboards/{dashboard_id}/undo", response_model=PatchEvent)
async def undo(
    dashboard_id: str,
    body: UndoRedoRequest,
    store: DashboardStore = Depends(_store),
) -> PatchEvent:
    return await store.undo(dashboard_id, body.base_version, "user")


@router.post("/dashboards/{dashboard_id}/redo", response_model=PatchEvent)
async def redo(
    dashboard_id: str,
    body: UndoRedoRequest,
    store: DashboardStore = Depends(_store),
) -> PatchEvent:
    return await store.redo(dashboard_id, body.base_version, "user")


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


@router.post("/dashboards/{dashboard_id}/render", response_model=RenderResponse)
async def render_dashboard(
    request: Request,
    dashboard_id: str,
    body: RenderRequest,
    repos: Repositories = Depends(_repos),
    engine: DataEngine = Depends(_engine),
) -> RenderResponse:
    """Render ulang item dengan Global_Filter + Cross_Filter tanpa LLM (Req 22.3)."""
    record = await repos.dashboards.get(dashboard_id)
    return await render_items(
        record,
        body.item_ids,
        body.cross_filters,
        repos=repos,
        engine=engine,
        timeout_s=_timeout_s(request),
    )


# ---------------------------------------------------------------------------
# Refresh insight
# ---------------------------------------------------------------------------


async def refresh_insight(
    dashboard_id: str,
    item_id: str,
    base_version: int,
    *,
    repos: Repositories,
    store: DashboardStore,
    engine: DataEngine,
    rewriter: InsightRewriter | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    source: PatchSource = "user",
) -> PatchEvent:
    """Eksekusi ulang SQL insight dengan Global_Filter aktif lalu simpan via ``apply``."""
    record = await repos.dashboards.get(dashboard_id)
    if base_version != record.version:
        raise VersionConflict(record.version, base_version)
    item = record.content.items.get(item_id)
    if item is None:
        raise StudioError(
            "NOT_FOUND",
            f"Item '{item_id}' tidak ada di Dashboard ini.",
            {"entity": "DashboardItem", "id": item_id},
            http_status=404,
        )
    if not isinstance(item, InsightItem):
        raise StudioError(
            "NOT_INSIGHT",
            f"Item '{item_id}' bukan Insight_Card.",
            {"id": item_id, "kind": item.kind},
            http_status=422,
        )

    ws_id = record.workspace_id
    query = await repos.queries.get(item.query_id, workspace_id=ws_id)
    filters = list(record.content.global_filters)
    result = await engine.run_saved(query, filters, timeout_s, workspace_id=ws_id)

    truncated = truncate_result(result, MAX_EVIDENCE_ROWS)
    evidence = EvidenceTable(
        columns=list(result.columns), rows=truncated["rows"], row_count=result.row_count
    )
    changed = evidence.model_dump(mode="json") != item.evidence.model_dump(mode="json")

    dataset_ids = list(item.dataset_ids or query.dataset_ids)
    versions = await repos.datasets.data_versions(ws_id)
    changes = InsightChanges(
        evidence=evidence,
        filters_snapshot=filters,
        computed_at=datetime.now(UTC),
        dataset_versions={d: versions[d] for d in dataset_ids if d in versions},
    )
    if changed and rewriter is not None:
        text = rewriter(item, evidence)
        if inspect.isawaitable(text):
            text = await text
        if text:
            changes.text = text

    command = UpdateInsightCommand(id=item_id, changes=changes)
    try:
        return await store.apply(dashboard_id, command, base_version, source)
    except StudioError as exc:
        if exc.code != "INSIGHT_NUMBER_MISMATCH":
            raise
        raise StudioError(
            "INSIGHT_TEXT_OUTDATED",
            "Hasil numerik insight berubah dan teksnya tidak lagi cocok dengan bukti baru; "
            "minta agent menyusun ulang teks Insight_Card.",
            {
                "item_id": item_id,
                "query_id": item.query_id,
                "unmatched": exc.details.get("unmatched", []),
            },
            http_status=409,
        ) from exc


@router.post(
    "/dashboards/{dashboard_id}/insights/{item_id}/refresh", response_model=PatchEvent
)
async def refresh_insight_endpoint(
    request: Request,
    dashboard_id: str,
    item_id: str,
    body: RefreshInsightRequest,
    repos: Repositories = Depends(_repos),
    store: DashboardStore = Depends(_store),
    engine: DataEngine = Depends(_engine),
) -> PatchEvent:
    """Req 15.1: hasil numerik baru dengan filter aktif (Req 15.2 via ``insight_rewriter``)."""
    return await refresh_insight(
        dashboard_id,
        item_id,
        body.base_version,
        repos=repos,
        store=store,
        engine=engine,
        rewriter=_rewriter(request),
        timeout_s=_timeout_s(request),
    )


__all__ = ["router", "InsightRewriter", "refresh_insight"]
