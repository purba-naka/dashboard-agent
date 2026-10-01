"""Feature: dashboard-studio-agent, Property 14: Status invalid turunan dari relasi.

**Validates: Requirements 7.8**
"""

from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given
from hypothesis import strategies as st

from studio.core.models import (
    ChartItem,
    ChartSpec,
    DashboardContent,
    EvidenceTable,
    InsightItem,
    LayoutRect,
)
from studio.core.status import QueryMeta, dashboard_item_status, item_status

_RELATIONS = [f"rel_{i}" for i in range(6)]
_QUERY_IDS = [f"q_{i}" for i in range(5)]
_DATASETS = ["ds_a", "ds_b", "ds_c"]


def _chart(item_id: str, query_id: str) -> ChartItem:
    return ChartItem(
        id=item_id,
        title=f"Chart {item_id}",
        spec=ChartSpec(query_id=query_id, chart_type="bar", option={}),
    )


def _insight(item_id: str, query_id: str, versions: dict[str, int]) -> InsightItem:
    return InsightItem(
        id=item_id,
        insight_type="trend",
        title=f"Insight {item_id}",
        text="Tidak ada angka.",
        query_id=query_id,
        sql="SELECT 1",
        evidence=EvidenceTable(columns=[], rows=[], row_count=0),
        dataset_ids=list(versions),
        computed_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        dataset_versions=versions,
    )


_relation_set = st.frozensets(st.sampled_from(_RELATIONS))
_versions = st.dictionaries(st.sampled_from(_DATASETS), st.integers(min_value=1, max_value=5))


@st.composite
def _scenario(
    draw: st.DrawFn,
) -> tuple[DashboardContent, dict[str, QueryMeta], frozenset[str], dict[str, int]]:
    """Dashboard acak + katalog query + relasi confirmed + data_version saat ini."""
    query_metas = {
        qid: QueryMeta(
            query_id=qid,
            relations_used=draw(_relation_set),
            tables_used=("t",),
            dataset_ids=("ds_a",),
        )
        for qid in _QUERY_IDS
    }
    n_items = draw(st.integers(min_value=0, max_value=8))
    items: dict = {}
    layout: dict[str, LayoutRect] = {}
    for i in range(n_items):
        item_id = f"item_{i}"
        query_id = draw(st.sampled_from(_QUERY_IDS))
        if draw(st.booleans()):
            items[item_id] = _chart(item_id, query_id)
        else:
            items[item_id] = _insight(item_id, query_id, draw(_versions))
        w = draw(st.integers(min_value=1, max_value=12))
        x = draw(st.integers(min_value=0, max_value=12 - w))
        layout[item_id] = LayoutRect(x=x, y=i * 4, w=w, h=draw(st.integers(1, 6)))
    content = DashboardContent(title="Dashboard", items=items, layout=layout)
    confirmed = draw(_relation_set)
    current_versions = draw(_versions)
    return content, query_metas, confirmed, current_versions


def _query_of(item: ChartItem | InsightItem) -> str:
    return item.query_id if isinstance(item, InsightItem) else item.spec.query_id


def _invalid_ids(statuses: dict) -> set[str]:
    return {item_id for item_id, status in statuses.items() if status.invalid}


@given(_scenario())
def test_invalid_iff_relations_not_subset_of_confirmed(
    scenario: tuple[DashboardContent, dict[str, QueryMeta], frozenset[str], dict[str, int]],
) -> None:
    """Feature: dashboard-studio-agent, Property 14: Status invalid turunan dari relasi.

    **Validates: Requirements 7.8**
    """
    content, query_metas, confirmed, versions = scenario
    statuses = dashboard_item_status(content, query_metas, confirmed, versions)

    assert set(statuses) == set(content.items)
    for item_id, item in content.items.items():
        meta = query_metas[_query_of(item)]
        expected = not meta.relations_used <= confirmed
        assert statuses[item_id].invalid == expected
        # Konsisten dengan perhitungan per item.
        assert item_status(item, meta, confirmed, versions) == statuses[item_id]


@given(_scenario(), st.data())
def test_removing_relation_invalidates_exactly_its_users(
    scenario: tuple[DashboardContent, dict[str, QueryMeta], frozenset[str], dict[str, int]],
    data: st.DataObject,
) -> None:
    """Feature: dashboard-studio-agent, Property 14: Status invalid turunan dari relasi.

    **Validates: Requirements 7.8**
    """
    content, query_metas, confirmed, versions = scenario
    removed = data.draw(st.sampled_from(_RELATIONS), label="removed_relation")

    before = _invalid_ids(dashboard_item_status(content, query_metas, confirmed, versions))
    after_statuses = dashboard_item_status(
        content, query_metas, confirmed - {removed}, versions
    )
    after = _invalid_ids(after_statuses)

    users = {
        item_id
        for item_id, item in content.items.items()
        if removed in query_metas[_query_of(item)].relations_used
    }
    assert after == before | users
    # Status stale tidak dipengaruhi perubahan relasi.
    before_stale = {
        k: s.stale
        for k, s in dashboard_item_status(content, query_metas, confirmed, versions).items()
    }
    assert {k: s.stale for k, s in after_statuses.items()} == before_stale


def test_removed_relation_example() -> None:
    """Feature: dashboard-studio-agent, Property 14: Status invalid turunan dari relasi.

    **Validates: Requirements 7.8**
    """
    metas = {
        "q_join": QueryMeta("q_join", frozenset({"rel_1"}), ("a", "b"), ("ds_a", "ds_b")),
        "q_single": QueryMeta("q_single", frozenset(), ("a",), ("ds_a",)),
    }
    content = DashboardContent(
        title="D",
        items={"c1": _chart("c1", "q_join"), "c2": _chart("c2", "q_single")},
        layout={"c1": LayoutRect(x=0, y=0, w=6, h=4), "c2": LayoutRect(x=6, y=0, w=6, h=4)},
    )
    ok = dashboard_item_status(content, metas, {"rel_1"}, {})
    assert not ok["c1"].invalid and not ok["c2"].invalid
    removed = dashboard_item_status(content, metas, set(), {})
    assert removed["c1"].invalid and not removed["c2"].invalid
