"""Strategi Hypothesis untuk Semantic_Model, Blueprint, dan KPI (Properti 36–43)."""

from __future__ import annotations

from typing import Any

from hypothesis import strategies as st

from studio.core.models import (
    BusinessMetric,
    ColumnProfile,
    GlossaryTerm,
    NumberFormat,
    SemanticColumn,
    SemanticModel,
    VerifiedQuery,
    WorkspaceInstruction,
)
from tests.property.strategies_chart import safe_text

__all__ = [
    "METRIC_TABLES",
    "metric_exprs",
    "column_profiles",
    "number_formats",
    "semantic_columns",
    "business_metrics",
    "semantic_models",
]

#: Tabel tetap untuk validasi metrik: kolom numerik dan non-numerik.
METRIC_TABLES: dict[str, list[str]] = {
    "sales": ["amount", "qty", "cost", "region", "order_date"],
    "customers": ["customer_id", "segment", "score"],
}
_NUMERIC_COLS: dict[str, list[str]] = {
    "sales": ["amount", "qty", "cost"],
    "customers": ["customer_id", "score"],
}

_ident = st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True)
_aggs = st.sampled_from(["SUM", "AVG", "MIN", "MAX", "COUNT"])


@st.composite
def _agg_term(draw: st.DrawFn, table: str) -> str:
    col = draw(st.sampled_from(_NUMERIC_COLS[table]))
    kind = draw(st.sampled_from(["agg", "count_star", "count_distinct"]))
    if kind == "count_star":
        return "COUNT(*)"
    if kind == "count_distinct":
        return f"COUNT(DISTINCT {col})"
    return f"{draw(_aggs)}({col})"


@st.composite
def metric_exprs(draw: st.DrawFn) -> tuple[str, str, str]:
    """``(base_table, expr, expected)``; expected ∈ valid | unknown | not_agg | subquery."""
    table = draw(st.sampled_from(sorted(METRIC_TABLES)))
    expected = draw(st.sampled_from(["valid", "unknown", "not_agg", "subquery"]))
    a = draw(_agg_term(table))
    b = draw(_agg_term(table))
    if expected == "valid":
        shape = draw(st.sampled_from(["single", "diff", "ratio"]))
        if shape == "single":
            return table, a, expected
        if shape == "diff":
            return table, f"{a} - {b}", expected
        return table, f"{a} / NULLIF({b}, 0)", expected
    if expected == "unknown":
        missing = draw(_ident.filter(lambda s: s not in METRIC_TABLES[table]))
        return table, f"SUM({missing}_x)", expected
    if expected == "not_agg":
        col = draw(st.sampled_from(_NUMERIC_COLS[table]))
        shape = draw(st.sampled_from(["bare", "arith", "mixed"]))
        if shape == "bare":
            return table, col, expected
        if shape == "arith":
            return table, f"{col} + 1", expected
        return table, f"{a} + {col}", expected
    col = draw(st.sampled_from(_NUMERIC_COLS[table]))
    return table, f"(SELECT MAX({col}) FROM {table})", expected


number_formats = st.builds(
    NumberFormat,
    style=st.sampled_from(["number", "currency", "percent"]),
    currency=st.sampled_from(["IDR", "USD", None]),
    decimals=st.integers(0, 4),
    compact=st.booleans(),
)

_roles = st.sampled_from(["dimension", "measure", "time", "identifier"])
_types = st.sampled_from(["integer", "float", "string", "boolean", "date", "datetime"])


@st.composite
def column_profiles(draw: st.DrawFn, name: str | None = None) -> ColumnProfile:
    col_type = draw(_types)
    numeric = col_type in ("integer", "float")
    lo = draw(st.integers(-100, 100)) if numeric else None
    hi = (lo + draw(st.integers(0, 1000))) if numeric else None
    distinct = draw(st.integers(0, 200))
    top = draw(st.lists(st.tuples(safe_text, st.integers(1, 50)), max_size=5))
    return ColumnProfile(
        name=name or draw(_ident),
        type=col_type,
        role=draw(_roles),
        null_count=0,
        null_pct=0.0,
        distinct_count=distinct,
        min=lo,
        max=hi,
        mean=None,
        top_values=top,
    )


_short_list = st.lists(safe_text.filter(bool), max_size=3)


@st.composite
def semantic_columns(draw: st.DrawFn) -> SemanticColumn:
    return SemanticColumn(
        table=draw(_ident),
        column=draw(_ident),
        label=draw(safe_text),
        description=draw(safe_text),
        synonyms=draw(_short_list),
        default_aggregation=draw(
            st.sampled_from(["sum", "avg", "count", "count_distinct", "min", "max", "none"])
        ),
        format=draw(number_formats),
        is_enum=draw(st.booleans()),
        enum_values=draw(
            st.lists(st.one_of(st.none(), st.booleans(), st.integers(-100, 100), safe_text), max_size=4)
        ),
    )


@st.composite
def business_metrics(draw: st.DrawFn) -> BusinessMetric:
    return BusinessMetric(
        name=draw(_ident),
        label=draw(safe_text),
        description=draw(safe_text),
        expr=f"SUM({draw(_ident)})",
        base_table=draw(_ident),
        synonyms=draw(_short_list),
        format=draw(number_formats),
        good_direction=draw(st.sampled_from(["up", "down", "neutral"])),
        time_column=draw(st.none() | _ident),
    )


def _unique_by(key: Any):
    return lambda items: len({key(i) for i in items}) == len(items)


@st.composite
def semantic_models(draw: st.DrawFn) -> SemanticModel:
    """Semantic_Model valid dengan kunci kanonik unik per jenis."""
    from studio.core.semantic import column_key, instruction_key, metric_key, term_key, verified_query_key

    columns = draw(
        st.lists(semantic_columns(), max_size=4).filter(
            _unique_by(lambda c: column_key(c.table, c.column))
        )
    )
    metrics = draw(
        st.lists(business_metrics(), max_size=3).filter(_unique_by(lambda m: metric_key(m.name)))
    )
    terms = draw(
        st.lists(
            st.builds(GlossaryTerm, term=_ident, description=safe_text, synonyms=_short_list),
            max_size=3,
        ).filter(_unique_by(lambda t: term_key(t.term)))
    )
    instructions = draw(
        st.lists(
            st.builds(WorkspaceInstruction, text=safe_text.filter(lambda s: s.strip() != "")),
            max_size=2,
        ).filter(_unique_by(lambda i: instruction_key(i.text)))
    )
    vqs = draw(
        st.lists(
            st.builds(
                VerifiedQuery,
                question=safe_text,
                sql=_ident.map(lambda t: f"SELECT COUNT(*) AS n FROM {t}"),
                query_id=st.none() | _ident,
                item_id=st.none() | _ident,
            ),
            max_size=2,
        ).filter(_unique_by(lambda v: verified_query_key(v.sql)))
    )
    return SemanticModel(
        domain=draw(st.none() | _ident),
        domain_confidence=draw(st.none() | st.floats(0, 1, allow_nan=False)),
        assumptions=draw(_short_list),
        columns=columns,
        metrics=metrics,
        terms=terms,
        instructions=instructions,
        verified_queries=vqs,
    )
