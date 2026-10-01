"""Feature: dashboard-studio-agent, Property 5: Round-trip CSV → Parquet.

Untuk tabel valid yang dapat direpresentasikan dalam CSV (kolom integer, float,
string, boolean, date, datetime[us]; sel boleh null), menulis tabel ke CSV
dengan ``polars.DataFrame.write_csv``, mengonversinya dengan
``convert_csv_to_parquet`` (dan lewat ``IngestionService`` end-to-end), lalu
membaca Parquet hasilnya SHALL menghasilkan nama kolom, tipe logis, jumlah
baris (= ``row_count`` yang dikembalikan/tersimpan), dan nilai sel yang
ekuivalen dengan tabel asli.

Generator ``csv_safe_tables()`` sengaja TIDAK membangkitkan input yang
ambigu dalam CSV (bukan kekurangan pipeline, melainkan informasi yang hilang
di format teks):

- String yang dapat di-parse sebagai tipe lain: alfabet string tanpa digit,
  wajib berisi minimal satu huruf, dan bukan ``true``/``false``/``nan``/
  ``inf``/``infinity`` (tanpa memandang huruf besar-kecil).
- Ambiguitas string kosong vs null: string minimal 1 karakter (sel kosong =
  null).
- Whitespace di awal/akhir string, serta ``\\r`` (normalisasi akhir baris).
  Koma, tanda kutip, dan newline di dalam nilai tetap dibangkitkan (di-quote).
- Presisi float: NaN/inf tidak dibangkitkan; float berhingga ditulis Polars
  dengan representasi round-trip terpendek sehingga dibandingkan eksak.
- Datetime dengan zona waktu (datetime naive saja, presisi mikrodetik).
- Kolom yang seluruh nilainya null tidak punya tipe di CSV; nilai harapannya
  kolom ``string`` berisi null (sesuai inferensi yang terdokumentasi).
- Header yang belum ternormalisasi (normalisasi header diuji terpisah).

**Validates: Requirements 2.2, 4.2, 4.3**
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from polars.testing import assert_frame_equal

from studio.core.models import LogicalType
from studio.data.csv_reader import TARGET_DTYPES, convert_csv_to_parquet
from studio.data.ingestion import IngestionService
from studio.data.parquet_writer import partial_path_for, read_parquet
from studio.events.bus import EventBus
from studio.store.db import Database
from studio.store.repos import Repositories

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

#: String yang akan diinferensi Polars sebagai boolean/float.
_RESERVED_STRINGS = frozenset({"true", "false", "nan", "inf", "infinity"})

_STRING_ALPHABET = st.sampled_from(
    list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
    + list("éüßñçøå中文日本")
    + [" ", ",", '"', "'", "-", ".", "!", "\n"]
)


def _csv_safe_string(s: str) -> bool:
    return (
        s == s.strip()
        and any(c.isalpha() for c in s)
        and s.lower() not in _RESERVED_STRINGS
    )


_VALUES: dict[LogicalType, st.SearchStrategy[Any]] = {
    "integer": st.integers(_INT64_MIN, _INT64_MAX),
    "float": st.floats(allow_nan=False, allow_infinity=False),
    "string": st.text(_STRING_ALPHABET, min_size=1, max_size=20).filter(_csv_safe_string),
    "boolean": st.booleans(),
    "date": st.dates(min_value=date(1, 1, 1), max_value=date(9999, 12, 31)),
    "datetime": st.datetimes(
        min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59, 999999)
    ),
}

_HEADER = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)


@st.composite
def csv_safe_tables(draw: st.DrawFn, max_rows: int = 25) -> pl.DataFrame:
    """DataFrame 1–6 kolom bertipe logis acak (dengan null), ≥ 1 baris, aman untuk CSV."""
    kinds: list[LogicalType] = draw(
        st.lists(st.sampled_from(sorted(_VALUES)), min_size=1, max_size=6)
    )
    names = draw(st.lists(_HEADER, min_size=len(kinds), max_size=len(kinds), unique=True))
    n_rows = draw(st.integers(1, max_rows))
    series = []
    for name, kind in zip(names, kinds, strict=True):
        values = draw(st.lists(st.none() | _VALUES[kind], min_size=n_rows, max_size=n_rows))
        series.append(pl.Series(name, values, dtype=TARGET_DTYPES[kind]))
    return pl.DataFrame(series)


def _expected(df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, LogicalType]]:
    """Tabel harapan setelah round-trip: kolom tanpa nilai → ``string`` null."""
    by_dtype = {repr(dtype): kind for kind, dtype in TARGET_DTYPES.items()}
    types: dict[str, LogicalType] = {}
    cols = []
    for s in df.get_columns():
        if s.null_count() == s.len():
            types[s.name] = "string"
            cols.append(pl.Series(s.name, [None] * s.len(), dtype=pl.String))
        else:
            types[s.name] = by_dtype[repr(s.dtype)]
            cols.append(s)
    return pl.DataFrame(cols), types


def _write_csv(df: pl.DataFrame, path: Path) -> None:
    path.write_bytes(df.write_csv().encode("utf-8"))


_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)


@_SETTINGS
@given(csv_safe_tables(), st.integers(1, 8))
def test_csv_to_parquet_roundtrip(tmp_path: Path, df: pl.DataFrame, batch_size: int) -> None:
    """Feature: dashboard-studio-agent, Property 5: Round-trip CSV → Parquet.

    **Validates: Requirements 2.2, 4.2, 4.3**
    """
    work = tmp_path / uuid4().hex
    work.mkdir()
    src, dst = work / "data.csv", work / "data.parquet"
    _write_csv(df, src)

    expected, types = _expected(df)
    progress: list[float] = []
    columns, mapping, row_count = convert_csv_to_parquet(
        src, dst, progress.append, batch_size=batch_size
    )

    assert [(c.name, c.type) for c in columns] == list(types.items())
    assert [(m["original"], m["normalized"]) for m in mapping] == [(n, n) for n in df.columns]
    assert row_count == df.height
    assert progress == sorted(progress) and all(0 <= p <= 99 for p in progress)
    assert not partial_path_for(dst).exists()

    back = read_parquet(dst)
    assert back.height == row_count
    assert back.schema == expected.schema
    assert_frame_equal(back, expected, check_exact=True)


async def _ingest(root: Path, csv_bytes: bytes) -> tuple[dict[str, Any], Any, pl.DataFrame | None]:
    db = await Database(root / "studio.db").open()
    try:
        repos = Repositories(db)
        svc = IngestionService(repos, EventBus(), root / "data")
        try:
            ws = await repos.workspaces.create("Round-trip")
            upload = await svc.save_upload(ws.id, "data.csv", csv_bytes)
            job_id = await svc.start_csv_job(ws.id, upload)
            status = await svc.wait_job(job_id, timeout=60)
            if status["status"] != "done":
                return status, None, None
            dataset = await repos.datasets.get(status["dataset_id"], workspace_id=ws.id)
            listed = await repos.datasets.list_by_workspace(ws.id)
            assert [d.id for d in listed] == [dataset.id]
            return status, dataset, read_parquet(svc.resolve_parquet_path(dataset))
        finally:
            await svc.aclose()
    finally:
        await db.close()


@settings(
    suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None, max_examples=20
)
@given(csv_safe_tables(max_rows=10))
def test_ingestion_registers_dataset_with_row_count(tmp_path: Path, df: pl.DataFrame) -> None:
    """Feature: dashboard-studio-agent, Property 5: Round-trip CSV → Parquet (row_count tersimpan).

    **Validates: Requirements 2.2, 4.2, 4.3**
    """
    root = tmp_path / uuid4().hex
    root.mkdir()
    status, dataset, back = asyncio.run(_ingest(root, df.write_csv().encode("utf-8")))

    assert status["status"] == "done", status
    expected, types = _expected(df)
    assert dataset.row_count == df.height
    assert [(c.name, c.type) for c in dataset.schema] == list(types.items())
    assert back is not None and back.height == dataset.row_count
    assert_frame_equal(back, expected, check_exact=True)
