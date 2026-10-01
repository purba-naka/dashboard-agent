"""Feature: dashboard-studio-agent, Property 8: CSV rusak ditolak dengan nomor baris yang tepat.

Untuk CSV valid dan posisi baris data ``k`` (1-based, setelah header) yang
diganti dengan record berjumlah field berbeda (field tambahan atau field yang
hilang), parsing SHALL gagal dengan ``ParseError`` (``PARSE_ERROR``, cause
``column_count``) yang menyebut nomor baris ``k + 1`` (1-based termasuk
header); tidak ada Dataset terdaftar dan tidak ada file parsial/sisa di
folder workspace.

Generator: nilai sel non-kosong (termasuk nilai ber-koma yang di-quote agar
jumlah field dihitung per record, bukan per koma), tanpa newline di dalam
nilai sehingga nomor record = nomor baris fisik; akhir baris ``\\n`` atau
``\\r\\n``, dengan/tanpa newline penutup.

**Validates: Requirements 2.3, 5.5**
"""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.data.csv_reader import ParseError, convert_csv_to_parquet, locate_bad_row
from studio.data.ingestion import IngestionService
from studio.data.parquet_writer import partial_path_for
from studio.events.bus import EventBus
from studio.store.db import Database
from studio.store.repos import Repositories

_HEADER = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
_CELL = st.from_regex(r"[A-Za-z0-9]{1,6}", fullmatch=True) | st.from_regex(
    r"[A-Za-z0-9 ]{0,3},[A-Za-z0-9 ]{0,3}", fullmatch=True  # di-quote oleh csv.writer
)


@dataclass(frozen=True)
class CorruptCsv:
    """CSV dengan tepat satu record rusak pada baris data ``bad_row`` (1-based)."""

    text: str
    n_cols: int
    bad_row: int
    bad_fields: int

    @property
    def bad_line(self) -> int:
        return self.bad_row + 1  # + baris header


@st.composite
def corrupt_csvs(draw: st.DrawFn, max_rows: int = 30) -> CorruptCsv:
    n_cols = draw(st.integers(1, 6))
    header = draw(st.lists(_HEADER, min_size=n_cols, max_size=n_cols, unique=True))
    n_rows = draw(st.integers(1, max_rows))
    rows = [draw(st.lists(_CELL, min_size=n_cols, max_size=n_cols)) for _ in range(n_rows)]

    bad_row = draw(st.integers(1, n_rows), label="bad_row")
    row = rows[bad_row - 1]
    # Field hilang hanya mungkin bila > 1 kolom (0 field = baris kosong, bukan record).
    if n_cols > 1 and draw(st.booleans(), label="missing"):
        rows[bad_row - 1] = row[: draw(st.integers(1, n_cols - 1))]
    else:
        rows[bad_row - 1] = row + draw(st.lists(_CELL, min_size=1, max_size=3))

    terminator = draw(st.sampled_from(["\n", "\r\n"]))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator=terminator)
    writer.writerow(header)
    writer.writerows(rows)
    text = buf.getvalue()
    if not draw(st.booleans(), label="trailing_newline"):
        text = text[: -len(terminator)]
    return CorruptCsv(text, n_cols, bad_row, len(rows[bad_row - 1]))


_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)


@_SETTINGS
@given(corrupt_csvs())
def test_bad_row_rejected_with_exact_line(tmp_path: Path, case: CorruptCsv) -> None:
    """Feature: dashboard-studio-agent, Property 8: CSV rusak ditolak dengan nomor baris yang tepat.

    **Validates: Requirements 2.3, 5.5**
    """
    work = tmp_path / uuid4().hex
    work.mkdir()
    src, dst = work / "data.csv", work / "data.parquet"
    src.write_bytes(case.text.encode("utf-8"))

    assert locate_bad_row(src) == case.bad_line

    with pytest.raises(ParseError) as info:
        convert_csv_to_parquet(src, dst)
    err = info.value
    assert err.code == "PARSE_ERROR" and err.http_status == 422
    assert err.cause == "column_count"
    assert err.line == case.bad_line
    assert err.details["line"] == case.bad_line
    assert err.details["expected_fields"] == case.n_cols
    assert err.details["found_fields"] == case.bad_fields
    assert f"baris {case.bad_line}" in err.message

    # Tidak ada file Parquet final maupun parsial.
    assert not dst.exists()
    assert not partial_path_for(dst).exists()
    assert sorted(p.name for p in work.iterdir()) == ["data.csv"]


async def _ingest(root: Path, csv_bytes: bytes) -> dict[str, Any]:
    db = await Database(root / "studio.db").open()
    try:
        repos = Repositories(db)
        svc = IngestionService(repos, EventBus(), root / "data")
        try:
            ws = await repos.workspaces.create("Rusak")
            upload = await svc.save_upload(ws.id, "rusak.csv", csv_bytes)
            job_id = await svc.start_csv_job(ws.id, upload)
            status = await svc.wait_job(job_id, timeout=60)
            ws_dir = svc.workspace_dir(ws.id)
            return {
                "status": status,
                "datasets": await repos.datasets.list_by_workspace(ws.id),
                "dataset_rows": await db.fetch_value("SELECT COUNT(*) FROM datasets"),
                "upload": await repos.uploads.get_or_none(upload.id),
                "files": sorted(str(p) for p in ws_dir.rglob("*")) if ws_dir.exists() else [],
            }
        finally:
            await svc.aclose()
    finally:
        await db.close()


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=20
)
@given(corrupt_csvs(max_rows=10))
def test_ingestion_rejects_bad_row_without_leftovers(tmp_path: Path, case: CorruptCsv) -> None:
    """Feature: dashboard-studio-agent, Property 8: CSV rusak ditolak (tanpa Dataset & sisa file).

    **Validates: Requirements 2.3, 5.5**
    """
    root = tmp_path / uuid4().hex
    root.mkdir()
    result = asyncio.run(_ingest(root, case.text.encode("utf-8")))

    status = result["status"]
    assert status["status"] == "failed", status
    error = status["error"]
    assert error["code"] == "PARSE_ERROR"
    assert error["details"]["cause"] == "column_count"
    assert error["details"]["line"] == case.bad_line

    assert result["datasets"] == []
    assert result["dataset_rows"] == 0
    assert result["upload"] is None
    assert result["files"] == []
