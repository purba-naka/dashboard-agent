"""Feature: dashboard-studio-agent, Property 16: Round-trip Chart_Spec.

Chart_Spec valid yang diserialisasi ke JSON, di-parse kembali, lalu divalidasi
menghasilkan Chart_Spec yang ekuivalen dengan aslinya dan tetap valid.

**Validates: Requirements 13.6**
"""

from __future__ import annotations

import copy
import json

from hypothesis import given
from hypothesis import strategies as st

from studio.core.chart_spec import validate_chart_spec
from studio.core.models import ChartSpec, ColumnInfo
from tests.property.strategies_chart import chart_specs, result_schemas


@given(result_schemas(), st.data())
def test_chart_spec_json_round_trip(schema: list[ColumnInfo], data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 16: Round-trip Chart_Spec.

    **Validates: Requirements 13.6**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)
    model = validate_chart_spec(spec, schema)

    # dict → JSON → dict → validasi
    parsed = json.loads(json.dumps(spec))
    from_json = validate_chart_spec(parsed, schema)
    assert from_json == model
    assert from_json.model_dump(mode="json") == {"cross_filter_column": None, **original}

    # ChartSpec → JSON (Pydantic) → ChartSpec → validasi
    reparsed = ChartSpec.model_validate_json(model.model_dump_json())
    assert reparsed == model
    assert validate_chart_spec(reparsed, schema) == model

    assert spec == original  # input tidak dimutasi
