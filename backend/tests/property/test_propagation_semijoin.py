"""Property 30: Invariant semi-join propagasi (Req 23.1, 23.5).

Hasil ``materialize`` dibandingkan dengan referensi semi-join berbasis himpunan
Python murni: predikat langsung dievaluasi baris per baris, lalu kunci
dirambatkan sepanjang jalur BFS setiap constraint memakai ``set`` Python.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.filters import Predicate
from studio.core.propagation import (
    Constraint,
    RelationGraph,
    materialize,
    plan_propagation,
)
from tests.property.strategies_data import DataGraph, graphs_with_filters

Row = dict[str, Any]


# ---------------------------------------------------------------------------
# Referensi Python murni
# ---------------------------------------------------------------------------


def _holds(p: Predicate, value: Any) -> bool:
    if p.kind == "date_range":
        if p.start is None and p.end is None:
            return True
        if value is None:
            return False
        d = value.date() if isinstance(value, datetime) else value
        assert isinstance(d, date)
        return (p.start is None or d >= p.start) and (p.end is None or d <= p.end)
    if value is None:
        return None in p.values
    return value in p.values


def _direct_rows(rows: list[Row], preds: list[Predicate], table: str) -> list[Row]:
    own = [p for p in preds if p.table == table]
    return [r for r in rows if all(_holds(p, r[p.column]) for p in own)]


def _key_set(rows: list[Row], column: str) -> set[Any]:
    """Nilai kunci non-null (null tidak pernah cocok dalam semi-join)."""
    return {r[column] for r in rows if r[column] is not None}


def _reference_chain(
    base: dict[str, list[Row]], preds: list[Predicate], c: Constraint
) -> list[tuple[list[Row], str]]:
    """Untuk setiap langkah jalur: ``(baris induk terfilter, kolom induk)``.

    Induk langkah pertama = sumber dengan predikat langsung; induk berikutnya
    = scan dasar tabel antara yang di-semi-join dengan kunci induknya.
    """
    parent_rows = _direct_rows(base[c.source], preds, c.source)
    chain: list[tuple[list[Row], str]] = []
    for step in c.path:
        assert step.parent_table in base
        chain.append((parent_rows, step.parent_column))
        keys = _key_set(parent_rows, step.parent_column)
        parent_rows = [r for r in base[step.child_table] if r[step.child_column] in keys]
    return chain


def _multiset(rows: list[Row]) -> Counter[tuple[Any, ...]]:
    return Counter(tuple(r.items()) for r in rows)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


def _check_semijoin(graph: DataGraph, preds: list[Predicate]) -> None:
    plan = plan_propagation(RelationGraph(graph.relations), preds)
    result = {t: lf.collect() for t, lf in materialize(graph.lazy(), preds, plan).items()}
    base = {t: df.to_dicts() for t, df in graph.frames.items()}
    sources = {p.table for p in preds}

    for table, rows in base.items():
        expected = _direct_rows(rows, preds, table) if table in sources else list(rows)
        out_rows = result[table].to_dicts()
        for c in plan.for_table(table):
            assert c.target == table
            chain = _reference_chain(base, preds, c)
            parent_rows, parent_col = chain[-1]
            parent_keys = _key_set(parent_rows, parent_col)
            last = c.path[-1]
            # Invariant: setiap nilai V.v pada hasil ada di U.u induk terfilter.
            for r in out_rows:
                assert r[last.child_column] in parent_keys, (c, r)
            expected = [r for r in expected if r[last.child_column] in parent_keys]

        assert result[table].schema == graph.frames[table].schema
        assert _multiset(out_rows) == _multiset(expected), table


@settings(deadline=None)
@given(data=st.data())
def test_propagation_semijoin_matches_reference(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 30: Invariant semi-join propagasi

    Graf acak (status confirmed/candidate/rejected, sisi paralel & siklus).

    **Validates: Requirements 23.1, 23.5**
    """
    graph, preds = data.draw(graphs_with_filters(max_tables=5, max_filters=4))
    _check_semijoin(graph, preds)


@settings(deadline=None)
@given(data=st.data())
def test_propagation_semijoin_all_confirmed(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 30: Invariant semi-join propagasi

    Semua relasi confirmed → propagasi lebih padat (jalur panjang & siklus).

    **Validates: Requirements 23.1, 23.5**
    """
    graph, preds = data.draw(
        graphs_with_filters(max_tables=5, max_filters=3, statuses=("confirmed",))
    )
    _check_semijoin(graph, preds)
