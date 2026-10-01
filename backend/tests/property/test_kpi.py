"""Feature: dashboard-studio-agent, Property 42: Perhitungan dan format KPI_Card.

``delta = value − comparison``, ``delta_pct = delta / |comparison| × 100`` bila
pembanding ≠ 0, sentimen sesuai arah nilai yang baik, string terformat dapat
dibaca kembali oleh ``extract_numbers``, dan hasil ≠ 1 baris → ``KPI_SHAPE``.

**Validates: Requirements 38.4, 38.5, 38.9, 38.10**
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.kpi import KpiShapeError, compute_kpi, format_kpi_number
from studio.core.models import ColumnInfo, KpiSpec, NumberFormat, QueryResult
from studio.core.numbers import extract_numbers, interpretation_matches
from tests.property.strategies_semantic import number_formats

_values = st.one_of(
    st.none(),
    st.just(0.0),
    st.integers(-10**13, 10**13).map(float),
    st.floats(-1e12, 1e12, allow_nan=False, allow_infinity=False),
)
_COLUMNS = [ColumnInfo(name="v", type="float"), ColumnInfo(name="c", type="float")]


def _spec(fmt: NumberFormat, direction: str) -> KpiSpec:
    return KpiSpec(query_id="q", value_column="v", comparison_column="c", format=fmt, good_direction=direction)  # type: ignore[arg-type]


# Feature: dashboard-studio-agent, Property 42: Perhitungan dan format KPI_Card
@settings(max_examples=100)
@given(
    value=_values,
    comparison=_values,
    fmt=number_formats,
    direction=st.sampled_from(["up", "down", "neutral"]),
)
def test_kpi_delta_sentiment_and_format(value, comparison, fmt, direction) -> None:
    out = compute_kpi(_spec(fmt, direction), QueryResult(query_id="q", columns=_COLUMNS, rows=[[value, comparison]], row_count=1))
    if value is None or comparison is None:
        assert out.delta is None and out.delta_pct is None and out.sentiment == "neutral"
    else:
        assert out.delta == value - comparison
        pct = None if comparison == 0 else out.delta / abs(comparison) * 100
        if pct is None or not math.isfinite(pct):
            assert out.delta_pct is None
        else:
            assert out.delta_pct == pytest.approx(pct)
        if direction == "neutral" or out.delta == 0:
            assert out.sentiment == "neutral"
        else:
            good = (out.delta > 0) == (direction == "up")
            assert out.sentiment == ("positive" if good else "negative")

    if value is not None:
        text = out.formatted["value"]
        assert text == format_kpi_number(value, fmt)
        tokens = extract_numbers(text)
        assert tokens, text
        target = Decimal(repr(value))
        assert any(interpretation_matches(i, target) for t in tokens for i in t.interpretations), text


@given(rows=st.sampled_from([0, 2, 3]))
def test_kpi_shape(rows: int) -> None:
    result = QueryResult(query_id="q", columns=_COLUMNS, rows=[[1.0, 2.0]] * rows, row_count=rows)
    with pytest.raises(KpiShapeError):
        compute_kpi(_spec(NumberFormat(), "up"), result)
