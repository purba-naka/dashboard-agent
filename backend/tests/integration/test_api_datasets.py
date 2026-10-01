"""Integration test endpoint upload, job, Dataset, dan re-upload (task 16.8).

_Requirements: 2.1, 2.4, 2.5, 3.2, 3.3, 5.3, 6.2, 26.1, 26.3, 27.4_
"""

from __future__ import annotations

import io
from pathlib import Path

import xlsxwriter

from studio.config import Settings
from tests.integration.helpers import EXPECTED_FACTS, Studio, err

CSV = b"id,name,amount\n1,alpha,10.5\n2,beta,20\n3,gamma,30.25\n"


def _workbook() -> bytes:
    """Workbook dua sheet: ``Orders`` (berisi data) dan ``Kosong`` (tanpa baris)."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    orders = wb.add_worksheet("Orders")
    for r, row in enumerate([["order_id", "qty"], [1, 5], [2, 7], [3, 9]]):
        orders.write_row(r, 0, row)
    wb.add_worksheet("Kosong")
    wb.close()
    return buf.getvalue()


async def test_csv_upload_job_and_dataset_detail(studio: Studio) -> None:
    client = studio.client
    ws = await studio.create_workspace()
    resp = await studio.upload(ws, "Orders 2024.csv", CSV)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert set(body) == {"upload_id", "job_id"}
    job = await studio.wait_job(body["job_id"])
    assert job["status"] == "done" and "error" not in job, job
    ds = job["dataset_id"]

    datasets = (await client.get(f"/api/workspaces/{ws}/datasets")).json()
    assert [d["id"] for d in datasets] == [ds]
    assert "parquet_path" not in datasets[0]

    detail = (await client.get(f"/api/workspaces/{ws}/datasets/{ds}")).json()
    dataset = detail["dataset"]
    assert dataset["upload_id"] == body["upload_id"]
    assert dataset["source_name"] == "Orders 2024.csv" and dataset["sheet_name"] is None
    assert dataset["row_count"] == 3 and dataset["data_version"] == 1
    assert detail["schema"] == [
        {"name": "id", "type": "integer"},
        {"name": "name", "type": "string"},
        {"name": "amount", "type": "float"},
    ]
    assert [m["normalized"] for m in detail["column_mapping"]] == ["id", "name", "amount"]
    amount = next(p for p in detail["column_profiles"] if p["name"] == "amount")
    assert amount["role"] == "measure" and amount["min"] == 10.5 and amount["max"] == 30.25

    # Workspace detail memuat Dataset.
    ws_detail = (await client.get(f"/api/workspaces/{ws}")).json()
    assert [d["id"] for d in ws_detail["datasets"]] == [ds]

    # 404: job/Dataset tidak dikenal atau di Workspace lain.
    other = await studio.create_workspace("Lain")
    assert (await client.get("/api/jobs/tidak-ada")).status_code == 404
    resp = await client.get(f"/api/workspaces/{other}/datasets/{ds}")
    assert resp.status_code == 404 and err(resp)["code"] == "NOT_FOUND"


async def test_privacy_toggle(studio: Studio) -> None:
    """``privacy_no_samples`` dapat diaktifkan & dinonaktifkan (Req 27.4)."""
    client = studio.client
    ws = await studio.create_workspace()
    ds = await studio.upload_csv(ws, "orders.csv", CSV)
    url = f"/api/workspaces/{ws}/datasets/{ds}"
    assert (await client.get(url)).json()["dataset"]["privacy_no_samples"] is False

    resp = await client.patch(url, json={"privacy_no_samples": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["privacy_no_samples"] is True
    assert (await client.get(url)).json()["dataset"]["privacy_no_samples"] is True

    # Body kosong tidak mengubah apa pun; field tak dikenal / tipe salah → 422.
    assert (await client.patch(url, json={})).json()["privacy_no_samples"] is True
    for bad in ({"privacy_no_samples": "mungkin"}, {"unknown": 1}):
        resp = await client.patch(url, json=bad)
        assert resp.status_code == 422 and err(resp)["code"] == "VALIDATION_ERROR"

    resp = await client.patch(url, json={"privacy_no_samples": False})
    assert resp.json()["privacy_no_samples"] is False


async def test_xlsx_upload_lists_sheets_and_converts_selected(studio: Studio) -> None:
    """XLSX → daftar sheet (200); satu job per sheet terpilih; sheet kosong gagal (Req 3.2, 3.3)."""
    client = studio.client
    ws = await studio.create_workspace()
    body = await studio.upload_xlsx_sheets(ws, "book.xlsx", _workbook())
    assert [s["name"] for s in body["sheets"]] == ["Orders", "Kosong"]
    assert (await client.get(f"/api/workspaces/{ws}/datasets")).json() == []

    resp = await client.post(
        f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets",
        json={"sheets": ["Orders", "Kosong"]},
    )
    assert resp.status_code == 202, resp.text
    jobs = {j["sheet"]: j["job_id"] for j in resp.json()["jobs"]}
    assert set(jobs) == {"Orders", "Kosong"} and len(set(jobs.values())) == 2

    done = await studio.wait_job(jobs["Orders"])
    assert done["status"] == "done", done
    failed = await studio.wait_job(jobs["Kosong"])
    assert failed["status"] == "failed" and failed["error"]["code"], failed
    assert "dataset_id" not in failed

    (dataset,) = (await client.get(f"/api/workspaces/{ws}/datasets")).json()
    assert dataset["id"] == done["dataset_id"]
    assert dataset["sheet_name"] == "Orders" and dataset["row_count"] == 3

    # Sheet yang tidak ada / daftar kosong ditolak.
    resp = await client.post(
        f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets", json={"sheets": []}
    )
    assert resp.status_code == 422


async def test_sample_products_xlsx(studio: Studio) -> None:
    ws = await studio.create_workspace()
    ds = await studio.upload_products(ws)
    detail = (await studio.client.get(f"/api/workspaces/{ws}/datasets/{ds}")).json()
    facts = EXPECTED_FACTS["tables"]["products"]
    assert detail["dataset"]["table_name"] == "products"
    assert detail["dataset"]["row_count"] == facts["rows"]
    assert [c["name"] for c in detail["schema"]] == facts["columns"]


async def test_unsupported_extension_rejected_415(studio: Studio, settings: Settings) -> None:
    ws = await studio.create_workspace()
    resp = await studio.upload(ws, "notes.txt", b"hello", "text/plain")
    assert resp.status_code == 415, resp.text
    error = err(resp)
    assert error["code"] == "UNSUPPORTED_FORMAT"
    assert (await studio.client.get(f"/api/workspaces/{ws}/datasets")).json() == []
    uploads = Path(settings.data_dir) / "uploads" / ws
    assert not uploads.exists() or not any(p.is_file() for p in uploads.rglob("*"))


async def test_reupload_schema_mismatch_409_then_success(studio: Studio) -> None:
    """Kolom hilang/bertambah → 409 ``SCHEMA_MISMATCH``; skema cocok → 202 dan data diganti."""
    client = studio.client
    ws = await studio.create_workspace()
    ds = await studio.upload_csv(ws, "orders.csv", CSV)
    url = f"/api/workspaces/{ws}/datasets/{ds}/reupload"

    resp = await client.post(
        url, files={"file": ("orders.csv", b"id,name,total\n1,a,2\n", "text/csv")}
    )
    assert resp.status_code == 409, resp.text
    error = err(resp)
    assert error["code"] == "SCHEMA_MISMATCH"
    assert error["details"]["missing"] == ["amount"]
    assert error["details"]["added"] == ["total"]

    # Dataset tidak berubah.
    dataset = (await client.get(f"/api/workspaces/{ws}/datasets/{ds}")).json()["dataset"]
    assert dataset["data_version"] == 1 and dataset["row_count"] == 3

    # Format tidak didukung dan Dataset tidak dikenal.
    resp = await client.post(url, files={"file": ("orders.txt", b"x", "text/plain")})
    assert resp.status_code == 415
    resp = await client.post(
        f"/api/workspaces/{ws}/datasets/tidak-ada/reupload",
        files={"file": ("orders.csv", CSV, "text/csv")},
    )
    assert resp.status_code == 404

    resp = await client.post(
        url, files={"file": ("orders.csv", CSV + b"4,delta,40\n", "text/csv")}
    )
    assert resp.status_code == 202, resp.text
    job = await studio.wait_job(resp.json()["job_id"])
    assert job == {"status": "done", "progress": 100.0, "dataset_id": ds}
    dataset = (await client.get(f"/api/workspaces/{ws}/datasets/{ds}")).json()["dataset"]
    assert dataset["data_version"] == 2 and dataset["row_count"] == 4
