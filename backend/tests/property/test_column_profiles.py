"""Feature: dashboard-studio-agent, Property 9: Profil kolom sesuai perhitungan referensi.

``compute_column_profiles`` + ``classify_roles`` dibandingkan dengan perhitungan
referensi Python murni atas ``DataFrame.rows()`` / ``Series.to_list()``.

**Validates: Requirements 6.1, 6.2, 6.3, 6.5**
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date, datetime, time
from typing import Any

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.core.models import ColumnInfo
from studio.core.relations import normalize_name
from studio.data.profiler import classify_roles, compute_column_profiles

_ROLES = {"dimension", "measure", "time", "identifier"}
_NUMERIC = {"integer", "float"}
_TEMPORAL = {"date", "datetime"}
_ID_NAME = re.compile(r"^id$|_id$|^id_|uuid|kode|_code$")

# ---------------------------------------------------------------------------
# Generator: domain kecil agar duplikat, seri top value, dan rasio unik sering muncul
# ---------------------------------------------------------------------------

_DATES = tuple(date(2024, 1, d) for d in (1, 2, 5))
#: slot kolom → (tipe logis, dtype Polars, domain nilai non-null, pilihan nama)
_SLOTS: dict[str, tuple[str, pl.DataType, tuple[Any, ...], tuple[str, ...]]] = {
    "int": ("integer", pl.Int64(), (-3, 0, 1, 2, 7), ("qty", "customer_id", "CustomerID")),
    "float": (
        "float",
        pl.Float64(),
        (-1.5, 0.0, 2.25, 10.0, math.nan),
        ("price", "score_code"),
    ),
    "str": (
        "string",
        pl.String(),
        ("a", "b", "B", "", "é", "1", " 2 ", "3.5", "2024-01-05"),
        ("region", "kode_toko", "uuid"),
    ),
    "bool": ("boolean", pl.Boolean(), (True, False), ("flag", "is_id")),
    "date": ("date", pl.Date(), _DATES, ("tanggal", "date_id")),
    "datetime": (
        "datetime",
        pl.Datetime("us"),
        tuple(datetime.combine(d, t) for d in _DATES[:2] for t in (time(0), time(12, 30))),
        ("ts", "created_at"),
    ),
}


@st.composite
def frames(draw: st.DrawFn) -> tuple[pl.DataFrame, list[ColumnInfo]]:
    slots = draw(
        st.lists(st.sampled_from(sorted(_SLOTS)), min_size=1, max_size=len(_SLOTS), unique=True)
    )
    # Domain per kolom kadang dipersempit (lebih banyak duplikat / kolom konstan).
    columns: list[tuple[str, str, pl.DataType, tuple[Any, ...]]] = []
    for slot in slots:
        ltype, dtype, domain, names = _SLOTS[slot]
        sub = draw(st.lists(st.sampled_from(domain), min_size=1, max_size=len(domain), unique=True))
        columns.append((draw(st.sampled_from(names)), ltype, dtype, tuple(sub)))
    cell_strategies = [
        st.one_of(st.none(), st.sampled_from(domain)) for _, _, _, domain in columns
    ]
    rows = draw(st.lists(st.tuples(*cell_strategies), max_size=12))
    # Sesekali gandakan baris agar baris duplikat pasti ada.
    if rows and draw(st.booleans()):
        rows += draw(st.lists(st.sampled_from(rows), max_size=4))
    data = {name: [r[i] for r in rows] for i, (name, _, _, _) in enumerate(columns)}
    df = pl.DataFrame(data, schema={name: dtype for name, _, dtype, _ in columns})
    schema = [ColumnInfo(name=name, type=ltype) for name, ltype, _, _ in columns]  # type: ignore[arg-type]
    return df, schema


# ---------------------------------------------------------------------------
# Referensi Python murni
# ---------------------------------------------------------------------------


def _is_nan(v: Any) -> bool:
    return isinstance(v, float) and math.isnan(v)


def _json(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _hashable(v: Any) -> Any:
    return "<NaN>" if _is_nan(v) else v  # NaN != NaN; Polars menyamakan NaN saat hashing


def _typed(text: str) -> bool:
    s = text.strip()
    try:
        float(s)
        return True
    except ValueError:
        pass
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _reference(values: list[Any], ltype: str, name: str, row_count: int) -> dict[str, Any]:
    non_null = [v for v in values if v is not None]
    nulls = len(values) - len(non_null)
    distinct = len({_hashable(v) for v in non_null})
    ref: dict[str, Any] = {
        "null_count": nulls,
        "null_pct": nulls * 100.0 / row_count if row_count else 0.0,
        "distinct_count": distinct,
        "min": None,
        "max": None,
        "mean": None,
    }
    comparable = [v for v in non_null if not _is_nan(v)]
    if ltype in _NUMERIC or ltype in _TEMPORAL:
        ref["min"] = _json(min(comparable)) if comparable else None
        ref["max"] = _json(max(comparable)) if comparable else None
    if ltype in _NUMERIC and non_null and not any(_is_nan(v) for v in non_null):
        ref["mean"] = sum(non_null) / len(non_null)
    counts = Counter(comparable)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ref["top_values"] = [(_json(v), c) for v, c in ranked]
    ref["mixed"] = ltype == "string" and 0 < sum(map(_typed, non_null)) < len(non_null)

    if ltype in _TEMPORAL:
        role = "time"
    elif ltype != "boolean" and _ID_NAME.search(normalize_name(name)):
        role = "identifier"
    elif ltype in ("integer", "string") and row_count >= 2 and distinct >= 0.95 * row_count:
        role = "identifier"
    elif ltype in _NUMERIC:
        role = "measure"
    else:
        role = "dimension"
    ref["role"] = role
    return ref


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(frames())
def test_column_profiles_match_reference(case: tuple[pl.DataFrame, list[ColumnInfo]]) -> None:
    """Feature: dashboard-studio-agent, Property 9: Profil kolom sesuai perhitungan referensi.

    **Validates: Requirements 6.1, 6.2, 6.3, 6.5**
    """
    df, schema = case
    result = compute_column_profiles(df.lazy(), schema)
    row_count = df.height
    assert result.row_count == row_count

    # Tepat satu Column_Profile per kolom, urutan skema.
    assert [p.name for p in result.columns] == [c.name for c in schema]
    assert [p.type for p in result.columns] == [c.type for c in schema]

    mixed_ref: list[str] = []
    for col, prof in zip(schema, result.columns, strict=True):
        ref = _reference(df[col.name].to_list(), col.type, col.name, row_count)
        assert prof.null_count == ref["null_count"], col.name
        assert math.isclose(prof.null_pct, ref["null_pct"], rel_tol=1e-12, abs_tol=1e-12)
        assert result.quality["null_pct"][col.name] == prof.null_pct
        assert prof.distinct_count == ref["distinct_count"], col.name
        assert prof.min == ref["min"], col.name
        assert prof.max == ref["max"], col.name
        if ref["mean"] is None:
            assert prof.mean is None, col.name
        else:
            assert prof.mean is not None
            assert math.isclose(prof.mean, ref["mean"], rel_tol=1e-9, abs_tol=1e-9), col.name
        assert [tuple(tv) for tv in prof.top_values] == ref["top_values"], col.name
        assert prof.role in _ROLES
        assert prof.role == ref["role"], col.name
        if ref["mixed"]:
            mixed_ref.append(col.name)

    # Kualitas data (Req 6.2).
    unique_rows = {tuple(_hashable(v) for v in row) for row in df.rows()}
    assert result.quality["duplicate_rows"] == row_count - len(unique_rows)
    assert result.quality["mixed_type_columns"] == mixed_ref
    assert set(result.quality["null_pct"]) == {c.name for c in schema}

    # Setiap kolom tepat satu peran, konsisten dengan classify_roles (Req 6.3).
    roles = classify_roles(schema, result.columns, row_count)
    assert roles == {p.name: p.role for p in result.columns}
    assert roles == classify_roles(schema, {p.name: p for p in result.columns}, row_count)

    # Deterministik (Req 6.5): perhitungan ulang pada data yang sama identik.
    again = compute_column_profiles(df.clone().lazy(), schema)
    assert again == result
    assert [p.model_dump() for p in again.columns] == [p.model_dump() for p in result.columns]
