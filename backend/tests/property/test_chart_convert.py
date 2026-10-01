"""Feature: dashboard-studio-agent, Property 18: Konversi tipe chart menghasilkan spec valid.

Untuk Chart_Spec valid dan tipe target yang didukung, ``convert_chart_type``
menghasilkan Chart_Spec yang lolos Chart_Spec_Validator dengan ``chart_type``
dan setiap ``series[].type`` sama dengan tipe target, atau error
``INCOMPATIBLE_TYPE`` bila skema tidak memenuhi syarat; tidak pernah spec tidak
valid.

**Validates: Requirements 17.3**
"""

from __future__ import annotations

import copy

from hypothesis import given
from hypothesis import strategies as st

from studio.core.chart_convert import NUMERIC_TYPES, convert_chart_type
from studio.core.chart_spec import ChartSpecError, validate_chart_spec
from studio.core.models import ColumnInfo
from tests.property.strategies_chart import ALL_CHART_TYPES, chart_specs, result_schemas


@given(
    result_schemas(min_measures=1, max_dimensions=3, max_measures=3),
    st.sampled_from(ALL_CHART_TYPES),
    st.data(),
)
def test_convert_chart_type_yields_valid_spec_or_incompatible(
    schema: list[ColumnInfo], target: str, data: st.DataObject
) -> None:
    """Feature: dashboard-studio-agent, Property 18: Konversi tipe chart menghasilkan spec valid.

    **Validates: Requirements 17.3**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)
    numeric = [c for c in schema if c.type in NUMERIC_TYPES]

    try:
        converted = convert_chart_type(spec, target, schema)
    except ChartSpecError as exc:
        assert exc.code == "INCOMPATIBLE_TYPE", exc.message
        # bar/line/pie selalu dapat dibentuk dari spec valid (kategori + nilai);
        # scatter hanya gagal bila measure numerik < 2.
        assert target in ("scatter", "heatmap")
        if target == "scatter":
            assert len(numeric) < 2
        assert spec == original
        return

    assert spec == original  # input tidak dimutasi
    revalidated = validate_chart_spec(converted, schema)
    assert revalidated == converted
    assert converted.chart_type == target
    series = converted.option["series"]
    series_list = series if isinstance(series, list) else [series]
    assert series_list
    assert all(s["type"] == target for s in series_list)
    assert converted.query_id == spec["query_id"]
