"""Probe B1 sekali jalan: ingestion seed + deteksi relasi lewat app in-process (tanpa LLM).

    backend\\.venv\\Scripts\\python.exe -m tests.e2e.seeds.probe_ingestion
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from studio.app import create_app
from studio.config import Settings

SEEDS = Path(__file__).resolve().parent
FILES = ["wilayah.csv", "toko.csv", "produk.xlsx", "pesanan.csv", "detail_pesanan.csv"]


async def wait_job(c: httpx.AsyncClient, job_id: str) -> dict:
    for _ in range(600):
        job = (await c.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in ("done", "succeeded", "completed", "failed", "error"):
            return job
        await asyncio.sleep(0.1)
    raise TimeoutError(job_id)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=Path(tmp), query_runner_mode="inline"))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                ws = (await c.post("/api/workspaces", json={"name": "probe"})).json()["id"]
                for name in FILES:
                    with open(SEEDS / name, "rb") as fh:
                        r = await c.post(f"/api/workspaces/{ws}/uploads", files={"file": (name, fh)})
                    body = r.json()
                    jobs = [body["job_id"]] if "job_id" in body else [
                        j["job_id"] for j in (await c.post(
                            f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets",
                            json={"sheets": [s["name"] for s in body["sheets"]]},
                        )).json()["jobs"]
                    ]
                    for j in jobs:
                        print(name, (await wait_job(c, j))["status"])
                for d in (await c.get(f"/api/workspaces/{ws}/datasets")).json():
                    detail = (await c.get(f"/api/workspaces/{ws}/datasets/{d['id']}")).json()

                    print(detail["dataset"]["table_name"], [(c["name"], c["type"]) for c in detail["schema"]] if isinstance(detail["schema"], list) else detail["schema"])

                rels = (await c.post(f"/api/workspaces/{ws}/relations/detect")).json()
                for r in rels:
                    print("REL", r["from_table"], r["from_column"], "->", r["to_table"], r["to_column"], r["cardinality"], r["overlap_pct"], r["status"])




async def probe_seed_dashboard() -> None:
    """Probe gate G4-G6: dashboard deterministik via REST tanpa LLM (sama dengan e2e/lib/workspace.ts)."""
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Settings(data_dir=Path(tmp), query_runner_mode="inline"))
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                ws = (await c.post("/api/workspaces", json={"name": "probe"})).json()["id"]
                for name in FILES:
                    with open(SEEDS / name, "rb") as fh:
                        body = (await c.post(f"/api/workspaces/{ws}/uploads", files={"file": (name, fh)})).json()
                    jobs = [body["job_id"]] if "job_id" in body else [
                        j["job_id"] for j in (await c.post(
                            f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets",
                            json={"sheets": [s["name"] for s in body["sheets"]]},
                        )).json()["jobs"]
                    ]
                    for j in jobs:
                        await wait_job(c, j)
                for r in (await c.post(f"/api/workspaces/{ws}/relations/detect")).json():
                    await c.post(f"/api/workspaces/{ws}/relations/{r['id']}/confirm")
                sql = ("SELECT w.nama_wilayah, SUM(d.pendapatan) AS pendapatan FROM detail_pesanan d "
                       "JOIN pesanan p ON d.no_pesanan = p.no_pesanan JOIN toko t ON p.kode_toko = t.kode_toko "
                       "JOIN wilayah w ON t.kode_wilayah = w.kode_wilayah WHERE p.status = 'selesai' "
                       "GROUP BY w.nama_wilayah ORDER BY pendapatan DESC")
                q = await c.post(f"/api/workspaces/{ws}/queries", json={"sql": sql})
                print("QUERY", q.status_code, q.text[:200])
                dash = (await c.post(f"/api/workspaces/{ws}/dashboards", json={"title": "Overview"})).json()
                spec = {"query_id": q.json()["query_id"], "chart_type": "bar", "cross_filter_column": "nama_wilayah",
                        "option": {"xAxis": {"type": "category"}, "yAxis": {"type": "value"},
                                   "series": [{"type": "bar", "encode": {"x": "nama_wilayah", "y": "pendapatan"}}]}}
                r = await c.post(f"/api/dashboards/{dash['id']}/patches", json={"base_version": dash["version"], "command": {
                    "type": "add_chart", "title": "Pendapatan per Wilayah", "spec": spec, "layout": {"x": 0, "y": 0, "w": 6, "h": 8}}})
                print("PATCH", r.status_code, r.text[:300])
                print("SNAP", (await c.get(f"/api/dashboards/{dash['id']}")).json()["item_status"])
                print("QUERIES", [x["tables_used"] for x in (await c.get(f"/api/workspaces/{ws}/queries")).json()])


if __name__ == "__main__":
    asyncio.run(main())
    asyncio.run(probe_seed_dashboard())
