"""Property 31: Cakupan dan terminasi propagasi (Req 23.2, 23.3, 23.4).

Untuk graf relasi acak (termasuk siklus, sisi paralel, dan relasi berstatus
candidate/rejected) dan FilterSet:

- ``plan_propagation`` berhenti; setiap tabel menerima ≤ 1 constraint per sumber
  dan setiap jalur tidak mengunjungi tabel dua kali (Req 23.4);
- hanya Confirmed_Relation yang dipakai; cakupan rencana = tabel terjangkau
  dari sumber menurut BFS referensi, dengan jalur terpendek (Req 23.2);
- tabel di luar ``sources ∪ reachable(sources)`` tidak berubah, dan
  ``is_filter_unaffected`` ⇔ ``tables_used`` disjoint dengan himpunan itu
  (Req 23.3);
- rencana dapat di-round-trip lewat JSON tanpa mengubah hasil.
"""

from __future__ import annotations

import json
from collections import deque

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.filters import in_values
from studio.core.propagation import (
    PropagationPlan,
    RelationGraph,
    affected_tables,
    is_filter_unaffected,
    materialize,
    plan_propagation,
)
from tests.property.strategies_data import DataGraph, RelationSpec, graphs_with_filters, tables


def _reference_distances(graph: DataGraph, source: str) -> dict[str, int]:
    """Jarak BFS dari ``source`` pada graf tak berarah Confirmed_Relation saja."""
    adj: dict[str, set[str]] = {}
    for r in graph.relations:
        if r.status != "confirmed":
            continue
        adj.setdefault(r.table_a, set()).add(r.table_b)
        adj.setdefault(r.table_b, set()).add(r.table_a)
    dist = {source: 0}
    queue = deque([source])
    while queue:
        u = queue.popleft()
        for v in sorted(adj.get(u, ())):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def _check_coverage(graph: DataGraph, preds: list, tables_used: set[str]) -> None:
    rel_graph = RelationGraph(graph.relations)
    plan = plan_propagation(rel_graph, preds)  # harus berhenti (siklus diperbolehkan)
    sources = sorted({p.table for p in preds if not p.is_noop})
    assert list(plan.sources) == sources

    confirmed = {r.id: r for r in graph.relations if r.status == "confirmed"}

    # Referensi cakupan: target yang terjangkau per sumber + jarak terpendek.
    expected_pairs: dict[tuple[str, str], int] = {}
    for s in sources:
        for v, d in _reference_distances(graph, s).items():
            if v != s:
                expected_pairs[(s, v)] = d

    seen_pairs: set[tuple[str, str]] = set()
    for target, constraints in plan.constraints:
        per_source = [c.source for c in constraints]
        # ≤ 1 constraint per sumber untuk setiap tabel.
        assert len(per_source) == len(set(per_source)), (target, per_source)
        for c in constraints:
            assert c.target == target
            assert c.source != target
            visited = [c.source] + [step.child_table for step in c.path]
            # Setiap tabel paling banyak sekali pada jalur.
            assert len(visited) == len(set(visited)), visited
            # Jalur BFS terpendek.
            assert len(c.path) == expected_pairs[(c.source, target)]
            # Hanya Confirmed_Relation, dengan orientasi kolom yang benar.
            for step in c.path:
                rel = confirmed.get(step.relation_id)
                assert rel is not None, step
                ends = {(rel.table_a, rel.column_a), (rel.table_b, rel.column_b)}
                assert {(step.parent_table, step.parent_column),
                        (step.child_table, step.child_column)} == ends
            seen_pairs.add((c.source, target))
    assert seen_pairs == set(expected_pairs)

    # Relasi candidate/rejected tidak berpengaruh sama sekali.
    assert plan_propagation(RelationGraph(graph.confirmed), preds) == plan

    # affected_tables = sources ∪ reachable(sources).
    affected = affected_tables(rel_graph, preds)
    expected_affected = set(sources) | {v for (_, v) in expected_pairs}
    assert affected == expected_affected == plan.affected_tables

    # Tabel di luar himpunan terpengaruh tidak berubah (identik, termasuk urutan).
    result = {t: lf.collect() for t, lf in materialize(graph.lazy(), preds, plan).items()}
    for table, df in graph.frames.items():
        if table not in affected:
            assert result[table].equals(df), table

    # Flag filter_unaffected ⇔ tables_used ∩ affected = ∅.
    assert is_filter_unaffected(tables_used, affected) == tables_used.isdisjoint(
        expected_affected
    )

    # Round trip JSON rencana → rencana & hasil identik.
    restored = PropagationPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert restored == plan
    assert restored.to_dict() == plan.to_dict()
    again = {t: lf.collect() for t, lf in materialize(graph.lazy(), preds, restored).items()}
    for table in result:
        assert again[table].equals(result[table]), table


@settings(deadline=None)
@given(data=st.data())
def test_propagation_coverage_and_termination(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 31: Cakupan dan terminasi propagasi

    **Validates: Requirements 23.2, 23.3, 23.4**
    """
    graph, preds = data.draw(graphs_with_filters(max_tables=6, max_filters=4))
    tables_used = data.draw(st.sets(st.sampled_from(sorted(graph.frames)), max_size=3))
    _check_coverage(graph, preds, tables_used)


@settings(deadline=None)
@given(
    frames=st.lists(tables(max_rows=6, key_columns=1), min_size=4, max_size=4),
    source=st.sampled_from(["t0", "t1", "t2", "t3"]),
    values=st.sets(st.sampled_from([0, 1, 2, 3, 4]), max_size=3),
    tables_used=st.sets(st.sampled_from(["t0", "t1", "t2", "t3"]), max_size=2),
)
def test_propagation_terminates_on_dense_cycles(
    frames: list, source: str, values: set[int], tables_used: set[str]
) -> None:
    """Feature: dashboard-studio-agent, Property 31: Cakupan dan terminasi propagasi

    Graf siklus penuh (K4) dengan sisi paralel ditambah relasi non-confirmed
    ke tabel terisolasi ``t3``: propagasi berhenti dan ``t3`` tidak tersentuh.

    **Validates: Requirements 23.2, 23.3, 23.4**
    """
    names = ["t0", "t1", "t2"]
    relations: list[RelationSpec] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for dup in range(2):  # sisi paralel
                relations.append(RelationSpec(f"r{len(relations)}", a, "k0", b, "k0"))
    relations.append(RelationSpec("rc", "t0", "k0", "t3", "k0", status="candidate"))
    relations.append(RelationSpec("rr", "t2", "k0", "t3", "k0", status="rejected"))
    graph = DataGraph(
        frames={f"t{i}": df for i, df in enumerate(frames)}, relations=tuple(relations)
    )
    preds = [in_values(source, "k0", values)]
    _check_coverage(graph, preds, tables_used)
