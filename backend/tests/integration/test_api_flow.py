"""Integration test alur REST API end-to-end (task 16.8).

upload CSV → progres → profil → kandidat relasi → konfirmasi → Dashboard →
patch → render; restart app memulihkan state; hapus Workspace membersihkan
SQLite dan folder; re-upload menandai insight ``stale``; render tanpa LLM.

_Requirements: 1.4, 5.3, 7.4, 17.5, 18.5, 22.3, 26.2, 28.4_
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import litellm
import pytest

from studio.app import create_app
from studio.config import Settings
from studio.store.db import DB_FILENAME
from tests.integration.helpers import (
    EXPECTED_FACTS,
    SAMPLES,
    SQL,
    Studio,
    bar_spec,
    dataset_rows,
    err,
    insight_draft,
    running,
)


def _forbid_llm(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    """Mock semua jalur pemanggilan model (LiteLLM & klien ADK); kembalikan mock-nya."""
    from google.adk.models.lite_llm import LiteLLMClient

    mocks = [AsyncMock(name="acompletion"), MagicMock(name="completion"), AsyncMock(name="adk")]
    monkeypatch.setattr(litellm, "acompletion", mocks[0])
    monkeypatch.setattr(litellm, "completion", mocks[1])
    monkeypatch.setattr(LiteLLMClient, "acompletion", mocks[2])
    return mocks


async def test_upload_profile_confirm_dashboard_render_flow(
    studio: Studio, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = _forbid_llm(monkeypatch)
    client = studio.client
    ws = await studio.create_workspace("Penjualan")

    # --- upload CSV → progres (Req 5.3) ---------------------------------------
    resp = await studio.upload(ws, "customers.csv", (SAMPLES / "customers.csv").read_bytes())
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = await studio.wait_job(job_id)
    assert job["status"] == "done" and job["progress"] == 100.0, job
    seen = studio.progress_seen[job_id]
    assert seen == sorted(seen) and all(0.0 <= p <= 100.0 for p in seen)
    customers_id = job["dataset_id"]

    # --- profil & kualitas data ------------------------------------------------
    resp = await client.get(f"/api/workspaces/{ws}/datasets/{customers_id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    facts = EXPECTED_FACTS["tables"]["customers"]
    assert detail["dataset"]["table_name"] == "customers"
    assert detail["dataset"]["row_count"] == facts["rows"]
    assert [c["name"] for c in detail["schema"]] == facts["columns"]
    profiles = {p["name"]: p for p in detail["column_profiles"]}
    assert list(profiles) == facts["columns"]
    email = EXPECTED_FACTS["data_quality"]["customers.email"]
    assert profiles["email"]["null_count"] == email["null_count"]
    assert profiles["email"]["null_pct"] == pytest.approx(email["null_pct"])
    assert profiles["customer_id"]["distinct_count"] == facts["rows"]
    assert detail["quality"]["duplicate_rows"] == 0

    # --- kandidat relasi → konfirmasi (Req 7.4) ---------------------------------
    await studio.upload_sample(ws, "transactions.csv")
    rel = await studio.relation_between(ws, "customers", "customer_id", "transactions", "candidate")
    assert rel["cardinality"] == "one_to_many"
    confirmed = await studio.confirm(ws, rel["id"])
    assert confirmed["status"] == "confirmed" and confirmed["decided_at"] is not None
    assert [r["id"] for r in await studio.relations(ws, "confirmed")] == [rel["id"]]

    # --- Dashboard → patch (source user, Req 17.5) → render (Req 18.5, 22.3) ------
    dash = await studio.create_dashboard(ws, "Ringkasan")
    assert dash["version"] == 0 and dash["content"]["items"] == {}
    query = await studio.execute(ws, SQL["revenue_by_region"])
    item_id, version = await studio.add_chart(
        dash["id"], 0, "Pendapatan per region", bar_spec(query.query_id, "region", "revenue")
    )
    assert version == 1

    snapshot = await studio.get_dashboard(dash["id"])
    assert snapshot["version"] == 1 and snapshot["can_undo"] is True
    assert snapshot["content"]["items"][item_id]["spec"]["query_id"] == query.query_id
    assert snapshot["item_status"][item_id] == {"invalid": False, "stale": False}

    patches = (await client.get(f"/api/dashboards/{dash['id']}/patches")).json()
    assert [p["source"] for p in patches["patches"]] == ["user"]

    rendered = await studio.render(dash["id"])
    assert rendered["version"] == 1
    out = rendered["items"][item_id]
    assert out["status"] == "ok" and out["filter_unaffected"] is False
    assert dataset_rows(out["option"]) == EXPECTED_FACTS["revenue_by_region"]
    # Desain chart tidak diubah; hanya dataset yang diikat.
    assert out["option"]["series"] == [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}]

    # Render (dengan filter) tidak pernah memanggil LLM (Req 22.3).
    await studio.render(
        dash["id"],
        cross_filters=[{"kind": "in", "table": "customers", "column": "region", "values": ["Jawa"]}],
    )
    for mock in llm:
        mock.assert_not_called()


async def test_restart_recovers_workspace_dashboard_and_versions(settings: Settings) -> None:
    """Metadata SQLite + Parquet bertahan antar restart (Req 28.4)."""
    app = create_app(settings)
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace("Persisten")
        await studio.upload_sample(ws, "customers.csv")
        query = await studio.execute(ws, SQL["customers_by_region"])
        dash = await studio.create_dashboard(ws, "Awal")
        item_id, v = await studio.add_chart(
            dash["id"], 0, "Pelanggan", bar_spec(query.query_id, "region", "customers")
        )
        await studio.apply_ok(dash["id"], {"type": "set_title", "title": "Akhir"}, v)
        before = await studio.get_dashboard(dash["id"])
        before_detail = (await client.get(f"/api/workspaces/{ws}")).json()

    app = create_app(settings)
    async with running(app) as client:
        studio = Studio(app, client)
        assert [w["id"] for w in (await client.get("/api/workspaces")).json()] == [ws]
        detail = (await client.get(f"/api/workspaces/{ws}")).json()
        assert detail == before_detail
        assert await studio.get_dashboard(dash["id"]) == before
        assert before["version"] == 2 and before["title"] == "Akhir"

        patches = (await client.get(f"/api/dashboards/{dash['id']}/patches?since_version=0")).json()
        assert [p["version"] for p in patches["patches"]] == [1, 2] and patches["version"] == 2

        # Riwayat undo dipulihkan.
        resp = await client.post(f"/api/dashboards/{dash['id']}/undo", json={"base_version": 2})
        assert resp.status_code == 200, resp.text
        assert (await studio.get_dashboard(dash["id"]))["title"] == "Awal"

        # Parquet tetap dapat dieksekusi setelah restart.
        rendered = await studio.render(dash["id"])
        assert dataset_rows(rendered["items"][item_id]["option"]) == EXPECTED_FACTS[
            "customers_by_region"
        ]


def _count(db_file: Path, table: str) -> int:
    with sqlite3.connect(db_file) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


@pytest.mark.skipif(
    bool(os.environ.get("TEST_DATABASE_URL")), reason="membaca file SQLite langsung"
)
async def test_delete_workspace_removes_metadata_and_files(
    studio: Studio, settings: Settings
) -> None:
    """``DELETE`` dengan ``confirm_name`` cocok menghapus metadata (cascade) dan folder (Req 1.4)."""
    client = studio.client
    keep = await studio.create_workspace("Tetap")
    ws = await studio.create_workspace("Hapus Saya")
    await studio.upload_sample(ws, "customers.csv")
    query = await studio.execute(ws, SQL["customers_by_region"])
    dash = await studio.create_dashboard(ws)
    await studio.add_chart(dash["id"], 0, "c", bar_spec(query.query_id, "region", "customers"))

    uploads = Path(settings.data_dir) / "uploads" / ws
    assert uploads.is_dir() and any(p.is_file() for p in uploads.rglob("*"))
    db_file = Path(settings.data_dir) / DB_FILENAME

    # Nama konfirmasi harus sama persis.
    for wrong in ("hapus saya", "Hapus Saya ", "Tetap"):
        resp = await client.request("DELETE", f"/api/workspaces/{ws}", json={"confirm_name": wrong})
        assert resp.status_code == 422, resp.text
        assert err(resp)["code"] == "VALIDATION_ERROR"
    assert (await client.get(f"/api/workspaces/{ws}")).status_code == 200

    resp = await client.request("DELETE", f"/api/workspaces/{ws}", json={"confirm_name": "Hapus Saya"})
    assert resp.status_code == 204, resp.text

    assert not uploads.exists()
    resp = await client.get(f"/api/workspaces/{ws}")
    assert resp.status_code == 404 and err(resp)["code"] == "NOT_FOUND"
    assert (await client.get(f"/api/dashboards/{dash['id']}")).status_code == 404
    assert (await client.get(f"/api/queries/{query.query_id}")).status_code == 404
    assert [w["id"] for w in (await client.get("/api/workspaces")).json()] == [keep]
    for table in (
        "uploads",
        "datasets",
        "dataset_profiles",
        "relations",
        "queries",
        "dashboards",
        "patch_events",
        "chat_sessions",
    ):
        assert _count(db_file, table) == 0, table
    assert _count(db_file, "workspaces") == 1

    resp = await client.request("DELETE", f"/api/workspaces/{ws}", json={"confirm_name": "Hapus Saya"})
    assert resp.status_code == 404


async def test_reupload_marks_insight_stale(studio: Studio) -> None:
    """Re-upload skema sama → ``data_version`` naik → Insight_Card ``stale`` (Req 26.2)."""
    client = studio.client
    ws = await studio.create_workspace()
    ds = await studio.upload_sample(ws, "customers.csv")
    sql = SQL["customers_by_region"]
    result = await studio.execute(ws, sql)
    dash = await studio.create_dashboard(ws)
    chart_id, v = await studio.add_chart(
        dash["id"], 0, "c", bar_spec(result.query_id, "region", "customers")
    )
    draft = insight_draft(
        result, sql, "Jawa memiliki 100 pelanggan.", [ds], {ds: 1}, title="Pelanggan Jawa"
    )
    insight_id, v = await studio.add_insight(dash["id"], v, draft)
    snapshot = await studio.get_dashboard(dash["id"])
    assert snapshot["item_status"][insight_id] == {"invalid": False, "stale": False}
    matched: list[dict[str, Any]] = snapshot["content"]["items"][insight_id]["matched_numbers"]
    assert [m["value"] for m in matched] == [100]

    # Hanya 150 baris pertama → angka berubah; skema identik.
    lines = (SAMPLES / "customers.csv").read_bytes().splitlines(keepends=True)
    resp = await client.post(
        f"/api/workspaces/{ws}/datasets/{ds}/reupload",
        files={"file": ("customers.csv", b"".join(lines[:151]), "text/csv")},
    )
    assert resp.status_code == 202, resp.text
    job = await studio.wait_job(resp.json()["job_id"])
    assert job["status"] == "done" and job["dataset_id"] == ds, job

    dataset = (await client.get(f"/api/workspaces/{ws}/datasets/{ds}")).json()["dataset"]
    assert dataset["data_version"] == 2 and dataset["row_count"] == 150

    snapshot = await studio.get_dashboard(dash["id"])
    assert snapshot["item_status"][insight_id] == {"invalid": False, "stale": True}
    assert snapshot["item_status"][chart_id] == {"invalid": False, "stale": False}
    rendered = await studio.render(dash["id"])
    assert rendered["items"][insight_id] == {"status": "stale", "filter_unaffected": False}
    chart = rendered["items"][chart_id]
    assert chart["status"] == "ok"
    assert sum(r["customers"] for r in dataset_rows(chart["option"])) == 150
