"""Render item Dashboard: eksekusi SQL tersimpan dengan filter aktif + binding ECharts.

Dipakai ``POST /dashboards/{id}/render`` (Req 12.3, 22.2, 22.3, 23.3, 24.2, 26.2).
Tidak ada pemanggilan LLM di jalur ini: SQL yang dieksekusi adalah SQL
tersimpan (``queries``) apa adanya, filter diterapkan oleh Data_Engine dengan
mendaftarkan LazyFrame terfilter bernama sama.

Aturan per item:

- **Filter efektif** = Global_Filter (dari ``content.global_filters``) +
  Cross_Filter, **kecuali** Cross_Filter yang bersumber dari chart itu sendiri
  (Req 24.2). ``RenderRequest.cross_filters`` tidak membawa id chart sumber;
  chart dianggap sumber sebuah Cross_Filter bila ``spec.cross_filter_column``
  -nya di-resolve (via ``QueryRecord.lineage``) ke ``(table, column)`` yang sama
  dengan predikat tersebut.
- ``filter_unaffected`` = ada filter efektif aktif **dan**
  ``tables_used(query) ∩ affected_tables(G, filter) = ∅`` (Req 23.3). Chart
  yang tidak terpengaruh dieksekusi tanpa filter (hasil identik, dan predikat
  pada tabel lain tidak dapat menggagalkannya).
- Status: ``invalid`` (relasi yang dipakai query tidak lagi confirmed; tidak
  dieksekusi), ``error`` (``StudioError`` saat eksekusi/binding, mis.
  ``UNCONFIRMED_JOIN``, ``QUERY_TIMEOUT``, ``AXIS_STRUCTURE`` pie > 8 kategori),
  ``stale`` (data tetap dirender; hanya Insight_Card yang dapat stale), selain
  itu ``ok``.
- Insight_Card tidak dieksekusi: teks & bukti dirender Frontend dari content;
  hasil render hanya ``{status, filter_unaffected}`` (``option`` dihilangkan).

Eksekusi chart berjalan paralel dengan batas konkurensi (``asyncio.Semaphore``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from studio.api.errors import StudioError
from studio.api.schemas import ErrorBody, RenderedItem, RenderResponse
from studio.core.chart_spec import bind_data
from studio.core.filters import parse_filter_set
from studio.core.kpi import compute_kpi
from studio.core.models import ChartItem, DashboardItem, KpiItem, Predicate
from studio.core.propagation import RelationGraph, affected_tables, is_filter_unaffected
from studio.core.status import ItemStatus, item_query_id, item_status
from studio.data.engine import DataEngine
from studio.data.worker import DEFAULT_TIMEOUT_S
from studio.store.repos import DashboardRecord, QueryRecord, Repositories

__all__ = [
    "DEFAULT_RENDER_CONCURRENCY",
    "cross_filter_source_ref",
    "effective_filters",
    "render_items",
]

log = logging.getLogger(__name__)

#: Jumlah eksekusi query chart yang berjalan bersamaan per permintaan render.
DEFAULT_RENDER_CONCURRENCY = 4


# ---------------------------------------------------------------------------
# Helper murni
# ---------------------------------------------------------------------------


def cross_filter_source_ref(
    item: DashboardItem, query: QueryRecord | None
) -> tuple[str, str] | None:
    """``(table, column)`` asal ``spec.cross_filter_column`` chart (via lineage), bila ada."""
    if not isinstance(item, ChartItem) or query is None:
        return None
    column = item.spec.cross_filter_column
    if column is None:
        return None
    ref = query.lineage.get(column)
    if not isinstance(ref, Mapping):
        return None
    table, col = ref.get("table"), ref.get("column")
    if not table or not col:
        return None
    return (str(table), str(col))


def effective_filters(
    global_filters: Sequence[Predicate],
    cross_filters: Sequence[Predicate],
    source_ref: tuple[str, str] | None,
) -> list[Predicate]:
    """Global_Filter + Cross_Filter selain yang bersumber dari chart ini (Req 24.2)."""
    out: list[Predicate] = list(global_filters)
    for pred in cross_filters:
        if source_ref is not None and (pred.table, pred.column) == source_ref:
            continue
        out.append(pred)
    return out


def _error(exc: StudioError) -> ErrorBody:
    return ErrorBody(code=exc.code, message=exc.message, details=exc.details)


def _invalid_error(query: QueryRecord | None, confirmed: frozenset[str]) -> ErrorBody:
    missing = sorted(r for r in (query.relations_used if query else ()) if r not in confirmed)
    return ErrorBody(
        code="ITEM_INVALID",
        message=(
            "Item ini bergantung pada relasi yang tidak lagi dikonfirmasi; "
            "konfirmasi ulang relasi atau perbarui query item."
        ),
        details={"relation_ids": missing},
    )


def _filter_unaffected(
    graph: RelationGraph, query: QueryRecord | None, filters: Sequence[Predicate]
) -> tuple[bool, tuple[Any, ...]]:
    """``(filter_unaffected, filter_set_ternormalisasi)`` untuk satu item."""
    normalized = parse_filter_set(filters)
    if not normalized or query is None:
        return False, normalized
    affected = affected_tables(graph, normalized)
    return is_filter_unaffected(query.tables_used, affected), normalized


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


async def render_items(
    dashboard: DashboardRecord,
    item_ids: Iterable[str] | None,
    cross_filters: Sequence[Predicate] | None,
    *,
    repos: Repositories,
    engine: DataEngine,
    concurrency: int = DEFAULT_RENDER_CONCURRENCY,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> RenderResponse:
    """Render ``item_ids`` (``None`` = semua item) dari ``dashboard`` tanpa LLM.

    Global_Filter diambil dari ``dashboard.content``; ``cross_filters`` adalah
    state tampilan dari Canvas_Editor (tidak disimpan). Id yang tidak ada di
    Dashboard dilaporkan per item sebagai ``status="error"`` (``NOT_FOUND``).
    FilterSet yang tidak valid menggagalkan seluruh permintaan (``INVALID_FILTER``, 422).
    """
    content = dashboard.content
    ws_id = dashboard.workspace_id
    ids = list(content.items) if item_ids is None else list(dict.fromkeys(item_ids))
    global_filters: list[Predicate] = list(content.global_filters)
    cross: list[Predicate] = list(cross_filters or ())
    parse_filter_set([*global_filters, *cross])  # validasi awal → INVALID_FILTER (422)

    items = {i: content.items[i] for i in ids if i in content.items}
    query_ids = {item_query_id(item) for item in items.values()}
    queries = {
        qid: q
        for qid, q in (await repos.queries.get_many(query_ids)).items()
        if q.workspace_id == ws_id
    }
    confirmed = await repos.relations.confirmed_ids(ws_id)
    versions = await repos.datasets.data_versions(ws_id)
    graph = (await engine.workspace_tables(ws_id)).graph

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def render_one(item_id: str) -> RenderedItem:
        item = items.get(item_id)
        if item is None:
            return RenderedItem(
                status="error",
                error=ErrorBody(
                    code="NOT_FOUND",
                    message=f"Item '{item_id}' tidak ada di Dashboard ini.",
                    details={"entity": "DashboardItem", "id": item_id},
                ),
            )
        query_id = item_query_id(item)
        query = queries.get(query_id)
        filters = effective_filters(
            global_filters, cross, cross_filter_source_ref(item, query)
        )
        unaffected, normalized = _filter_unaffected(graph, query, filters)
        status: ItemStatus = item_status(item, query, confirmed, versions)

        if status.invalid:
            return RenderedItem(
                status="invalid",
                filter_unaffected=unaffected,
                error=_invalid_error(query, confirmed),
            )
        if query is None:
            return RenderedItem(
                status="error",
                filter_unaffected=unaffected,
                error=ErrorBody(
                    code="UNKNOWN_QUERY",
                    message=f"Query '{query_id}' tidak ditemukan di Workspace ini.",
                    details={"query_id": query_id},
                ),
            )
        ok_status = "stale" if status.stale else "ok"
        if not isinstance(item, (ChartItem, KpiItem)):  # Insight_Card: dirender dari content
            return RenderedItem(status=ok_status, filter_unaffected=unaffected)

        run_filters = () if unaffected else normalized
        try:
            async with semaphore:
                result = await engine.run_saved(
                    query, run_filters, timeout_s, workspace_id=ws_id
                )
            if isinstance(item, KpiItem):
                kpi = compute_kpi(item.spec, result)
                return RenderedItem(
                    kpi=kpi.model_dump(mode="json"),
                    status=ok_status,
                    filter_unaffected=unaffected,
                )
            option = bind_data(item.spec, result)
        except StudioError as exc:
            return RenderedItem(
                status="error", filter_unaffected=unaffected, error=_error(exc)
            )
        except Exception:  # noqa: BLE001 — satu chart gagal tidak menggagalkan render lain
            log.exception("Render item %s gagal", item_id)
            return RenderedItem(
                status="error",
                filter_unaffected=unaffected,
                error=ErrorBody(code="INTERNAL_ERROR", message="Gagal merender item."),
            )
        return RenderedItem(option=option, status=ok_status, filter_unaffected=unaffected)

    rendered = await asyncio.gather(*(render_one(i) for i in ids))
    return RenderResponse(version=dashboard.version, items=dict(zip(ids, rendered)))
