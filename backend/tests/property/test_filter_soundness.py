"""Property 27: Soundness filter (Req 22.5).

Hasil ``apply_predicates`` dibandingkan dengan evaluator referensi Python
murni baris per baris.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

import polars as pl
from hypothesis import given
from hypothesis import strategies as st

from studio.core.filters import Predicate, apply_predicates
from tests.property.strategies_data import filter_sets, relation_graphs, tables


def _holds(p: Predicate, value: Any) -> bool:
    """Semantik referensi satu predikat untuk satu nilai sel."""
    if p.kind == "date_range":
        if p.start is None and p.end is None:
            return True  # no-op
        if value is None:
            return False
        d = value.date() if isinstance(value, datetime) else value
        assert isinstance(d, date)
        return (p.start is None or d >= p.start) and (p.end is None or d <= p.end)
    if value is None:
        return None in p.values
    return value in p.values


def _satisfies(row: dict[str, Any], preds: list[Predicate], table: str) -> bool:
    return all(_holds(p, row[p.column]) for p in preds if p.table == table)


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.items())


def _check_soundness(df: pl.DataFrame, preds: list[Predicate], table: str) -> None:
    result = apply_predicates(df.lazy(), preds, table=table).collect()

    assert result.schema == df.schema
    assert result.height <= df.height

    result_rows = result.to_dicts()
    # Setiap baris hasil memenuhi semua predikat tabel tersebut.
    for row in result_rows:
        assert _satisfies(row, preds, table), row

    # Setiap baris asli yang memenuhi semua predikat ada di hasil (multiset).
    expected = Counter(_row_key(r) for r in df.to_dicts() if _satisfies(r, preds, table))
    assert Counter(_row_key(r) for r in result_rows) == expected


@given(df=tables(), data=st.data())
def test_filter_soundness_single_table(df: pl.DataFrame, data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 27: Soundness filter

    **Validates: Requirements 22.5**
    """
    filters = data.draw(filter_sets({"t": dict(df.schema)}, max_size=4))
    _check_soundness(df, filters, "t")


@given(data=st.data())
def test_filter_soundness_multi_table_filter_set(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 27: Soundness filter

    FilterSet mencakup beberapa tabel; ``apply_predicates(..., table=t)`` hanya
    memakai predikat milik ``t``.

    **Validates: Requirements 22.5**
    """
    graph = data.draw(relation_graphs(max_tables=4))
    filters = data.draw(filter_sets(graph.schemas, max_size=6))
    for table, df in graph.frames.items():
        _check_soundness(df, filters, table)
