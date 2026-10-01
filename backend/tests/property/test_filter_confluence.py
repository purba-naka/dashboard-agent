"""Property 29: Confluence filter (Req 22.7).

Hasil Filter_Engine (predikat langsung + propagasi melalui Confirmed_Relation)
tidak bergantung pada urutan predikat dalam FilterSet: ``[F1, F2]`` dan
``[F2, F1]`` — serta permutasi apa pun — menghasilkan tabel yang sama pada
setiap tabel (dibandingkan sebagai multiset baris).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.filters import Predicate
from studio.core.propagation import RelationGraph, materialize, plan_propagation
from tests.property.strategies_data import (
    DataGraph,
    graphs_with_filters,
    predicates,
    relation_graphs,
)


def _multiset(df: pl.DataFrame) -> Counter[tuple[Any, ...]]:
    return Counter(tuple(r.items()) for r in df.to_dicts())


def _run_engine(graph: DataGraph, preds: list[Predicate]) -> dict[str, pl.DataFrame]:
    rel_graph = RelationGraph(graph.relations)
    plan = plan_propagation(rel_graph, preds)
    return {t: lf.collect() for t, lf in materialize(graph.lazy(), preds, plan).items()}


def _assert_same_tables(a: dict[str, pl.DataFrame], b: dict[str, pl.DataFrame]) -> None:
    assert a.keys() == b.keys()
    for table in a:
        assert a[table].schema == b[table].schema, table
        assert _multiset(a[table]) == _multiset(b[table]), table


@settings(deadline=None)
@given(data=st.data())
def test_filter_confluence_two_predicates(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 29: Confluence filter

    ``[F1, F2]`` vs ``[F2, F1]`` pada graf dengan relasi (termasuk propagasi).

    **Validates: Requirements 22.7**
    """
    graph = data.draw(relation_graphs(max_tables=5))
    schemas = graph.schemas
    table = st.sampled_from(sorted(schemas))
    pred = table.flatmap(lambda t: predicates(t, schemas[t]))
    f1, f2 = data.draw(pred), data.draw(pred)

    rel_graph = RelationGraph(graph.relations)
    assert plan_propagation(rel_graph, [f1, f2]) == plan_propagation(rel_graph, [f2, f1])
    _assert_same_tables(_run_engine(graph, [f1, f2]), _run_engine(graph, [f2, f1]))


@settings(deadline=None)
@given(data=st.data())
def test_filter_confluence_permutations(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 29: Confluence filter

    Permutasi sembarang FilterSet (boleh duplikat) menghasilkan rencana dan
    tabel termaterialisasi yang sama.

    **Validates: Requirements 22.7**
    """
    graph, preds = data.draw(graphs_with_filters(max_tables=5, max_filters=4))
    permuted = data.draw(st.permutations(preds))

    rel_graph = RelationGraph(graph.relations)
    assert plan_propagation(rel_graph, preds) == plan_propagation(rel_graph, permuted)
    _assert_same_tables(_run_engine(graph, preds), _run_engine(graph, list(permuted)))
