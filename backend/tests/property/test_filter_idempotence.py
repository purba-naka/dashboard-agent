"""Property 28: Idempotence filter (Req 22.6).

Menerapkan FilterSet ``F`` pada hasil penerapan ``F`` menghasilkan tabel yang
sama dengan menerapkan ``F`` sekali. Karena FilterSet adalah himpunan,
menduplikasi ``F`` (``F + F``) juga tidak mengubah hasil.
"""

from __future__ import annotations

import polars as pl
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.filters import Predicate, apply_predicates, normalize_filters
from tests.property.strategies_data import filter_sets, relation_graphs, tables


def _check_idempotent(df: pl.DataFrame, preds: list[Predicate], table: str) -> None:
    once = apply_predicates(df.lazy(), preds, table=table).collect()
    twice = apply_predicates(
        apply_predicates(df.lazy(), preds, table=table), preds, table=table
    ).collect()
    doubled = apply_predicates(df.lazy(), preds + preds, table=table).collect()

    # apply_predicates hanya memfilter (tanpa mengubah urutan), jadi hasil
    # identik termasuk urutan baris.
    assert twice.equals(once)
    assert doubled.equals(once)
    # Menerapkan ulang pada DataFrame hasil (eager → lazy) juga tidak berubah.
    assert apply_predicates(once.lazy(), preds, table=table).collect().equals(once)


@settings(deadline=None)
@given(df=tables(), data=st.data())
def test_filter_idempotence_single_table(df: pl.DataFrame, data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 28: Idempotence filter

    **Validates: Requirements 22.6**
    """
    preds = data.draw(filter_sets({"t": dict(df.schema)}, max_size=4))
    _check_idempotent(df, preds, "t")
    # Normalisasi FilterSet juga idempoten.
    normalized = normalize_filters(preds)
    assert normalize_filters(normalized) == normalized


@settings(deadline=None)
@given(data=st.data())
def test_filter_idempotence_multi_table(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 28: Idempotence filter

    FilterSet mencakup beberapa tabel; idempoten pada setiap tabel.

    **Validates: Requirements 22.6**
    """
    graph = data.draw(relation_graphs(max_tables=4))
    preds = data.draw(filter_sets(graph.schemas, max_size=6))
    for table, df in graph.frames.items():
        _check_idempotent(df, preds, table)
