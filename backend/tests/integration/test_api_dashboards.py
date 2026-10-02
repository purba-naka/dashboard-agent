"""Integration test Dashboard, relasi, render, refresh insight, dan detail query (task 16.8).

_Requirements: 7.3, 7.4, 7.5, 7.8, 14.7, 15.1, 17.5, 18.4, 18.5, 19.1, 19.2, 22.3, 23.3, 24.2_
"""

from __future__ import annotations

from typing import Any

from tests.integration.helpers import (
    EXPECTED_FACTS,
    SQL,
    Studio,
    bar_spec,
    dataset_rows,
    err,
    insight_draft,
    pie_spec,
)

WEBSITE = {"kind": "in", "table": "transactions", "column": "channel", "values": ["Website"]}
NOVEMBER = {
    "kind": "date_range",
    "table": "transactions",
    "column": "transaction_date",
    "start": "2024-11-01",
    "end": "2024-11-30",
}


def _channel(name: str) -> dict[str, Any]:
    return next(c for c in EXPECTED_FACTS["revenue_by_channel"] if c["channel"] == name)


def _total(option: dict[str, Any], column: str) -> int:
    return sum(row[column] for row in dataset_rows(option))


# ---------------------------------------------------------------------------
# Patch, konflik versi, undo/redo
# ---------------------------------------------------------------------------


async def test_patches_conflict_validation_undo_redo(studio: Studio) -> None:
    client = studio.client
    ws = await studio.create_workspace()
    await studio.upload_sample(ws, "customers.csv")
    query = await studio.execute(ws, SQL["customers_by_region"])
    dash = await studio.create_dashboard(ws, "Pelanggan")
    dash_id = dash["id"]
    assert dash == {
        "id": dash_id,
        "title": "Pelanggan",
        "version": 0,
        "content": {
            "title": "Pelanggan",
            "items": {},
            "layout": {},
            "global_filters": [],
            "brief": None,
        },
        "can_undo": False,
        "can_redo": False,
        "item_status": {},
    }
    listed = (await client.get(f"/api/workspaces/{ws}/dashboards")).json()
    assert [d["id"] for d in listed] == [dash_id]

    spec = bar_spec(query.query_id, "region", "customers")
    first = await studio.apply_ok(
        dash_id, {"type": "add_chart", "title": "Per region", "spec": spec}, 0
    )
    assert first["version"] == 1 and first["base_version"] == 0
    assert first["source"] == "user" and first["kind"] == "normal"
    item_id = first["ops"][0]["item"]["id"]

    # --- patches?since_version ----------------------------------------------
    url = f"/api/dashboards/{dash_id}/patches"
    body = (await client.get(url, params={"since_version": 0})).json()
    assert body["version"] == 1 and [p["id"] for p in body["patches"]] == [first["id"]]
    assert (await client.get(url, params={"since_version": 1})).json() == {
        "patches": [],
        "version": 1,
    }
    ahead = (await client.get(url, params={"since_version": 9})).json()
    assert set(ahead) == {"snapshot"} and ahead["snapshot"]["version"] == 1

    # --- 409 VERSION_CONFLICT -------------------------------------------------
    resp = await studio.apply(dash_id, {"type": "set_title", "title": "Basi"}, 0)
    assert resp.status_code == 409, resp.text
    error = err(resp)
    assert error["code"] == "VERSION_CONFLICT"
    assert error["details"]["current_version"] == 1

    # --- 422 command tidak valid (Dashboard tidak berubah) ---------------------
    bad_spec = bar_spec(query.query_id, "region", "tidak_ada")
    invalid: list[tuple[dict[str, Any], str]] = [
        ({"type": "explode"}, "VALIDATION_ERROR"),
        ({"type": "set_title"}, "VALIDATION_ERROR"),
        ({"type": "add_chart", "title": "x", "spec": bad_spec}, "UNKNOWN_COLUMN"),
        (
            {"type": "add_chart", "title": "x", "spec": bar_spec("q-hilang", "region", "customers")},
            "UNKNOWN_QUERY",
        ),
    ]
    for command, code in invalid:
        resp = await studio.apply(dash_id, command, 1)
        assert resp.status_code == 422, (command, resp.text)
        assert err(resp)["code"] == code, command
    resp = await client.post(url, json={"base_version": -1, "command": {"type": "set_title", "title": "x"}})
    assert resp.status_code == 422
    assert (await studio.get_dashboard(dash_id))["version"] == 1

    # --- undo / redo ---------------------------------------------------------
    resp = await client.post(f"/api/dashboards/{dash_id}/undo", json={"base_version": 1})
    assert resp.status_code == 200, resp.text
    undo = resp.json()
    assert undo["kind"] == "undo" and undo["version"] == 2
    assert undo["target_patch_id"] == first["id"] and undo["source"] == "user"
    snapshot = await studio.get_dashboard(dash_id)
    assert snapshot["content"]["items"] == {} and snapshot["can_redo"] is True

    resp = await client.post(f"/api/dashboards/{dash_id}/redo", json={"base_version": 1})
    assert resp.status_code == 409 and err(resp)["details"]["current_version"] == 2

    resp = await client.post(f"/api/dashboards/{dash_id}/redo", json={"base_version": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "redo" and resp.json()["version"] == 3
    snapshot = await studio.get_dashboard(dash_id)
    assert list(snapshot["content"]["items"]) == [item_id]
    assert snapshot["can_undo"] is True and snapshot["can_redo"] is False

    resp = await client.post(f"/api/dashboards/{dash_id}/redo", json={"base_version": 3})
    assert resp.status_code == 400 and err(resp)["code"] == "NOTHING_TO_REDO"

    empty = await studio.create_dashboard(ws, "Kosong")
    resp = await client.post(f"/api/dashboards/{empty['id']}/undo", json={"base_version": 0})
    assert resp.status_code == 400 and err(resp)["code"] == "NOTHING_TO_UNDO"

    history = (await client.get(url)).json()
    assert [(p["version"], p["kind"]) for p in history["patches"]] == [
        (1, "normal"),
        (2, "undo"),
        (3, "redo"),
    ]
    resp = await client.get("/api/dashboards/tidak-ada")
    assert resp.status_code == 404 and err(resp)["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Relasi
# ---------------------------------------------------------------------------


async def test_relations_list_confirm_reject_delete(studio: Studio) -> None:
    client = studio.client
    ids = await studio.sales_workspace()
    ws = ids["ws"]
    customers = await studio.relation_between(ws, "customers", "customer_id", "transactions")
    products = await studio.relation_between(ws, "products", "product_id", "transactions")
    assert customers["status"] == products["status"] == "candidate"
    for rel in (customers, products):
        assert rel["cardinality"] == "one_to_many" and rel["overlap_pct"] > 0

    await studio.confirm(ws, customers["id"])
    resp = await client.post(f"/api/workspaces/{ws}/relations/{products['id']}/reject")
    assert resp.status_code == 200 and resp.json()["status"] == "rejected"

    by_status = {s: {r["id"] for r in await studio.relations(ws, s)} for s in ("candidate", "confirmed", "rejected")}
    assert customers["id"] in by_status["confirmed"] and customers["id"] not in by_status["candidate"]
    assert products["id"] in by_status["rejected"] and products["id"] not in by_status["candidate"]
    both = await studio.relations(ws, "confirmed,rejected")
    assert {customers["id"], products["id"]} <= {r["id"] for r in both}

    resp = await client.get(f"/api/workspaces/{ws}/relations", params={"status": "bogus"})
    assert resp.status_code == 422 and err(resp)["code"] == "VALIDATION_ERROR"
    resp = await client.post(f"/api/workspaces/{ws}/relations/tidak-ada/confirm")
    assert resp.status_code == 404

    resp = await client.delete(f"/api/workspaces/{ws}/relations/{customers['id']}")
    assert resp.status_code == 204, resp.text
    assert customers["id"] not in {r["id"] for r in await studio.relations(ws)}
    assert await studio.relations(ws, "confirmed") == []


# ---------------------------------------------------------------------------
# Render: Global_Filter, Cross_Filter, filter_unaffected, invalid, pie > 8
# ---------------------------------------------------------------------------


async def test_render_with_global_and_cross_filters(studio: Studio) -> None:
    client = studio.client
    ids = await studio.sales_workspace()
    ws = ids["ws"]
    rel = await studio.relation_between(ws, "customers", "customer_id", "transactions")
    await studio.confirm(ws, rel["id"])  # products ↔ transactions tetap kandidat

    queries = {
        "month": await studio.execute(ws, SQL["revenue_by_month"]),
        "channel": await studio.execute(ws, SQL["revenue_by_channel"]),
        "customers": await studio.execute(ws, SQL["customers_by_region"]),
        "products": await studio.execute(
            ws, "SELECT category, COUNT(*) AS products FROM products GROUP BY category ORDER BY category"
        ),
        "pie": await studio.execute(ws, "SELECT product_name, price FROM products"),
        "region": await studio.execute(ws, SQL["revenue_by_region"]),
    }
    specs = {
        "month": bar_spec(queries["month"].query_id, "month", "revenue"),
        "channel": bar_spec(
            queries["channel"].query_id, "channel", "revenue", cross_filter_column="channel"
        ),
        "customers": bar_spec(queries["customers"].query_id, "region", "customers"),
        "products": bar_spec(queries["products"].query_id, "category", "products"),
        "pie": pie_spec(queries["pie"].query_id, "product_name", "price"),
        "region": bar_spec(queries["region"].query_id, "region", "revenue"),
    }
    dash_id = (await studio.create_dashboard(ws))["id"]
    version = 0
    items: dict[str, str] = {}
    for key, spec in specs.items():
        items[key], version = await studio.add_chart(dash_id, version, key, spec)

    def by_key(rendered: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {key: rendered["items"][item_id] for key, item_id in items.items()}

    # --- tanpa filter -----------------------------------------------------
    out = by_key(await studio.render(dash_id))
    for key in ("month", "channel", "customers", "products", "region"):
        assert out[key]["status"] == "ok" and out[key]["filter_unaffected"] is False, key
    assert dataset_rows(out["month"]["option"]) == EXPECTED_FACTS["monthly_trend"]["months"]
    assert dataset_rows(out["channel"]["option"]) == EXPECTED_FACTS["revenue_by_channel"]
    assert _total(out["customers"]["option"], "customers") == 200
    assert _total(out["products"]["option"], "products") == 30
    # Pie dengan > 8 kategori → error per item, item lain tetap dirender.
    assert out["pie"]["status"] == "error" and "option" not in out["pie"]
    assert out["pie"]["error"]["code"] == "AXIS_STRUCTURE"
    assert out["pie"]["error"]["details"]["categories"] == 30

    # --- Cross_Filter channel=Website ----------------------------------------
    website = _channel("Website")
    out = by_key(await studio.render(dash_id, cross_filters=[WEBSITE]))
    assert _total(out["month"]["option"], "revenue") == website["revenue"]
    assert _total(out["month"]["option"], "transactions") == website["transactions"]
    # Chart sumber Cross_Filter tidak difilter oleh filternya sendiri (Req 24.2).
    assert out["channel"]["filter_unaffected"] is False
    assert dataset_rows(out["channel"]["option"]) == EXPECTED_FACTS["revenue_by_channel"]
    # customers terhubung lewat Confirmed_Relation → ikut terfilter (semi-join).
    assert out["customers"]["filter_unaffected"] is False
    assert 0 < _total(out["customers"]["option"], "customers") < 200
    # products tidak terhubung (relasi belum dikonfirmasi) → filter_unaffected (Req 23.3).
    assert out["products"]["status"] == "ok" and out["products"]["filter_unaffected"] is True
    assert _total(out["products"]["option"], "products") == 30

    # Subset item_ids + id tidak dikenal.
    rendered = await studio.render(dash_id, item_ids=[items["products"], "tidak-ada"])
    assert set(rendered["items"]) == {items["products"], "tidak-ada"}
    assert rendered["items"]["tidak-ada"]["error"]["code"] == "NOT_FOUND"

    # --- Global_Filter (disimpan di Dashboard) --------------------------------
    november = EXPECTED_FACTS["monthly_trend"]["anomaly"]
    event = await studio.apply_ok(
        dash_id, {"type": "set_global_filters", "filters": [NOVEMBER]}, version
    )
    version = event["version"]
    out = by_key(await studio.render(dash_id))
    assert dataset_rows(out["month"]["option"]) == [
        {"month": "2024-11", "revenue": november["revenue"], "transactions": november["transactions"]}
    ]
    assert _total(out["channel"]["option"], "revenue") == november["revenue"]
    assert out["products"]["filter_unaffected"] is True
    # Global + Cross: chart sumber tetap memakai Global_Filter saja.
    out = by_key(await studio.render(dash_id, cross_filters=[WEBSITE]))
    assert _total(out["channel"]["option"], "revenue") == november["revenue"]

    # FilterSet tidak valid menggagalkan permintaan.
    resp = await client.post(
        f"/api/dashboards/{dash_id}/render",
        json={"cross_filters": [{**NOVEMBER, "start": "bukan-tanggal"}]},
    )
    assert resp.status_code == 422, resp.text
    assert err(resp)["code"] == "VALIDATION_ERROR"

    # --- relasi dihapus → item yang memakai JOIN menjadi invalid (Req 7.8) -----
    resp = await client.delete(f"/api/workspaces/{ws}/relations/{rel['id']}")
    assert resp.status_code == 204
    snapshot = await studio.get_dashboard(dash_id)
    assert snapshot["item_status"][items["region"]] == {"invalid": True, "stale": False}
    assert snapshot["item_status"][items["customers"]] == {"invalid": False, "stale": False}
    out = by_key(await studio.render(dash_id, cross_filters=[WEBSITE]))
    assert out["region"]["status"] == "invalid" and "option" not in out["region"]
    assert out["region"]["error"]["code"] == "ITEM_INVALID"
    assert out["region"]["error"]["details"]["relation_ids"] == [rel["id"]]
    # customers tidak lagi terhubung ke transactions.
    assert out["customers"]["filter_unaffected"] is True
    assert _total(out["customers"]["option"], "customers") == 200


# ---------------------------------------------------------------------------
# Refresh insight & detail query
# ---------------------------------------------------------------------------


async def test_insight_refresh_outdated_then_rewritten(studio: Studio) -> None:
    client = studio.client
    ids = await studio.sales_workspace(products=False)
    ws, tx = ids["ws"], ids["transactions"]
    sql = "SELECT SUM(amount) AS revenue FROM transactions"
    result = await studio.execute(ws, sql)
    total = EXPECTED_FACTS["totals"]["revenue"]
    assert result.rows == [[total]]

    dash_id = (await studio.create_dashboard(ws))["id"]
    chart_id, v = await studio.add_chart(
        dash_id, 0, "Total", bar_spec(result.query_id, "revenue", "revenue")
    )
    insight_id, v = await studio.add_insight(
        dash_id, v, insight_draft(result, sql, f"Total pendapatan {total}.", [tx], {tx: 1})
    )
    v = (await studio.apply_ok(dash_id, {"type": "set_global_filters", "filters": [WEBSITE]}, v))[
        "version"
    ]
    url = f"/api/dashboards/{dash_id}/insights/{insight_id}/refresh"

    resp = await client.post(url, json={"base_version": v - 1})
    assert resp.status_code == 409 and err(resp)["code"] == "VERSION_CONFLICT"
    assert err(resp)["details"]["current_version"] == v

    # Tanpa rewriter: angka lama tidak cocok dengan bukti baru → 409, Dashboard tetap.
    assert studio.app.state.insight_rewriter is None
    resp = await client.post(url, json={"base_version": v})
    assert resp.status_code == 409, resp.text
    error = err(resp)
    assert error["code"] == "INSIGHT_TEXT_OUTDATED"
    assert error["details"]["item_id"] == insight_id
    assert error["details"]["query_id"] == result.query_id
    assert error["details"]["unmatched"]
    assert (await studio.get_dashboard(dash_id))["version"] == v

    # Dengan rewriter (Insight_Agent, task 18.11): teks disusun ulang dari bukti baru.
    calls: list[tuple[str, list[list[Any]]]] = []

    def rewriter(item: Any, evidence: Any) -> str:
        calls.append((item.id, evidence.rows))
        return f"Total pendapatan Website {evidence.rows[0][0]}."

    studio.app.state.insight_rewriter = rewriter
    website = _channel("Website")["revenue"]
    resp = await client.post(url, json={"base_version": v})
    assert resp.status_code == 200, resp.text
    event = resp.json()
    assert event["version"] == v + 1 and event["source"] == "user"
    assert calls == [(insight_id, [[website]])]

    insight = (await studio.get_dashboard(dash_id))["content"]["items"][insight_id]
    assert insight["text"] == f"Total pendapatan Website {website}."
    assert insight["evidence"]["rows"] == [[website]]
    assert insight["filters_snapshot"] == [WEBSITE]
    assert insight["dataset_versions"] == {tx: 1}
    assert [m["value"] for m in insight["matched_numbers"]] == [website]

    # Item bukan insight / tidak ada.
    resp = await client.post(
        f"/api/dashboards/{dash_id}/insights/{chart_id}/refresh", json={"base_version": v + 1}
    )
    assert resp.status_code == 422 and err(resp)["code"] == "NOT_INSIGHT"
    resp = await client.post(
        f"/api/dashboards/{dash_id}/insights/tidak-ada/refresh", json={"base_version": v + 1}
    )
    assert resp.status_code == 404


async def test_query_detail(studio: Studio) -> None:
    client = studio.client
    ws = await studio.create_workspace()
    await studio.upload_sample(ws, "customers.csv")
    sql = SQL["customers_by_region"]
    result = await studio.execute(ws, sql)

    resp = await client.get(f"/api/queries/{result.query_id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["sql"] == sql
    assert detail["columns"] == [
        {"name": "region", "type": "string"},
        {"name": "customers", "type": "integer"},
    ]
    assert detail["rows"] == [[r["region"], r["customers"]] for r in EXPECTED_FACTS["customers_by_region"]]
    assert detail["row_count"] == 5 and detail["filters"] == []
    assert detail["executed_at"].endswith("Z") or "+00:00" in detail["executed_at"]

    resp = await client.get("/api/queries/tidak-ada")
    assert resp.status_code == 404 and err(resp)["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Halaman = Dashboard: hapus
# ---------------------------------------------------------------------------

async def test_delete_page_rejects_last_and_removes_other(studio: Studio) -> None:
    ws = await studio.create_workspace()
    first = await studio.create_dashboard(ws, "Overview")
    resp = await studio.client.delete(f"/api/dashboards/{first['id']}")
    assert resp.status_code == 409 and err(resp)["code"] == "LAST_PAGE"

    second = await studio.create_dashboard(ws, "Revenue")
    resp = await studio.client.delete(f"/api/dashboards/{second['id']}")
    assert resp.status_code == 204
    listed = await studio.client.get(f"/api/workspaces/{ws}/dashboards")
    assert [d["id"] for d in listed.json()] == [first["id"]]
    assert (await studio.client.get(f"/api/dashboards/{second['id']}")).status_code == 404
