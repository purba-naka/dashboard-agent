"""Generator fixture patch lintas bahasa (Req 30.3).

Membangkitkan ``frontend/src/lib/__fixtures__/patches.json`` dari logika
``studio/core/patches.py`` + ``studio/core/history.py`` sehingga reducer
frontend (``src/lib/dashboard-state.ts``) dapat diuji terhadap hasil backend.

Setiap skenario berbentuk::

    {"name", "description", "initial": DashboardContent,
     "patches": [PatchEvent], "expected": DashboardContent}

dengan ``replay(initial, patches) == expected``. Semua JSON diserialisasi via
``model_dump(mode="json")``; id patch/item dan timestamp deterministik sehingga
file stabil antar-run.

Pemakaian (dari folder ``backend``)::

    .venv/Scripts/python.exe -m tests.fixtures.gen_patch_fixtures          # tulis ulang
    .venv/Scripts/python.exe -m tests.fixtures.gen_patch_fixtures --check  # exit 1 bila usang
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:  # dukung `python tests/fixtures/gen_patch_fixtures.py`
    sys.path.insert(0, str(_BACKEND_DIR))

from studio.core.chart_convert import convert_chart_type  # noqa: E402
from studio.core.chart_spec import validate_chart_spec  # noqa: E402
from studio.core.history import History, redo_patch_fields, undo_patch_fields  # noqa: E402
from studio.core.models import (  # noqa: E402
    AddChartCommand,
    AddInsightCommand,
    AddKpiCommand,
    BriefKpi,
    DesignBrief,
    KpiSpec,
    NumberFormat,
    SetBriefCommand,
    UpdateKpiCommand,
    ChangeChartTypeCommand,
    ChartSpec,
    ChartType,
    ColumnInfo,
    Command,
    DashboardContent,
    DateRangePredicate,
    EvidenceTable,
    InPredicate,
    InsightChanges,
    InsightDraft,
    LayoutRect,
    NumberMatch,
    PatchEvent,
    PatchSource,
    RemoveItemCommand,
    SetGlobalFiltersCommand,
    SetLayoutCommand,
    SetTitleCommand,
    UpdateChartCommand,
    UpdateInsightCommand,
)
from studio.core.patches import apply_ops, invert_ops, replay, resolve_command  # noqa: E402

REPO_ROOT = _BACKEND_DIR.parent
FIXTURE_PATH = REPO_ROOT / "frontend" / "src" / "lib" / "__fixtures__" / "patches.json"

DASHBOARD_ID = "dash_fixture"
_BASE_TIME = datetime(2025, 1, 1, 9, 0, 0, tzinfo=UTC)

#: Skema hasil query tetap yang direferensikan chart fixture.
QUERY_SCHEMAS: dict[str, list[ColumnInfo]] = {
    "q_sales": [
        ColumnInfo(name="region", type="string"),
        ColumnInfo(name="month", type="date"),
        ColumnInfo(name="revenue", type="float"),
        ColumnInfo(name="qty", type="integer"),
    ],
}


def _converter(spec: ChartSpec, new_type: ChartType) -> ChartSpec:
    return convert_chart_type(spec, new_type, QUERY_SCHEMAS[spec.query_id])


# ---------------------------------------------------------------------------
# Data contoh
# ---------------------------------------------------------------------------


def _bar_spec(measure: str = "revenue") -> ChartSpec:
    raw = {
        "query_id": "q_sales",
        "chart_type": "bar",
        "option": {
            "xAxis": {"type": "category"},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "encode": {"x": "region", "y": measure}}],
            "tooltip": {"trigger": "axis"},
        },
        "cross_filter_column": "region",
    }
    return validate_chart_spec(raw, QUERY_SCHEMAS["q_sales"])


def _insight_draft() -> InsightDraft:
    return InsightDraft(
        insight_type="top_bottom_contributors",
        title="Kontributor teratas",
        text="North menyumbang 1250.5 dari total revenue.",
        query_id="q_sales",
        sql="SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY 1",
        evidence=EvidenceTable(
            columns=[
                ColumnInfo(name="region", type="string"),
                ColumnInfo(name="revenue", type="float"),
            ],
            rows=[["North", 1250.5], ["South", 980.0]],
            row_count=2,
        ),
        matched_numbers=[NumberMatch(token="1250.5", value=1250.5, row=0, column="revenue")],
        filters_snapshot=[],
        dataset_ids=["ds_sales"],
        computed_at=_BASE_TIME,
        dataset_versions={"ds_sales": 1},
    )


def _kpi_spec(value: str = "revenue", style: str = "currency") -> KpiSpec:
    return KpiSpec(
        query_id="q_sales",
        value_column=value,
        comparison_column=value,
        comparison_label="vs bulan lalu",
        format=NumberFormat(style=style, decimals=0),  # type: ignore[arg-type]
        good_direction="up",
        metric_name=value,
    )


def _brief() -> DesignBrief:
    return DesignBrief(
        purpose="Memantau penjualan bulanan",
        audience="Manajer penjualan",
        key_questions=["Region mana yang tumbuh?"],
        kpis=[BriefKpi(metric="revenue", compare="previous_period")],
        sections=["kpi_row", "trend", "breakdown"],
        time_grain="month",
        assumptions=["Periode = kalender"],
    )


_FILTERS = [
    InPredicate(table="sales", column="region", values=("North", "South")),
    DateRangePredicate(table="sales", column="month", start=date(2024, 1, 1), end=date(2024, 6, 30)),
]


# ---------------------------------------------------------------------------
# Simulator Dashboard_Store murni (tanpa I/O)
# ---------------------------------------------------------------------------


class _Session:
    """Terapkan command/undo/redo seperti Dashboard_Store, mencatat Patch_Event."""

    def __init__(self, content: DashboardContent) -> None:
        self.initial = content
        self.content = content
        self.version = 0
        self.history = History()
        self.patches: list[PatchEvent] = []
        self._by_id: dict[str, PatchEvent] = {}
        self._item_seq = 0

    def _new_item_id(self) -> str:
        self._item_seq += 1
        return f"item_{self._item_seq:02d}"

    def _emit(self, source: PatchSource, fields: dict[str, Any]) -> PatchEvent:
        n = len(self.patches) + 1
        event = PatchEvent(
            id=f"patch_{n:02d}",
            dashboard_id=DASHBOARD_ID,
            version=self.version + 1,
            base_version=self.version,
            source=source,
            created_at=_BASE_TIME + timedelta(minutes=n),
            **fields,
        )
        self.content = apply_ops(self.content, event.ops)
        self.version = event.version
        self.history = self.history.record(event)
        self.patches.append(event)
        self._by_id[event.id] = event
        return event

    def command(self, command: Command, source: PatchSource = "user") -> PatchEvent:
        ops = resolve_command(
            self.content, command, new_id=self._new_item_id, convert_chart_type=_converter
        )
        return self._emit(source, {"ops": ops, "inverse_ops": invert_ops(ops)})

    def undo(self, source: PatchSource = "user") -> PatchEvent:
        target = self._by_id[self.history.peek_undo()]
        return self._emit(source, undo_patch_fields(self.history, target))

    def redo(self, source: PatchSource = "user") -> PatchEvent:
        target = self._by_id[self.history.peek_redo()]
        return self._emit(source, redo_patch_fields(self.history, target))


def _base_session(*, with_insight: bool = True) -> _Session:
    """Session dengan chart ``item_01`` (+ insight ``item_02``) sebagai state awal."""
    seed = _Session(DashboardContent(title="Penjualan"))
    seed.command(AddChartCommand(title="Revenue per region", spec=_bar_spec()))
    if with_insight:
        seed.command(AddInsightCommand(insight=_insight_draft()))
    session = _Session(seed.content)
    session._item_seq = seed._item_seq
    return session


# ---------------------------------------------------------------------------
# Skenario
# ---------------------------------------------------------------------------


def _scenarios() -> list[tuple[str, str, _Session]]:
    out: list[tuple[str, str, _Session]] = []

    s = _Session(DashboardContent(title="Penjualan"))
    s.command(AddChartCommand(title="Revenue per region", spec=_bar_spec()), source="agent")
    s.command(
        AddInsightCommand(insight=_insight_draft(), layout=LayoutRect(x=6, y=0, w=6, h=3)),
        source="agent",
    )
    out.append(("add_item", "add_item chart (layout default) dan insight (layout eksplisit)", s))

    s = _base_session()
    s.command(RemoveItemCommand(id="item_02"))
    out.append(("remove_item", "remove_item membawa snapshot item & layout", s))

    s = _base_session(with_insight=False)
    s.command(UpdateChartCommand(id="item_01", spec=_bar_spec("qty"), title="Qty per region"))
    out.append(("set_item_chart_update", "set_item dari update_chart (spec & judul baru)", s))

    s = _base_session(with_insight=False)
    s.command(ChangeChartTypeCommand(id="item_01", chart_type="line"))
    s.command(ChangeChartTypeCommand(id="item_01", chart_type="pie"))
    s.command(ChangeChartTypeCommand(id="item_01", chart_type="bar"))
    out.append(("set_item_chart_type", "set_item dari change_chart_type bar→line→pie→bar", s))

    s = _base_session()
    s.command(
        UpdateInsightCommand(
            id="item_02",
            changes=InsightChanges(title="Region teratas", text="North memimpin revenue."),
        )
    )
    out.append(("set_item_insight", "set_item dari update_insight (perubahan parsial)", s))

    s = _base_session()
    s.command(
        SetLayoutCommand(
            changes={
                "item_01": LayoutRect(x=0, y=0, w=8, h=5),
                "item_02": LayoutRect(x=8, y=0, w=4, h=5),
            }
        )
    )
    out.append(("set_layout", "set_layout untuk dua item sekaligus", s))

    s = _base_session()
    s.command(SetGlobalFiltersCommand(filters=list(_FILTERS)))
    s.command(SetGlobalFiltersCommand(filters=[_FILTERS[0]]))
    s.command(SetGlobalFiltersCommand(filters=[]))
    out.append(("set_filters", "set_filters: tambah, kurangi, kosongkan filter global", s))

    s = _base_session()
    s.command(SetTitleCommand(title="Penjualan Q1"), source="agent")
    out.append(("set_title", "set_title", s))

    s = _Session(DashboardContent(title="Penjualan"))
    s.command(AddChartCommand(title="Revenue per region", spec=_bar_spec()))
    s.command(SetTitleCommand(title="Penjualan 2024"))
    s.undo()
    s.undo()
    s.redo()
    out.append(("undo_redo", "dua perubahan, undo dua kali, redo sekali", s))

    s = _base_session()
    s.command(SetLayoutCommand(changes={"item_01": LayoutRect(x=0, y=0, w=12, h=4)}))
    s.undo()
    s.redo()
    s.undo()
    s.command(SetTitleCommand(title="Judul baru"))  # patch normal mengosongkan redo
    s.undo()
    out.append(("undo_redo_chain", "undo/redo berulang lalu patch normal baru dan undo", s))

    s = _base_session()
    s.command(RemoveItemCommand(id="item_01"))
    s.command(SetGlobalFiltersCommand(filters=list(_FILTERS)))
    s.undo()
    s.undo()
    out.append(("undo_remove_and_filters", "undo set_filters dan remove_item (item kembali)", s))

    s = _base_session(with_insight=False)
    s.command(
        AddKpiCommand(title="Total revenue", spec=_kpi_spec(), layout=LayoutRect(x=0, y=10, w=3, h=2)),
        source="agent",
    )
    s.command(
        UpdateKpiCommand(
            id="item_02",
            spec=_kpi_spec("qty", style="number"),
            title="Total qty",
        )
    )
    s.undo()
    out.append(("kpi", "add_kpi, update_kpi, lalu undo update", s))

    s = _base_session(with_insight=False)
    s.command(SetBriefCommand(brief=_brief()), source="agent")
    s.command(SetBriefCommand(brief=_brief().model_copy(update={"audience": "Direksi"})))
    s.undo()
    s.redo()
    s.command(SetBriefCommand(brief=None))
    out.append(("set_brief", "set_brief: buat, ubah, undo/redo, hapus", s))

    return out


def build_fixtures() -> dict[str, Any]:
    """Seluruh isi ``patches.json`` (deterministik)."""
    scenarios = []
    for name, description, session in _scenarios():
        assert replay(session.initial, session.patches) == session.content
        scenarios.append(
            {
                "name": name,
                "description": description,
                "initial": session.initial.model_dump(mode="json"),
                "patches": [p.model_dump(mode="json") for p in session.patches],
                "expected": session.content.model_dump(mode="json"),
            }
        )
    return {
        "generated_by": "backend/tests/fixtures/gen_patch_fixtures.py",
        "dashboard_id": DASHBOARD_ID,
        "scenarios": scenarios,
    }


def render_fixtures() -> str:
    return json.dumps(build_fixtures(), indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 bila file usang")
    args = parser.parse_args(argv)
    text = render_fixtures()
    if args.check:
        current = FIXTURE_PATH.read_text(encoding="utf-8") if FIXTURE_PATH.exists() else None
        if current != text:
            print(f"{FIXTURE_PATH} usang; jalankan ulang generator.", file=sys.stderr)
            return 1
        return 0
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"Menulis {FIXTURE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
