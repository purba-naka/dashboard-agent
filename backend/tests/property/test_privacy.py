"""Feature: dashboard-studio-agent, Property 35: Batas payload privasi.

**Validates: Requirements 27.1, 27.3, 27.4, 27.5, 27.6**
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from studio.core.models import ColumnInfo, ColumnProfile, QueryResult
from studio.core.privacy import (
    MAX_RESULT_ROWS,
    PrivacySettings,
    build_dataset_context,
    json_safe,
    truncate_result,
)

#: Penanda nilai asli kolom string; tidak boleh bocor saat ``no_samples`` aktif.
SECRET = "SECRET_"

_POLARS_DTYPE = {
    "integer": pl.Int64,
    "float": pl.Float64,
    "string": pl.Utf8,
    "boolean": pl.Boolean,
    "date": pl.Date,
    "datetime": pl.Datetime("us"),
}

_secret_str = st.text(alphabet="abcxyz019 ", max_size=6).map(lambda s: SECRET + s)
_VALUE_STRATEGY: dict[str, st.SearchStrategy[Any]] = {
    "integer": st.integers(-(2**31), 2**31),
    "float": st.floats(allow_nan=True, allow_infinity=True, width=64),
    "string": _secret_str,
    "boolean": st.booleans(),
    "date": st.dates(min_value=date(1970, 1, 1), max_value=date(2100, 1, 1)),
    "datetime": st.datetimes(min_value=datetime(1970, 1, 1), max_value=datetime(2100, 1, 1)),
}
# Nilai profil (Scalar) per tipe; tanggal tidak termasuk Scalar sehingga None.
_SCALAR_STRATEGY: dict[str, st.SearchStrategy[Any]] = {
    "integer": st.integers(-1000, 1000),
    "float": st.floats(allow_nan=False, allow_infinity=False, width=32),
    "string": _secret_str,
    "boolean": st.booleans(),
    "date": st.none(),
    "datetime": st.none(),
}
_ROLE = {"integer": "measure", "float": "measure", "string": "dimension", "boolean": "dimension",
         "date": "time", "datetime": "time"}


@dataclass(frozen=True)
class _Dataset:
    table_name: str
    schema: list[ColumnInfo]


@st.composite
def _dataset_inputs(draw: st.DrawFn) -> tuple[_Dataset, list[ColumnProfile], pl.DataFrame | None]:
    types = draw(st.lists(st.sampled_from(list(_POLARS_DTYPE)), min_size=1, max_size=6))
    schema = [ColumnInfo(name=f"col_{i}", type=t) for i, t in enumerate(types)]
    n_rows = draw(st.integers(0, 15))

    profiles: list[ColumnProfile] = []
    for col in schema:
        scalar = _SCALAR_STRATEGY[col.type]
        top = draw(st.lists(st.tuples(scalar, st.integers(1, 100)), max_size=5))
        profiles.append(
            ColumnProfile(
                name=col.name,
                type=col.type,
                role=_ROLE[col.type],
                null_count=draw(st.integers(0, n_rows)),
                null_pct=draw(st.floats(0.0, 100.0)),
                distinct_count=draw(st.integers(0, n_rows)),
                min=draw(scalar),
                max=draw(scalar),
                mean=draw(st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=False)))
                if col.type in ("integer", "float")
                else None,
                top_values=[list(t) for t in top],
            )
        )

    if draw(st.booleans()) and n_rows == 0:
        sample = None
    else:
        sample = pl.DataFrame(
            [
                pl.Series(
                    col.name,
                    draw(st.lists(st.one_of(st.none(), _VALUE_STRATEGY[col.type]),
                                  min_size=n_rows, max_size=n_rows)),
                    dtype=_POLARS_DTYPE[col.type],
                )
                for col in schema
            ]
        )
    return _Dataset(table_name="sales_2024", schema=schema), profiles, sample


_privacy_settings = st.builds(PrivacySettings, sample_rows=st.integers(0, 10), no_samples=st.booleans())


@given(_dataset_inputs(), _privacy_settings)
def test_dataset_context_respects_privacy_limits(
    inputs: tuple[_Dataset, list[ColumnProfile], pl.DataFrame | None],
    privacy: PrivacySettings,
) -> None:
    """Feature: dashboard-studio-agent, Property 35: Batas payload privasi.

    **Validates: Requirements 27.1, 27.4, 27.5**
    """
    dataset, profiles, sample = inputs
    context = build_dataset_context(dataset, profiles, sample, privacy)

    # Hanya skema, Column_Profile, dan (opsional) Sample_Rows.
    expected_keys = {"table_name", "schema", "column_profiles"}
    if not privacy.no_samples:
        expected_keys.add("sample_rows")
    assert set(context) == expected_keys
    assert context["schema"] == [{"name": c.name, "type": c.type} for c in dataset.schema]
    assert len(context["column_profiles"]) == len(profiles)

    # Payload harus JSON valid (tanpa NaN/inf).
    encoded = json.dumps(context, allow_nan=False)

    if privacy.no_samples:
        # 0 sample rows dan nilai contoh kolom string dihapus.
        for prof_dict, prof in zip(context["column_profiles"], profiles, strict=True):
            if prof.type == "string":
                assert not {"top_values", "min", "max"} & set(prof_dict)
        assert SECRET not in encoded
    else:
        rows = context["sample_rows"]
        height = 0 if sample is None else sample.height
        assert len(rows) == min(height, privacy.sample_rows) <= privacy.sample_rows
        if sample is not None:
            expected_rows = [
                {k: json_safe(v) for k, v in row.items()}
                for row in sample.head(privacy.sample_rows).iter_rows(named=True)
            ]
            assert rows == expected_rows


@st.composite
def _query_results(draw: st.DrawFn) -> QueryResult:
    n_rows = draw(st.integers(0, 450))
    extra = draw(st.one_of(st.just(0), st.integers(0, 10_000)))
    return QueryResult(
        query_id="q_1",
        columns=[ColumnInfo(name="i", type="integer"), ColumnInfo(name="label", type="string")],
        rows=[[i, f"v{i}"] for i in range(n_rows)],
        row_count=n_rows + extra,
        truncated_for_storage=extra > 0,
    )


@given(_query_results(), st.one_of(st.none(), st.integers(0, MAX_RESULT_ROWS)))
def test_truncate_result_caps_rows(result: QueryResult, limit: int | None) -> None:
    """Feature: dashboard-studio-agent, Property 35: Batas payload privasi.

    **Validates: Requirements 27.3, 27.6**
    """
    payload = truncate_result(result) if limit is None else truncate_result(result, limit)
    effective = MAX_RESULT_ROWS if limit is None else limit

    assert set(payload) == {"columns", "rows", "row_count", "truncated"}
    assert len(payload["rows"]) <= effective <= MAX_RESULT_ROWS
    assert payload["rows"] == result.rows[:effective]
    assert payload["row_count"] == result.row_count
    assert payload["truncated"] == (result.row_count > effective)


def test_truncate_result_rejects_limit_above_cap() -> None:
    result = QueryResult(query_id="q", columns=[], rows=[], row_count=0)
    with pytest.raises(ValueError):
        truncate_result(result, MAX_RESULT_ROWS + 1)
