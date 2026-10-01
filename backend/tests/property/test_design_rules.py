"""Feature: dashboard-studio-agent, Property 43: Determinisme review desain.

Temuan identik untuk semua permutasi urutan item, ``item_ids`` ⊆ item Dashboard,
dan ``TOO_MANY_VISUALS`` muncul tepat ketika item > 12.

**Validates: Requirements 39.2, 39.3, 39.7**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.design_rules import MAX_VISUALS, QueryInfo, review
from studio.core.models import ChartItem, DashboardContent, KpiItem, KpiSpec, NumberFormat
from tests.property.strategies import QUERY_SCHEMAS, dashboard_contents


@st.composite
def _queries(draw: st.DrawFn) -> dict[str, QueryInfo]:
    out = {}
    for qid, schema in QUERY_SCHEMAS.items():
        out[qid] = QueryInfo(
            tables_used=("sales",),
            lineage={c.name: ("sales", c.name) for c in schema},
            distinct_counts={c.name: draw(st.integers(0, 12)) for c in schema},
        )
    return out


def _permuted(content: DashboardContent, order: list[str]) -> DashboardContent:
    return DashboardContent(
        title=content.title,
        items={i: content.items[i] for i in order},
        layout={i: content.layout[i] for i in reversed(order)},
        global_filters=content.global_filters,
        brief=content.brief,
    )


# Feature: dashboard-studio-agent, Property 43: Determinisme review desain
@settings(max_examples=100)
@given(content=dashboard_contents(max_items=15), queries=_queries(), data=st.data(), with_time=st.booleans())
def test_review_deterministic(content, queries, data, with_time) -> None:
    times = {("sales", "month")} if with_time else set()
    findings = review(content, queries, times)
    order = data.draw(st.permutations(sorted(content.items)))
    assert review(_permuted(content, list(order)), queries, times) == findings
    for f in findings:
        assert set(f.item_ids) <= set(content.items)
        assert f.severity in ("info", "warning")
    assert any(f.code == "TOO_MANY_VISUALS" for f in findings) == (len(content.items) > MAX_VISUALS)


def test_specific_rules_fire() -> None:
    from studio.core.models import ChartSpec, LayoutRect

    pie = ChartItem(
        id="p",
        title="",
        spec=ChartSpec(
            query_id="q",
            chart_type="pie",
            option={"series": [{"type": "pie", "encode": {"itemName": "region", "value": "revenue"}}]},
        ),
    )
    kpi = KpiItem(id="k", title="Rev", spec=KpiSpec(query_id="q", value_column="revenue", format=NumberFormat()))
    content = DashboardContent(
        title="D",
        items={"p": pie, "k": kpi},
        layout={"p": LayoutRect(x=0, y=0, w=6, h=4), "k": LayoutRect(x=6, y=5, w=3, h=2)},
    )
    info = {"q": QueryInfo(("sales",), {"region": ("sales", "region")}, {"region": 9})}
    codes = {f.code for f in review(content, info, {("sales", "month")})}
    assert {"MISSING_TITLE", "PIE_TOO_MANY_SLICES", "KPI_NO_COMPARISON", "KPI_NOT_ON_TOP", "NO_TIME_TREND"} <= codes
