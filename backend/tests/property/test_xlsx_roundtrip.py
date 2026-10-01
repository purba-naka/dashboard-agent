"""Feature: dashboard-studio-agent, Property 7: Round-trip XLSX.

Tabel kecil yang dapat direpresentasikan di XLSX (integer, float, string,
boolean, date, datetime; dengan sel kosong) ditulis dengan ``xlsxwriter`` ke
satu atau beberapa sheet. Membaca workbook dengan ``xlsx_reader`` SHALL
menghasilkan daftar sheet yang sama (urutan dipertahankan) dan, per sheet,
nama kolom, tipe logis, jumlah baris, dan nilai sel yang ekuivalen dengan
tabel asli — baik lewat jalur baca sekaligus maupun jalur per blok.

Semantik Excel yang diperhitungkan pada nilai harapan (bukan pengecualian):

- Excel hanya punya tipe angka double: kolom yang semua nilainya bulat dibaca
  sebagai ``integer``; datetime yang semua jamnya 00:00:00 dibaca sebagai ``date``.
- Baris yang seluruh selnya kosong dibuang oleh reader.

Input yang sengaja tidak dibangkitkan (semantik Excel berbeda / tidak lossless):

- String kosong (Excel menyimpannya sebagai sel kosong → null), string dengan
  whitespace di awal/akhir, karakter kontrol, pola escape ``_xHHHH_``, dan string
  yang tampak seperti angka/tanggal/boolean (alfabet hanya huruf + spasi + tanda baca).
- Integer di luar ±10^15 dan float dengan > 15 digit signifikan (xlsxwriter
  menulis angka dengan ``%.16G``; Excel presisi 15 digit); NaN/inf.
- Tanggal sebelum 1900-03-01 (bug tahun kabisat 1900 di epoch Excel) dan
  datetime dengan presisi di bawah milidetik.
- Header yang tidak sudah ternormalisasi (normalisasi header diuji terpisah).

**Validates: Requirements 3.1, 3.2**
"""

from __future__ import annotations

import math
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import uuid4

import polars as pl
import xlsxwriter
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from polars.testing import assert_frame_equal

from studio.core.models import LogicalType
from studio.data.xlsx_reader import (
    XlsxSheetReader,
    list_sheets,
    polars_dtype_for,
    read_sheet,
)

Kind = LogicalType  # tipe logis yang ditulis (sebelum semantik Excel)

_MIN_DATE = date(1900, 3, 1)
_MAX_DATE = date(9999, 12, 31)

_STRING_ALPHABET = st.sampled_from(
    list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") + list("éüßñçøå中文日本") + [" ", "-", ".", ",", "!", "&", "'", "(", ")"]
)


def _fifteen_digits(x: float) -> float:
    return float(f"{x:.15g}")


_VALUES: dict[str, st.SearchStrategy[Any]] = {
    "integer": st.integers(-(10**15), 10**15),
    "float": st.floats(
        -1e12, 1e12, allow_nan=False, allow_infinity=False, allow_subnormal=False
    ).map(_fifteen_digits),
    "string": st.text(_STRING_ALPHABET, min_size=1, max_size=20).filter(
        lambda s: s == s.strip() and any(c.isalpha() for c in s)
    ),
    "boolean": st.booleans(),
    "date": st.dates(min_value=_MIN_DATE, max_value=_MAX_DATE),
    "datetime": st.datetimes(
        min_value=datetime.combine(_MIN_DATE, time()),
        max_value=datetime(9999, 12, 31, 23, 59, 59),
    ).map(lambda d: d.replace(microsecond=(d.microsecond // 1000) * 1000)),
}

_HEADER = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)
_SHEET_NAME = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,15}", fullmatch=True).filter(
    lambda s: s.lower() != "history"  # nama sheet reservasi Excel
)


class Table:
    """Satu sheet: nama kolom, jenis per kolom, dan baris (``None`` = sel kosong)."""

    def __init__(self, columns: list[str], kinds: list[Kind], rows: list[list[Any]]) -> None:
        self.columns = columns
        self.kinds = kinds
        self.rows = rows

    def __repr__(self) -> str:  # ringkas untuk laporan Hypothesis
        return f"Table(columns={self.columns!r}, kinds={self.kinds!r}, rows={self.rows!r})"


@st.composite
def tables(draw: st.DrawFn) -> Table:
    kinds: list[Kind] = draw(st.lists(st.sampled_from(sorted(_VALUES)), min_size=1, max_size=5))
    columns = draw(st.lists(_HEADER, min_size=len(kinds), max_size=len(kinds), unique=True))
    row = st.tuples(*(st.none() | _VALUES[k] for k in kinds)).map(list)
    rows = draw(
        st.lists(row, min_size=1, max_size=12).filter(
            lambda rs: any(v is not None for r in rs for v in r)
        )
    )
    return Table(columns, kinds, rows)


@st.composite
def workbooks(draw: st.DrawFn) -> list[tuple[str, Table]]:
    names = draw(st.lists(_SHEET_NAME, min_size=1, max_size=3, unique_by=str.lower))
    return [(name, draw(tables())) for name in names]


# ---------------------------------------------------------------------------
# Tulis & nilai harapan
# ---------------------------------------------------------------------------


def _write_workbook(path: Path, sheets: list[tuple[str, Table]]) -> None:
    wb = xlsxwriter.Workbook(str(path))
    date_fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
    dt_fmt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm:ss.000"})
    for name, table in sheets:
        ws = wb.add_worksheet(name)
        for c, header in enumerate(table.columns):
            ws.write_string(0, c, header)
        for r, values in enumerate(table.rows, start=1):
            for c, (kind, value) in enumerate(zip(table.kinds, values, strict=True)):
                if value is None:
                    continue
                if kind in ("integer", "float"):
                    ws.write_number(r, c, value)
                elif kind == "string":
                    ws.write_string(r, c, value)
                elif kind == "boolean":
                    ws.write_boolean(r, c, value)
                elif kind == "date":
                    ws.write_datetime(r, c, datetime.combine(value, time()), date_fmt)
                else:
                    ws.write_datetime(r, c, value, dt_fmt)
    wb.close()


def _expected_type(kind: Kind, values: list[Any]) -> LogicalType:
    present = [v for v in values if v is not None]
    if not present:
        return "string"  # kolom tanpa nilai
    if kind == "float" and all(math.floor(v) == v for v in present):
        return "integer"  # Excel tidak membedakan 3 dan 3.0
    if kind == "datetime" and all(v.time() == time() for v in present):
        return "date"  # datetime tengah malam dibaca sebagai tanggal
    return kind


def _expected_frame(table: Table) -> tuple[pl.DataFrame, dict[str, LogicalType]]:
    rows = [r for r in table.rows if any(v is not None for v in r)]
    types: dict[str, LogicalType] = {}
    series = []
    for i, (name, kind) in enumerate(zip(table.columns, table.kinds, strict=True)):
        values = [r[i] for r in rows]
        ltype = _expected_type(kind, values)
        if ltype == "integer":
            values = [None if v is None else int(v) for v in values]
        elif ltype == "date":
            values = [None if v is None else (v.date() if isinstance(v, datetime) else v) for v in values]
        types[name] = ltype
        series.append(pl.Series(name, values, dtype=polars_dtype_for(ltype)))
    return pl.DataFrame(series), types


def _check(df: pl.DataFrame, schema: list[Any], expected: pl.DataFrame, types: dict[str, LogicalType]) -> None:
    assert df.columns == list(types)
    assert {c.name: c.type for c in schema} == types
    assert [c.name for c in schema] == list(types)
    assert df.height == expected.height
    assert_frame_equal(df, expected, check_exact=True)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------

_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)


@_SETTINGS
@given(workbooks(), st.integers(1, 5))
# Regresi: kolom kosong di blok pertama dulu terkunci ke "string" pada jalur blok.
@example(
    sheets=[
        ("G", Table(columns=["a", "a0"], kinds=["boolean", "boolean"], rows=[[None, False], [False, None]]))
    ],
    block_rows=1,
)
def test_xlsx_roundtrip(tmp_path: Path, sheets: list[tuple[str, Table]], block_rows: int) -> None:
    """Feature: dashboard-studio-agent, Property 7: Round-trip XLSX.

    **Validates: Requirements 3.1, 3.2**
    """
    path = tmp_path / f"{uuid4().hex}.xlsx"
    _write_workbook(path, sheets)

    # Req 3.2: daftar sheet sama dan berurutan (pada kedua jalur).
    written = [name for name, _ in sheets]
    assert [s.name for s in list_sheets(path)] == written
    assert [s.name for s in list_sheets(path, block_threshold_bytes=0)] == written

    for name, table in sheets:
        expected, types = _expected_frame(table)

        # Jalur baca sekaligus (file ≤ ambang).
        df, schema, mapping = read_sheet(path, name)
        _check(df, schema, expected, types)
        assert [(m["original"], m["normalized"]) for m in mapping] == [(c, c) for c in table.columns]

        # Jalur per blok (ambang 0 memaksa blok); pelebaran tipe antar-blok ditangani read_sheet.
        df_b, schema_b, _ = read_sheet(path, name, block_threshold_bytes=0, batch_size=block_rows)
        _check(df_b, schema_b, expected, types)

        # XlsxSheetReader jalur blok langsung, blok cukup besar (tanpa pelebaran).
        reader = XlsxSheetReader(path, name, block_threshold_bytes=0)
        assert reader.uses_blocks
        _check(reader.read(), reader.schema(), expected, types)
