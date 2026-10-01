"""Feature: dashboard-studio-agent, Property 15: Validasi Chart_Spec.

Spec valid yang dibangkitkan untuk skema hasil query acak diterima; mutasi
berupa data inline, kolom tak dikenal, atau string berpola kode ditolak dengan
``INLINE_DATA``, ``UNKNOWN_COLUMN`` (menyebut nama kolom), atau ``CODE_VALUE``.

**Validates: Requirements 12.2, 13.1, 13.2, 13.3, 13.4**
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from studio.core.chart_spec import ChartSpecError, validate_chart_spec
from studio.core.models import ChartSpec, ColumnInfo
from tests.property.strategies_chart import (
    chart_specs,
    inject_code_string,
    inject_inline_data,
    inject_unknown_column,
    result_schemas,
)


@given(result_schemas(), st.data())
def test_valid_chart_spec_is_accepted(schema: list[ColumnInfo], data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 15: Validasi Chart_Spec.

    **Validates: Requirements 12.2, 13.1**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)

    model = validate_chart_spec(spec, schema)

    assert isinstance(model, ChartSpec)
    assert spec == original  # input tidak dimutasi
    assert model.chart_type == spec["chart_type"]
    assert model.query_id == spec["query_id"]
    assert model.option == spec["option"]
    assert model.cross_filter_column == spec.get("cross_filter_column")
    # Model hasil validasi juga diterima kembali (idempoten).
    assert validate_chart_spec(model, schema) == model


@given(result_schemas(), st.data())
def test_inline_data_is_rejected(schema: list[ColumnInfo], data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 15: Validasi Chart_Spec.

    **Validates: Requirements 13.2**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)
    mutated = data.draw(inject_inline_data(spec), label="mutated")
    assert spec == original

    with pytest.raises(ChartSpecError) as exc_info:
        validate_chart_spec(mutated, schema)
    assert exc_info.value.code == "INLINE_DATA"


@given(result_schemas(), st.data())
def test_unknown_column_is_rejected(schema: list[ColumnInfo], data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 15: Validasi Chart_Spec.

    **Validates: Requirements 13.3**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)
    mutated, column = data.draw(inject_unknown_column(spec, schema), label="mutated")
    assert spec == original
    assert column not in {c.name for c in schema}

    with pytest.raises(ChartSpecError) as exc_info:
        validate_chart_spec(mutated, schema)
    err = exc_info.value
    assert err.code == "UNKNOWN_COLUMN"
    assert err.details["column"] == column
    assert column in err.message


@given(result_schemas(), st.data())
def test_code_string_is_rejected(schema: list[ColumnInfo], data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 15: Validasi Chart_Spec.

    **Validates: Requirements 13.4**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    original = copy.deepcopy(spec)
    mutated = data.draw(inject_code_string(spec), label="mutated")
    assert spec == original

    with pytest.raises(ChartSpecError) as exc_info:
        validate_chart_spec(mutated, schema)
    assert exc_info.value.code == "CODE_VALUE"
