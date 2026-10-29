"""API canvas OpenUI: patch lewat ``/patches`` dan render Query batch (irisan 2–3).

Dilewati bila bun (sidecar OpenUI) tidak terpasang.
"""

from __future__ import annotations

import shutil

import pytest

from tests.integration.helpers import EXPECTED_FACTS, SQL, Studio, err

pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="bun tidak terpasang")

WEBSITE = {"kind": "in", "table": "transactions", "column": "channel", "values": ["Website"]}


async def test_canvas_patch_and_render(studio: Studio) -> None:
    client = studio.client
    ids = await studio.sales_workspace()
    ws = ids["ws"]
    q_month = (await studio.execute(ws, SQL["revenue_by_month"])).query_id
    q_channel = (await studio.execute(ws, SQL["revenue_by_channel"])).query_id
    dash = (await studio.create_dashboard(ws))["id"]

    program = "\n".join(
        [
            "root = VContainer([row])",
            f'q_m = Query("render_query", {{query: "{q_month}"}}, {{rows: []}})',
            f'q_c = Query("render_query", {{query: "{q_channel}"}}, {{rows: []}})',
            'trend = LineChart("Tren", q_m, "month", ["revenue"])',
            'mix = BarChart("Kanal", q_c, "channel", ["revenue"], {crossFilter: "channel"})',
            "row = HContainer([trend, mix], [2, 1])",
        ]
    )
    event = await studio.apply_ok(dash, {"type": "apply_canvas_patch", "patch": program}, 0)
    assert event["version"] == 1 and all(op["op"] == "set_statement" for op in event["ops"])
    snap = (await client.get(f"/api/dashboards/{dash}")).json()
    assert set(snap["content"]["statements"]) == {"root", "q_m", "q_c", "trend", "mix", "row"}

    def body(cross: list[dict] | None = None) -> dict:
        return {
            "queries": [
                {"key": "a", "tool": "render_query", "args": {"query": q_month}},
                {"key": "b", "tool": "render_query", "args": {"query": q_channel}},
                {"key": "c", "tool": "render_query", "args": {"query": "q_tidak_di_program"}},
            ],
            "cross_filters": cross or [],
        }

    resp = await client.post(f"/api/dashboards/{dash}/canvas/render", json=body())
    assert resp.status_code == 200, resp.text
    out = resp.json()["results"]
    assert out["a"]["status"] == "ok"
    assert [r["month"] for r in out["a"]["rows"]] == [m["month"] for m in EXPECTED_FACTS["monthly_trend"]["months"]]
    assert out["c"]["status"] == "error" and out["c"]["error"]["code"] == "NOT_FOUND"

    # Cross_Filter Website: tren terfilter, chart sumber (kanal) tidak (Req 24.2).
    out = (await client.post(f"/api/dashboards/{dash}/canvas/render", json=body([WEBSITE]))).json()["results"]
    website = next(c for c in EXPECTED_FACTS["revenue_by_channel"] if c["channel"] == "Website")
    assert sum(r["revenue"] for r in out["a"]["rows"]) == website["revenue"]
    assert len(out["b"]["rows"]) == len(EXPECTED_FACTS["revenue_by_channel"])

    # Patch tidak valid → 422 berisi semua pelanggaran.
    resp = await studio.apply(dash, {"type": "apply_canvas_patch", "patch": 'mix = PieChart("X", q_c, "kota", "revenue")'}, 1)
    assert resp.status_code == 422 and err(resp)["code"] == "CANVAS_PATCH_INVALID"
    assert err(resp)["details"]["errors"][0]["code"] == "UNKNOWN_COLUMN"
