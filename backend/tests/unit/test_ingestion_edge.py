"""Unit test edge case Ingestion_Service (task 13.9).

Mencakup: encoding non-UTF-8, file kosong, hanya header, sheet kosong, XLSX
rusak/terenkripsi, ekstensi tidak didukung, re-upload skema berbeda yang
mempertahankan Dataset lama, progres monoton hingga 100, path traversal nama
file, pemetaan header duplikat, dan upload terputus tanpa sisa file.

Requirements: 2.3, 2.4, 2.5, 3.3, 3.4, 5.3, 5.5, 26.3, 29.5
"""

from __future__ import annotations

import io
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import polars as pl
import pytest
import xlsxwriter

from studio.core.identifiers import UnsupportedFormat
from studio.data.ingestion import (
    IngestionService,
    SchemaMismatch,
    UploadInterrupted,
    UploadTooLarge,
)
from studio.data.xlsx_reader import SheetNotFound, UnreadableWorkbook
from studio.events.bus import Event, EventBus, Subscription

from studio.store.repos import Repositories

JOB_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def repos(metadata_db) -> AsyncIterator[Repositories]:
    yield Repositories(metadata_db)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def svc(repos: Repositories, bus: EventBus, tmp_data_dir: Path) -> AsyncIterator[IngestionService]:
    service = IngestionService(repos, bus, tmp_data_dir, heartbeat_interval=0.05, batch_size=50)
    try:
        yield service
    finally:
        await service.aclose()


@pytest.fixture
async def ws_id(repos: Repositories) -> str:
    return (await repos.workspaces.create("Edge")).id


def _files(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    return sorted(p.name for p in directory.iterdir())


def _xlsx(sheets: dict[str, list[list[Any]]]) -> bytes:
    """Bangun workbook XLSX di memori; baris pertama tiap sheet = header."""
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    for name, rows in sheets.items():
        ws = wb.add_worksheet(name)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                ws.write(r, c, value)
    wb.close()
    return buf.getvalue()


async def _run_csv(svc: IngestionService, ws_id: str, content: bytes, name: str = "data.csv") -> dict[str, Any]:
    upload = await svc.save_upload(ws_id, name, content)
    job_id = await svc.start_csv_job(ws_id, upload)
    return await svc.wait_job(job_id, timeout=JOB_TIMEOUT)


async def _new_csv_dataset(svc: IngestionService, repos: Repositories, ws_id: str, content: bytes):
    status = await _run_csv(svc, ws_id, content)
    assert status["status"] == "done", status
    return await repos.datasets.get(status["dataset_id"])


# ---------------------------------------------------------------------------
# CSV tidak dapat di-parse (Req 2.3, 5.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "cause", "line"),
    [
        pytest.param("nama,kota\nJosé,Bandung\n".encode("latin-1"), "encoding", 2, id="latin1"),
        pytest.param(b"", "empty", None, id="empty-file"),
        pytest.param(b"a,b,c\n", "empty", None, id="header-only"),
    ],
)
async def test_unparseable_csv_fails_without_dataset_or_leftovers(
    svc: IngestionService,
    repos: Repositories,
    ws_id: str,
    content: bytes,
    cause: str,
    line: int | None,
) -> None:
    status = await _run_csv(svc, ws_id, content)

    assert status["status"] == "failed"
    error = status["error"]
    assert error["code"] == "PARSE_ERROR"
    assert error["details"]["cause"] == cause
    assert error["details"]["line"] == line
    assert error["message"]
    # Tidak ada Dataset, baris upload, file asli, maupun file staging/parsial.
    assert await repos.datasets.list_by_workspace(ws_id) == []
    assert await repos.uploads.list_by_workspace(ws_id) == []
    assert _files(svc.workspace_dir(ws_id)) == []


# ---------------------------------------------------------------------------
# Ekstensi tidak didukung (Req 2.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["data.json", "laporan.xls", "tanpa_ekstensi", "data.csv.exe"])
async def test_unsupported_extension_rejected_before_writing(
    svc: IngestionService, repos: Repositories, ws_id: str, filename: str
) -> None:
    with pytest.raises(UnsupportedFormat) as info:
        await svc.save_upload(ws_id, filename, b"a,b\n1,2\n")

    err = info.value
    assert err.code == "UNSUPPORTED_FORMAT"
    assert err.http_status == 415
    assert set(err.details["supported"]) == {".csv", ".xlsx"}
    assert ".csv" in err.message and ".xlsx" in err.message
    assert await repos.uploads.list_by_workspace(ws_id) == []
    assert _files(svc.workspace_dir(ws_id)) == []


# ---------------------------------------------------------------------------
# Header duplikat / kosong (Req 2.5)
# ---------------------------------------------------------------------------


async def test_duplicate_and_blank_headers_are_normalized_with_mapping(
    svc: IngestionService, repos: Repositories, ws_id: str
) -> None:
    dataset = await _new_csv_dataset(svc, repos, ws_id, b"a,a, ,b\n1,2,3,4\n5,6,7,8\n")

    expected = ["a", "a_2", "column_3", "b"]
    assert [c.name for c in dataset.schema] == expected
    assert [(m.original, m.normalized) for m in dataset.column_mapping] == [
        ("a", "a"),
        ("a", "a_2"),
        (" ", "column_3"),
        ("b", "b"),
    ]
    df = pl.read_parquet(svc.resolve_parquet_path(dataset))
    assert df.columns == expected
    assert df.height == 2
    assert df["a_2"].to_list() == [2, 6]


# ---------------------------------------------------------------------------
# Path traversal (Req 29.5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    ["../../evil.csv", "..\\..\\evil.csv", "/etc/evil.csv", "C:\\Windows\\evil.csv", "sub/../../evil.csv"],
)
async def test_path_components_in_filename_stay_in_workspace_dir(
    svc: IngestionService, ws_id: str, tmp_data_dir: Path, filename: str
) -> None:
    upload = await svc.save_upload(ws_id, filename, b"x\n1\n")

    ws_dir = svc.workspace_dir(ws_id)
    stored = svc.upload_path(upload)
    assert stored.parent == ws_dir
    assert stored.is_file()
    assert stored.name.endswith(".csv")
    assert "/" not in upload.stored_path and "\\" not in upload.stored_path
    assert ".." not in Path(upload.stored_path).parts
    assert upload.original_name == "evil.csv"
    # Tidak ada file yang lolos ke luar folder workspace.
    outside = [p for p in tmp_data_dir.parent.rglob("evil*") if ws_dir not in p.parents]
    assert outside == []


# ---------------------------------------------------------------------------
# Upload terputus / terlalu besar (Req 5.5)
# ---------------------------------------------------------------------------


async def test_interrupted_upload_leaves_no_file(
    svc: IngestionService, repos: Repositories, ws_id: str
) -> None:
    async def broken_body() -> AsyncIterator[bytes]:
        yield b"a,b\n" + b"1,2\n" * 1000
        raise ConnectionResetError("client disconnected")

    with pytest.raises(UploadInterrupted) as info:
        await svc.save_upload(ws_id, "putus.csv", broken_body())

    assert info.value.code == "UPLOAD_INTERRUPTED"
    assert info.value.details["filename"] == "putus.csv"
    assert await repos.uploads.list_by_workspace(ws_id) == []
    assert _files(svc.workspace_dir(ws_id)) == []


async def test_oversized_upload_leaves_no_file(
    repos: Repositories, bus: EventBus, tmp_data_dir: Path, ws_id: str
) -> None:
    svc = IngestionService(repos, bus, tmp_data_dir, max_upload_bytes=100, chunk_bytes=32)
    with pytest.raises(UploadTooLarge):
        await svc.save_upload(ws_id, "besar.csv", b"a\n" + b"1\n" * 200)

    assert await repos.uploads.list_by_workspace(ws_id) == []
    assert _files(svc.workspace_dir(ws_id)) == []


# ---------------------------------------------------------------------------
# XLSX: sheet kosong, rusak, terenkripsi (Req 3.3, 3.4)
# ---------------------------------------------------------------------------


async def test_empty_sheet_rejected_with_sheet_name(
    svc: IngestionService, repos: Repositories, ws_id: str
) -> None:
    content = _xlsx(
        {
            "Data": [["id", "nilai"], [1, 10], [2, 20]],
            "HanyaHeader": [["id", "nilai"]],
            "Kosong": [],
        }
    )
    upload = await svc.save_upload(ws_id, "buku.xlsx", content)
    jobs = dict(await svc.start_sheet_jobs(ws_id, upload, ["Data", "HanyaHeader", "Kosong"]))
    statuses = {s: await svc.wait_job(j, timeout=JOB_TIMEOUT) for s, j in jobs.items()}

    assert statuses["Data"]["status"] == "done"
    for sheet in ("HanyaHeader", "Kosong"):
        status = statuses[sheet]
        assert status["status"] == "failed", status
        assert status["error"]["code"] == "EMPTY_SHEET"
        assert status["error"]["details"]["sheet"] == sheet
        assert sheet in status["error"]["message"]

    datasets = await repos.datasets.list_by_workspace(ws_id)
    assert [d.sheet_name for d in datasets] == ["Data"]
    # Hanya file asli + Parquet sheet "Data"; tidak ada sisa staging.
    files = _files(svc.workspace_dir(ws_id))
    assert len(files) == 2
    assert not any("staging" in f or f.endswith(".partial") for f in files)


async def test_unknown_sheet_rejected(svc: IngestionService, ws_id: str) -> None:
    upload = await svc.save_upload(ws_id, "buku.xlsx", _xlsx({"Data": [["a"], [1]]}))
    with pytest.raises(SheetNotFound) as info:
        await svc.start_sheet_jobs(ws_id, upload, ["TidakAda"])
    assert info.value.code == "SHEET_NOT_FOUND"
    assert info.value.details["available"] == ["Data"]


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"ini bukan file excel sama sekali", id="garbage"),
        pytest.param(_xlsx({"Data": [["a"], [1]]})[:200], id="truncated-zip"),
        # Workbook OOXML terenkripsi disimpan sebagai container OLE/CFB, bukan zip.
        pytest.param(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 1024, id="encrypted-cfb"),
    ],
)
async def test_corrupt_or_encrypted_xlsx_is_unreadable(
    svc: IngestionService, repos: Repositories, ws_id: str, content: bytes
) -> None:
    upload = await svc.save_upload(ws_id, "rusak.xlsx", content)

    with pytest.raises(UnreadableWorkbook) as info:
        await svc.list_upload_sheets(upload)

    err = info.value
    assert err.code == "UNREADABLE_WORKBOOK"
    assert err.details["filename"] == "rusak.xlsx"
    assert "rusak.xlsx" in err.message
    # Upload yang tak terbaca dibuang (baris + file).
    assert await repos.uploads.list_by_workspace(ws_id) == []
    assert _files(svc.workspace_dir(ws_id)) == []


# ---------------------------------------------------------------------------
# Re-upload skema berbeda mempertahankan Dataset lama (Req 26.3)
# ---------------------------------------------------------------------------


async def test_reupload_with_changed_columns_keeps_old_dataset(
    svc: IngestionService, repos: Repositories, ws_id: str
) -> None:
    old = await _new_csv_dataset(svc, repos, ws_id, b"id,amount,region\n1,10,A\n2,20,B\n")
    parquet = svc.resolve_parquet_path(old)
    before_bytes = parquet.read_bytes()
    files_before = _files(svc.workspace_dir(ws_id))

    upload = await svc.save_upload(ws_id, "baru.csv", b"id,total,region,extra\n1,5,A,x\n")
    with pytest.raises(SchemaMismatch) as info:
        await svc.start_reupload(ws_id, old.id, upload)

    err = info.value
    assert err.code == "SCHEMA_MISMATCH"
    assert err.http_status == 409
    assert list(err.diff.missing) == ["amount"]
    assert sorted(err.diff.added) == ["extra", "total"]

    current = await repos.datasets.get(old.id)
    assert current.data_version == old.data_version
    assert current.schema == old.schema
    assert current.row_count == 2
    assert parquet.read_bytes() == before_bytes
    # File upload baru dibuang; file Dataset lama tetap.
    assert _files(svc.workspace_dir(ws_id)) == files_before


async def test_reupload_with_changed_type_fails_job_and_keeps_old_dataset(
    svc: IngestionService, repos: Repositories, bus: EventBus, ws_id: str
) -> None:
    old = await _new_csv_dataset(svc, repos, ws_id, b"id,amount\n1,10\n2,20\n3,30\n")
    parquet = svc.resolve_parquet_path(old)
    before_bytes = parquet.read_bytes()
    files_before = _files(svc.workspace_dir(ws_id))

    sub = bus.subscribe(ws_id)
    upload = await svc.save_upload(ws_id, "baru.csv", b"id,amount\n1,sepuluh\n2,dua puluh\n")
    job_id = await svc.start_reupload(ws_id, old.id, upload)
    status = await svc.wait_job(job_id, timeout=JOB_TIMEOUT)

    assert status["status"] == "failed"
    assert status["error"]["code"] == "SCHEMA_MISMATCH"
    changed = status["error"]["details"]["changed"]
    assert [c["name"] for c in changed] == ["amount"]

    failed = [e.data for e in await _drain(sub) if e.type == "job.failed"]
    assert failed and failed[-1]["job_id"] == job_id
    assert failed[-1]["error"]["code"] == "SCHEMA_MISMATCH"

    current = await repos.datasets.get(old.id)
    assert current.data_version == old.data_version
    assert [c.type for c in current.schema] == ["integer", "integer"]
    assert current.row_count == 3
    assert parquet.read_bytes() == before_bytes
    assert _files(svc.workspace_dir(ws_id)) == files_before
    sub.close()


# ---------------------------------------------------------------------------
# Progres monoton dan mencapai 100 (Req 5.3)
# ---------------------------------------------------------------------------


async def _drain(sub: Subscription) -> list[Event]:
    """Ambil semua event yang sudah diterbitkan (termasuk yang dijadwalkan dari
    thread job via ``call_soon_threadsafe``) sampai antrean diam."""
    events: list[Event] = []
    while (event := await sub.next(timeout=0.1)) is not None:
        events.append(event)
    return events


async def test_progress_is_monotonic_and_reaches_100(
    svc: IngestionService, bus: EventBus, ws_id: str
) -> None:
    rows = "".join(f"{i},{i * 1.5},kota_{i % 7}\n" for i in range(2000))
    content = f"id,nilai,kota\n{rows}".encode()

    sub = bus.subscribe(ws_id)
    upload = await svc.save_upload(ws_id, "besar.csv", content)
    job_id = await svc.start_csv_job(ws_id, upload)
    status = await svc.wait_job(job_id, timeout=JOB_TIMEOUT)
    assert status == {"status": "done", "progress": 100.0, "dataset_id": status["dataset_id"]}

    events = [e for e in await _drain(sub) if e.data.get("job_id") == job_id]
    sub.close()
    progress = [e.data["progress"] for e in events if e.type == "job.progress"]

    assert len(progress) >= 3  # awal, beberapa batch, final
    assert all(0 <= p <= 100 for p in progress)
    assert progress == sorted(progress), progress
    assert progress[-1] == 100
    assert all(p < 100 for p in progress[:-1])
    # job.done diterbitkan setelah progres 100, sekali, dengan dataset_id.
    types = [e.type for e in events]
    assert types.count("job.done") == 1
    assert types.index("job.done") > max(i for i, t in enumerate(types) if t == "job.progress")
    assert "job.failed" not in types


async def test_failed_job_progress_stays_below_100(
    svc: IngestionService, bus: EventBus, ws_id: str
) -> None:
    sub = bus.subscribe(ws_id)
    status = await _run_csv(svc, ws_id, b"a,b\n1,2\n3\n")
    events = await _drain(sub)
    sub.close()

    assert status["status"] == "failed"
    assert status["error"]["code"] == "PARSE_ERROR"
    assert status["error"]["details"]["cause"] == "column_count"
    assert status["error"]["details"]["line"] == 3
    progress = [e.data["progress"] for e in events if e.type == "job.progress"]
    assert progress == sorted(progress)
    assert all(p < 100 for p in progress)
    assert [e.type for e in events].count("job.failed") == 1
    assert "job.done" not in [e.type for e in events]
