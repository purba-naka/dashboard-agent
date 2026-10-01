"""Feature: dashboard-studio-agent, Property 6: Round-trip Parquet.

Untuk DataFrame Polars valid dengan tipe logis yang didukung (Int64, Float64,
String, Boolean, Date, Datetime[us]; semua kolom boleh berisi null),
``write_parquet`` lalu ``read_parquet`` menghasilkan DataFrame identik
(termasuk dtype, urutan kolom, dan null). Hal yang sama berlaku bila data
ditulis per batch lewat ``ParquetBatchWriter``.

**Validates: Requirements 4.4**
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from polars.testing import assert_frame_equal

from studio.data.parquet_writer import (
    ParquetBatchWriter,
    partial_path_for,
    read_parquet,
    write_parquet,
)

_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1

#: dtype target → strategi nilai non-null.
_VALUE_STRATEGIES: dict[str, tuple[pl.DataType, st.SearchStrategy[object]]] = {
    "integer": (pl.Int64(), st.integers(_INT64_MIN, _INT64_MAX)),
    "float": (pl.Float64(), st.floats(allow_nan=True, allow_infinity=True)),
    "string": (pl.String(), st.text(max_size=20)),
    "boolean": (pl.Boolean(), st.booleans()),
    "date": (pl.Date(), st.dates()),
    "datetime": (
        pl.Datetime("us"),
        st.datetimes(min_value=datetime(1, 1, 1), max_value=datetime(9999, 12, 31, 23, 59, 59)),
    ),
}


@st.composite
def frames(draw: st.DrawFn, max_rows: int = 30) -> pl.DataFrame:
    """DataFrame 1–6 kolom bertipe logis acak dengan null; boleh 0 baris."""
    kinds = draw(st.lists(st.sampled_from(sorted(_VALUE_STRATEGIES)), min_size=1, max_size=6))
    # Kolom Parquet Studio selalu bernama hasil ``normalize_columns`` (snake_case
    # ASCII); nama berisi NUL tidak didukung Arrow FFI sehingga tidak dibangkitkan.
    names = draw(
        st.lists(
            st.from_regex(r"[a-z_][a-z0-9_]{0,15}", fullmatch=True),
            min_size=len(kinds),
            max_size=len(kinds),
            unique=True,
        )
    )
    n_rows = draw(st.integers(0, max_rows))
    columns: dict[str, pl.Series] = {}
    for name, kind in zip(names, kinds, strict=True):
        dtype, values = _VALUE_STRATEGIES[kind]
        data = draw(st.lists(st.none() | values, min_size=n_rows, max_size=n_rows))
        columns[name] = pl.Series(name, data, dtype=dtype)
    return pl.DataFrame(list(columns.values()))


def _fresh_path(tmp_path: Path) -> Path:
    # tmp_path dipakai bersama oleh semua contoh Hypothesis; gunakan nama unik per contoh.
    return tmp_path / f"{uuid4().hex}.parquet"


_SETTINGS = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


@_SETTINGS
@given(frames())
def test_write_then_read_parquet_is_identity(tmp_path: Path, df: pl.DataFrame) -> None:
    """Feature: dashboard-studio-agent, Property 6: Round-trip Parquet.

    **Validates: Requirements 4.4**
    """
    path = _fresh_path(tmp_path)
    written = write_parquet(df, path)

    assert written == path
    assert not partial_path_for(path).exists()
    back = read_parquet(path)
    assert back.schema == df.schema
    assert_frame_equal(back, df, check_exact=True)


@_SETTINGS
@given(frames(max_rows=40), st.data())
def test_batched_write_then_read_parquet_is_identity(
    tmp_path: Path, df: pl.DataFrame, data: st.DataObject
) -> None:
    """Feature: dashboard-studio-agent, Property 6: Round-trip Parquet (per batch).

    **Validates: Requirements 4.4**
    """
    cuts = sorted(data.draw(st.lists(st.integers(0, df.height), max_size=4), label="cuts"))
    bounds = [0, *cuts, df.height]
    batches = [df.slice(a, b - a) for a, b in zip(bounds, bounds[1:], strict=False)]
    # Skema eksplisit atau diambil dari batch pertama.
    explicit = data.draw(st.booleans(), label="explicit_schema")

    path = _fresh_path(tmp_path)
    with ParquetBatchWriter(path, df.schema if explicit else None) as writer:
        for batch in batches:
            writer.write(batch)

    assert writer.finalized
    assert writer.rows_written == df.height
    assert not partial_path_for(path).exists()
    back = read_parquet(path)
    assert back.schema == df.schema
    assert_frame_equal(back, df, check_exact=True)
