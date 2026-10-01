"""Integration test alur Blueprint dengan model ADK di-mock (task 31.9).

"buatkan dashboard" → Architect ``propose_dashboard_plan`` → kartu
``approval.request(kind=blueprint)`` → setujui 3 dari 5 slot → Root membangun
slot berurutan lewat Blueprint_Builder (Slot_Builder: run_sql → add_* dengan ``slot_id``) → item tepat di layout
slot + ``blueprint.progress`` → filter bawaan diterapkan → ``review.findings``.

_Requirements: 37.5, 37.6, 37.7, 37.8, 37.12, 39.1_
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio.agents.definitions import ARCHITECT_NAME, BUILDER_NAME, ROOT_NAME
from tests.integration.helpers import Studio
from tests.integration.test_chat_stream import FakeModel, call, chat_stream, make_app, running, text

SALES_CSV = b"month,region,revenue\n2024-01-01,Jawa,1500\n2024-01-01,Bali,700\n2024-02-01,Jawa,1700\n2024-02-01,Bali,650\n"

BLUEPRINT = {
    "brief": {
        "purpose": "Memantau penjualan bulanan",
        "audience": "Manajer penjualan",
        "key_questions": ["Bagaimana tren revenue?"],
        "sections": ["kpi_row", "trend", "breakdown"],
        "assumptions": ["revenue dalam Rupiah"],
    },
    "slots": [
        {"slot_id": "kpi_rev", "section": "kpi_row", "purpose": "Total revenue", "visual": "kpi", "metrics": ["revenue"]},
        {"slot_id": "kpi_extra", "section": "kpi_row", "purpose": "Revenue rata-rata", "visual": "kpi", "metrics": ["revenue"]},
        {"slot_id": "trend_rev", "section": "trend", "purpose": "Tren revenue per bulan", "visual": "line",
         "metrics": ["revenue"], "dimension": "month"},
        {"slot_id": "by_region", "section": "breakdown", "purpose": "Revenue per region", "visual": "bar",
         "metrics": ["revenue"], "dimension": "region", "cross_filter_column": "region"},
        {"slot_id": "notes", "section": "detail", "purpose": "Catatan", "visual": "insight", "metrics": ["revenue"]},
    ],
    "default_filters": [{"kind": "in", "table": "sales", "column": "region", "values": ["Jawa", "Bali"]}],
}
SELECTED = ["kpi_rev", "trend_rev", "by_region"]

LINE = {
    "title": {"text": "Tren revenue"},
    "tooltip": {"trigger": "axis"},
    "xAxis": {"type": "category", "name": "Bulan"},
    "yAxis": {"type": "value", "name": "Revenue"},
    "series": [{"type": "line", "encode": {"x": "month", "y": "revenue"}}],
}
BAR = {
    "title": {"text": "Revenue per region"},
    "tooltip": {"trigger": "axis"},
    "xAxis": {"type": "category", "name": "Region"},
    "yAxis": {"type": "value", "name": "Revenue"},
    "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
}


def _back() -> Any:
    return call("transfer_to_agent", {"agent_name": ROOT_NAME})


async def _collect(app: Any, ws: str, body: dict[str, Any]) -> list[Any]:
    async with chat_stream(app, ws, body) as (resp, reader):
        assert resp.status_code == 200
        return await reader.until(lambda f: f.event == "run.done", timeout=60)


async def test_blueprint_propose_approve_build_review(tmp_path: Path) -> None:
    root = [
        # Giliran 1: rancang.
        call("classify_turn", {"intent": "dashboard_modification", "explicit_change_request": False}),
        call("transfer_to_agent", {"agent_name": ARCHITECT_NAME}),
        text("Rancangan siap, silakan setujui."),
        # Giliran 2: satu transfer; loop slot dikerjakan Blueprint_Builder.
        call("transfer_to_agent", {"agent_name": BUILDER_NAME}),
    ]
    architect = [
        call("propose_dashboard_plan", {"summary": "Dashboard penjualan bulanan", "blueprint": BLUEPRINT}),
        _back(),
    ]
    # Slot_Builder memakai model chart: run_sql → add_* → teks, per slot.
    chart = [
        call("run_sql", {"sql": "SELECT SUM(revenue) AS total, SUM(revenue) AS prev FROM sales"}),
        call("add_kpi", {"base_version": 0, "title": "Revenue", "value_column": "total",
                         "comparison_column": "prev", "format_style": "currency", "slot_id": "kpi_rev"}),
        text("KPI selesai."),
        call("run_sql", {"sql": "SELECT month, SUM(revenue) AS revenue FROM sales GROUP BY month ORDER BY month"}),
        call("add_chart", {"base_version": 1, "title": "Tren revenue", "chart_type": "line", "option": LINE,
                           "slot_id": "trend_rev"}),
        text("Tren selesai."),
        call("run_sql", {"sql": "SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region"}),
        call("add_chart", {"base_version": 2, "title": "Revenue per region", "chart_type": "bar", "option": BAR,
                           "cross_filter_column": "region", "slot_id": "by_region"}),
        text("Region selesai."),
    ]
    default = FakeModel.of()
    models = {
        "root": FakeModel.of(*root),
        "architect": FakeModel.of(*architect),
        "query": default,
        "chart": FakeModel.of(*chart),
        "profiler": default,
        "insight": default,
    }
    app = make_app(tmp_path, models)
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace()
        await studio.upload_csv(ws, "sales.csv", SALES_CSV)
        dash = await studio.create_dashboard(ws)

        # --- giliran 1: usulan Blueprint -------------------------------------
        frames = await _collect(app, ws, {"message": "Buatkan dashboard penjualan"})
        session_id = frames[0].data["session_id"]
        (card,) = [f for f in frames if f.event == "approval.request"]
        assert card.data["kind"] == "blueprint"
        proposed = {s["slot_id"]: s for s in card.data["blueprint"]["slots"]}
        assert set(proposed) == {s["slot_id"] for s in BLUEPRINT["slots"]}
        assert all(s["layout"] for s in proposed.values())
        assert proposed["kpi_rev"]["layout"]["y"] < proposed["trend_rev"]["layout"]["y"]
        assert (await studio.get_dashboard(dash["id"]))["version"] == 0  # belum ada mutasi

        # --- giliran 2: setujui 3 slot → bangun ---------------------------------
        frames = await _collect(
            app,
            ws,
            {
                "session_id": session_id,
                "message": "Setuju, bangun slot terpilih",
                "approval": {"proposal_id": card.data["proposal_id"], "selected_slot_ids": SELECTED},
            },
        )
        errors = [f.data for f in frames if f.event == "error"]
        assert not errors, errors
        progress = [(f.data["slot_id"], f.data["status"]) for f in frames if f.event == "blueprint.progress"]
        for sid in SELECTED:
            assert (sid, "building") in progress and (sid, "done") in progress
        assert not [p for p in progress if p[0] in ("kpi_extra", "notes")]
        assert any(f.event == "review.findings" for f in frames)

        snap = await studio.get_dashboard(dash["id"])
        content = snap["content"]
        assert len(content["items"]) == 3
        by_title = {item["title"]: item_id for item_id, item in content["items"].items()}
        assert content["layout"][by_title["Revenue"]] == proposed["kpi_rev"]["layout"]
        assert content["layout"][by_title["Tren revenue"]] == proposed["trend_rev"]["layout"]
        assert content["layout"][by_title["Revenue per region"]] == proposed["by_region"]["layout"]
        assert content["items"][by_title["Revenue"]]["kind"] == "kpi"
        assert content["global_filters"][0]["column"] == "region"  # filter bawaan (Req 37.12)

        rendered = await studio.render(dash["id"])
        kpi = rendered["items"][by_title["Revenue"]]
        assert kpi["status"] == "ok" and kpi["kpi"]["value"] == 4550 and kpi["kpi"]["sentiment"] == "neutral"
        assert kpi["kpi"]["formatted"]["value"].startswith("Rp ")

        bp = (await client.get(f"/api/dashboards/{dash['id']}/blueprint")).json()
        assert bp["status"] == "completed"
        assert {s: v["status"] for s, v in bp["slot_status"].items()} == {s: "done" for s in SELECTED}

        semantic = (await client.get(f"/api/workspaces/{ws}/semantic", params={"kind": "verified_query"})).json()
        questions = {e["body"]["question"] for e in semantic["entries"]}
        assert {"Total revenue", "Tren revenue per bulan", "Revenue per region"} <= questions
