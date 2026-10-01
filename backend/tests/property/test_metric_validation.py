"""Feature: dashboard-studio-agent, Property 36: Validasi ekspresi Business_Metric.

Ekspresi metrik diterima jika dan hanya jika agregat, hanya merujuk kolom tabel
dasar, dan probe ``SELECT <expr> AS value FROM <tabel>`` lolos SQL_Validator;
ekspresi yang ditolak menghasilkan kode error dan penyebabnya.

**Validates: Requirements 31.5, 31.6**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings

from studio.core.models import BusinessMetric
from studio.core.semantic import MetricError, validate_metric_static
from tests.property.strategies_semantic import METRIC_TABLES, metric_exprs


# Feature: dashboard-studio-agent, Property 36: Validasi ekspresi Business_Metric
@settings(max_examples=100)
@given(case=metric_exprs())
def test_metric_expr_accepted_iff_valid(case: tuple[str, str, str]) -> None:
    table, expr, expected = case
    metric = BusinessMetric(name="m", expr=expr, base_table=table)
    if expected == "valid":
        probe = validate_metric_static(metric, METRIC_TABLES)
        assert probe.startswith("SELECT ") and expr in probe
        return
    with pytest.raises(MetricError) as info:
        validate_metric_static(metric, METRIC_TABLES)
    err = info.value
    if expected == "not_agg":
        assert err.code == "METRIC_NOT_AGGREGATE"
    else:
        assert err.code == "METRIC_INVALID"
    if expected == "unknown":
        assert err.details["column"].endswith("_x")
    assert err.details["reason"]


def test_unknown_base_table_rejected() -> None:
    with pytest.raises(MetricError) as info:
        validate_metric_static(BusinessMetric(name="m", expr="SUM(a)", base_table="nope"), METRIC_TABLES)
    assert info.value.details["table"] == "nope"


def test_foreign_table_column_rejected() -> None:
    metric = BusinessMetric(name="m", expr="SUM(customers.score)", base_table="sales")
    with pytest.raises(MetricError) as info:
        validate_metric_static(metric, METRIC_TABLES)
    assert info.value.code == "METRIC_INVALID"
