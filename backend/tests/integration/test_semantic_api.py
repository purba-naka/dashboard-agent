"""Integration test Semantic_Drafter + API semantik (task 28.4).

upload → draft heuristik otomatis → pengayaan LLM (enricher mock; entri invalid
dibuang, confirmed tidak ditimpa) → confirm-all → ekspor → impor ulang
ekuivalen → impor invalid ditolak tanpa perubahan.

_Requirements: 31.6, 31.7, 31.11, 32.1, 32.5, 32.7, 32.8_
"""

from __future__ import annotations

import asyncio
from typing import Any

from studio.data.semantic_drafter import SemanticDraftOutput
from tests.integration.helpers import Studio


def _by_key(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["entry_key"]: e for e in model["entries"]}


async def _semantic(studio: Studio, ws: str) -> dict[str, Any]:
    resp = await studio.client.get(f"/api/workspaces/{ws}/semantic")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_semantic_draft_review_export_import(studio: Studio) -> None:
    client = studio.client
    drafter = studio.app.state.semantic_drafter
    ws = await studio.create_workspace("Penjualan")
    await studio.upload_sample(ws, "transactions.csv")
    await drafter.wait_idle()

    # --- draft heuristik otomatis (Req 32.1, 32.2) ------------------------------
    model = await _semantic(studio, ws)
    entries = _by_key(model)
    amount = entries["col:transactions.amount"]
    assert amount["status"] == "candidate" and amount["source"] == "auto"
    assert amount["body"]["default_aggregation"] == "sum"
    assert entries["metric:total_amount"]["body"]["expr"] == 'SUM("amount")'
    assert model["draft_run"]["status"] == "done"

    # Pengguna mengedit label kolom → confirmed/user (Req 31.7).
    resp = await client.patch(
        f"/api/workspaces/{ws}/semantic/entries/{amount['id']}",
        json={"body": {"label": "Nilai transaksi"}},
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["status"], resp.json()["source"]) == ("confirmed", "user")

    # --- pengayaan LLM (enricher mock) ------------------------------------------
    async def enricher(context: dict[str, Any]) -> SemanticDraftOutput:
        assert context["datasets"][0]["table_name"] == "transactions"
        return SemanticDraftOutput.model_validate(
            {
                "domain": "retail_sales",
                "domain_confidence": 0.9,
                "assumptions": ["amount dalam Rupiah"],
                "columns": [
                    {"table": "transactions", "column": "amount", "label": "LLM tidak boleh menimpa"},
                    {"table": "transactions", "column": "channel", "description": "Kanal penjualan"},
                    {"table": "transactions", "column": "ghost", "label": "x"},
                ],
                "metrics": [
                    {"name": "revenue", "expr": "SUM(amount)", "base_table": "transactions",
                     "synonyms": ["omzet"], "format_style": "currency"},
                    {"name": "bad_metric", "expr": "SUM(nope)", "base_table": "transactions"},
                    {"name": "not_agg", "expr": "amount", "base_table": "transactions"},
                ],
                "glossary": [{"term": "AOV", "description": "Average order value"}],
            }
        )

    drafter.enricher = enricher
    try:
        await drafter.run(ws)
    finally:
        drafter.enricher = None

    model = await _semantic(studio, ws)
    entries = _by_key(model)
    assert model["domain"] == "retail_sales"
    assert entries["col:transactions.amount"]["body"]["label"] == "Nilai transaksi"
    assert entries["col:transactions.channel"]["body"]["description"] == "Kanal penjualan"
    assert entries["metric:revenue"]["body"]["synonyms"] == ["omzet"]
    assert "metric:bad_metric" not in entries and "metric:not_agg" not in entries
    assert "term:aov" in entries
    discarded = {d["entry_key"] for d in model["draft_run"]["discarded"]}
    assert {"metric:bad_metric", "metric:not_agg", "col:transactions.ghost"} <= discarded

    # --- tolak lalu draft ulang: tidak diusulkan kembali (Req 31.8, 32.6) -------
    term_id = entries["term:aov"]["id"]
    assert (await client.post(f"/api/workspaces/{ws}/semantic/entries/{term_id}/reject")).status_code == 200
    await drafter.run(ws)
    assert _by_key(await _semantic(studio, ws))["term:aov"]["status"] == "rejected"

    # --- confirm-all, ekspor, impor ulang ekuivalen -------------------------------
    resp = await client.post(f"/api/workspaces/{ws}/semantic/confirm-all")
    assert resp.json()["confirmed"] > 0
    before = _by_key(await _semantic(studio, ws))
    exported = (await client.get(f"/api/workspaces/{ws}/semantic/export")).text
    assert "revenue" in exported

    resp = await client.post(
        f"/api/workspaces/{ws}/semantic/import", content=exported, headers={"Content-Type": "text/yaml"}
    )
    assert resp.status_code == 200, resp.text
    after = _by_key(resp.json())
    assert {k: (v["status"], v["body"]) for k, v in after.items()} == {
        k: (v["status"], v["body"]) for k, v in before.items()
    }

    # --- impor invalid ditolak seluruhnya (Req 31.11) ------------------------------
    bad = "metrics:\n  - name: fresh\n    expr: SUM(amount)\n    base_table: transactions\n  - name: broken\n    expr: SUM(nope)\n    base_table: transactions\n"
    resp = await client.post(
        f"/api/workspaces/{ws}/semantic/import", content=bad, headers={"Content-Type": "text/yaml"}
    )
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "SEMANTIC_IMPORT_INVALID"
    assert [i["entry_key"] for i in body["details"]["issues"]] == ["metric:broken"]
    assert "metric:fresh" not in _by_key(await _semantic(studio, ws))

    # --- metrik baru via API divalidasi (Req 31.6) ------------------------------------
    resp = await client.post(
        f"/api/workspaces/{ws}/semantic/entries",
        json={"kind": "metric", "body": {"name": "x", "expr": "SUM(nope)", "base_table": "transactions"}},
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "METRIC_INVALID"


async def test_llm_failure_keeps_heuristic_draft(studio: Studio) -> None:
    drafter = studio.app.state.semantic_drafter
    events: list[tuple[str, Any]] = []
    bus = studio.app.state.bus
    ws = await studio.create_workspace("W")

    async def slow(_: Any) -> SemanticDraftOutput:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")

    original_publish = bus.publish

    def spy(workspace_id: str, event_type: str, data: Any = None):
        events.append((event_type, data))
        return original_publish(workspace_id, event_type, data)

    bus.publish = spy
    drafter.enricher = slow
    drafter.timeout_s = 0.05
    try:
        await studio.upload_sample(ws, "customers.csv")
        await drafter.wait_idle()
    finally:
        drafter.enricher = None
        bus.publish = original_publish

    model = await _semantic(studio, ws)
    assert model["draft_run"]["status"] == "llm_failed"
    assert "col:customers.customer_id" in _by_key(model)
    assert ("semantic.warning", {"run_id": model["draft_run"]["id"], "reason": "timeout"}) in events
