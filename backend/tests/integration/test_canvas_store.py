"""Dashboard_Store dengan patch canvas OpenUI Lang (irisan 2, D3/D10/D11).

Memakai SQLite sementara dan sidecar bun asli; dilewati bila bun tidak ada.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from studio.core.models import (
    AddChartCommand,
    AddKpiCommand,
    ChartSpec,
    DashboardContent,
    KpiSpec,
    LayoutRect,
    NumberFormat,
)
from studio.core.openui.sidecar import OpenUISidecar
from studio.core.openui.validate import CanvasPatchError
from studio.core.patches import replay
from studio.events.bus import EventBus
from studio.store.dashboard_store import DashboardStore
from studio.store.db import Database
from studio.store.repos import Repositories

pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="bun tidak terpasang")

SCHEMA = [
    {"name": "month", "type": "string"},
    {"name": "region", "type": "string"},
    {"name": "revenue", "type": "float"},
    {"name": "revenue_prev", "type": "float"},
]
ROWS = [["2026-01", "Jawa", 120.0, 100.0], ["2026-02", "Bali", 98.0, 90.0]]

BASE = "\n".join(
    [
        "root = VContainer([kpis, row])",
        "kpis = HContainer([kpi_rev])",
        'q_kpi = Query("render_kpi", {query: "q_sales", value: "revenue", compare: "revenue_prev"}, {})',
        'kpi_rev = Kpi("Pendapatan", q_kpi)',
        'q_trend = Query("render_query", {query: "q_sales"}, {rows: []})',
        'slot_trend = LineChart("Tren", q_trend, "month", ["revenue"], {crossFilter: "month"})',
        "row = HContainer([slot_trend])",
    ]
)


@pytest.fixture(scope="module")
def sidecar() -> Iterator[OpenUISidecar]:
    sc = OpenUISidecar(timeout_s=30)
    yield sc
    sc.close()


class Harness:
    def __init__(self, sidecar: OpenUISidecar) -> None:
        self.loop = asyncio.new_event_loop()
        self.tmp = tempfile.mkdtemp(prefix="studio_canvas_")
        self.db = self.run(Database(Path(self.tmp) / "studio.db").open())
        self.repos = Repositories(self.db)
        self.store = DashboardStore(self.repos, EventBus(), openui=sidecar)
        ws = self.run(self.repos.workspaces.create("Studio"))
        self.ws_id = ws.id
        self.run(
            self.repos.queries.create(
                ws.id, sql="SELECT 1", output_schema=SCHEMA, rows=ROWS, query_id="q_sales"
            )
        )
        self.dash = self.run(self.store.create_dashboard(ws.id, "Penjualan")).id

    def run(self, coro: Any) -> Any:
        return self.loop.run_until_complete(coro)

    def snap(self):
        return self.run(self.store.get(self.dash))

    def patch(self, text: str, **kw: Any):
        snap = self.snap()
        return self.run(self.store.apply_canvas(self.dash, text, snap.version, kw.pop("source", "agent"), **kw))

    def close(self) -> None:
        self.run(self.db.close())
        self.loop.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


@pytest.fixture()
def h(sidecar: OpenUISidecar) -> Iterator[Harness]:
    harness = Harness(sidecar)
    yield harness
    harness.close()


def test_apply_undo_redo_and_replay(h: Harness) -> None:
    initial = h.snap().content
    event, warnings = h.patch(BASE)
    assert warnings == []
    snap = h.snap()
    assert set(snap.content.statements) == {"root", "kpis", "q_kpi", "kpi_rev", "q_trend", "slot_trend", "row"}
    assert snap.content.statements["slot_trend"].props["x"] == "month"
    assert snap.item_status["slot_trend"].invalid is False

    # Patch kedua: hapus KPI (root ditulis ulang) → kpis, kpi_rev, q_kpi terbuang.
    h.patch("root = VContainer([row])")
    assert set(h.snap().content.statements) == {"root", "q_trend", "slot_trend", "row"}

    h.run(h.store.undo(h.dash, base_version=h.snap().version))
    assert "kpi_rev" in h.snap().content.statements
    h.run(h.store.redo(h.dash, base_version=h.snap().version))
    assert "kpi_rev" not in h.snap().content.statements

    events = h.run(h.store.history(h.dash))
    assert replay(initial, events) == h.snap().content


def _codes(exc: pytest.ExceptionInfo[CanvasPatchError]) -> set[str]:
    return {e["code"] for e in exc.value.details["errors"]}


def test_rejects_unknown_column_query_and_inline_data(h: Harness) -> None:
    with pytest.raises(CanvasPatchError) as exc:
        h.patch(
            "\n".join(
                [
                    "root = VContainer([a, b, c])",
                    'q1 = Query("render_query", {query: "q_sales"}, {rows: []})',
                    'a = BarChart("A", q1, "kota", ["revenue"])',
                    'q2 = Query("render_query", {query: "q_tidak_ada"}, {rows: []})',
                    'b = PieChart("B", q2, "region", "revenue")',
                    'c = PieChart("C", [1, 2], "region", "revenue")',
                ]
            )
        )
    assert {"UNKNOWN_COLUMN", "UNKNOWN_QUERY", "INLINE_DATA"} <= _codes(exc)
    assert h.snap().version == 0  # tidak ada yang tersimpan


def test_allowlist_and_unknown_component(h: Harness) -> None:
    with pytest.raises(CanvasPatchError) as exc:
        h.patch(BASE, allowed_components=["Query", "LineChart", "Kpi"])
    assert "FORBIDDEN_COMPONENT" in _codes(exc)
    with pytest.raises(CanvasPatchError) as exc:
        h.patch('root = VContainer([x])\nx = Gauge("A")')
    assert "UNKNOWN_COMPONENT" in _codes(exc)


def test_insight_attachment_and_number_check(h: Harness) -> None:
    h.patch(BASE)
    h.patch(
        'ins = Insight("trend", "Naik", "Pendapatan Januari 120.", "q_sales")\n'
        "root = VContainer([kpis, row, ins])"
    )
    stmt = h.snap().content.statements["ins"]
    assert stmt.insight is not None and stmt.insight.sql == "SELECT 1"
    assert [m.value for m in stmt.insight.matched_numbers] == [120.0]
    from studio.core.numbers import InsightNumberMismatch

    with pytest.raises(InsightNumberMismatch):
        h.patch('ins = Insight("trend", "Naik", "Pendapatan 999.", "q_sales")')


def test_convert_legacy_dashboard(h: Harness) -> None:
    snap = h.snap()
    chart = ChartSpec(
        query_id="q_sales",
        chart_type="bar",
        option={
            "xAxis": {"type": "category"},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
        },
        cross_filter_column="region",
    )
    kpi = KpiSpec(
        query_id="q_sales",
        value_column="revenue",
        comparison_column="revenue_prev",
        format=NumberFormat(style="number", decimals=0),
    )
    h.run(h.store.apply(h.dash, AddKpiCommand(title="Omzet", spec=kpi, layout=LayoutRect(x=0, y=0, w=3, h=2)), snap.version, "agent"))
    h.run(h.store.apply(h.dash, AddChartCommand(title="Per wilayah", spec=chart, layout=LayoutRect(x=0, y=2, w=6, h=6)), snap.version + 1, "agent"))

    event = h.run(h.store.convert_to_canvas(h.dash))
    assert event is not None
    after = h.snap()
    content: DashboardContent = after.content
    assert content.items == {} and content.is_canvas
    assert after.can_undo is False  # baseline baru
    components = {s.component for s in content.statements.values()}
    assert {"VContainer", "Kpi", "BarChart", "Query"} <= components
    bar = next(s for s in content.statements.values() if s.component == "BarChart")
    assert bar.props["x"] == "region" and bar.props["options"] == {"crossFilter": "region"}
    # Program hasil konversi lolos validator saat ditulis ulang apa adanya.
    assert h.run(h.store.convert_to_canvas(h.dash)) is None
