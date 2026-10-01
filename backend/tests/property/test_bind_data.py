"""Feature: dashboard-studio-agent, Property 17: Binding data mengikat hasil query tanpa mengubah desain.

``bind_data`` menghasilkan option dengan ``dataset.dimensions`` = kolom hasil,
``dataset.source`` = baris hasil (urutan dipertahankan, date/datetime → ISO),
dan semua key option lain identik dengan spec asli. Chart pie dengan lebih dari
8 kategori ditolak ``AXIS_STRUCTURE``.

**Validates: Requirements 12.3**
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from studio.core.chart_spec import (
    PIE_MAX_CATEGORIES,
    ChartSpecError,
    bind_data,
    validate_chart_spec,
)
from studio.core.models import ColumnInfo
from tests.property.strategies_chart import chart_specs, query_results, result_schemas


def _iso(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    return value


def _pie_categories(spec: dict[str, Any], columns: list[str], rows: list[list[Any]]) -> int:
    """Jumlah kategori terbesar di antara series pie (kolom ``encode.itemName``)."""
    series = spec["option"]["series"]
    most = 0
    for s in series if isinstance(series, list) else [series]:
        idx = columns.index(s["encode"]["itemName"])
        most = max(most, len({_iso(row[idx]) for row in rows}))
    return most


@given(result_schemas(), st.data())
def test_bind_data_binds_result_without_changing_design(
    schema: list[ColumnInfo], data: st.DataObject
) -> None:
    """Feature: dashboard-studio-agent, Property 17: Binding data mengikat hasil query tanpa mengubah desain.

    **Validates: Requirements 12.3**
    """
    spec = data.draw(chart_specs(schema), label="spec")
    result = data.draw(query_results(schema, query_id=spec["query_id"]), label="result")
    model = validate_chart_spec(spec, schema)
    original_spec = copy.deepcopy(spec)
    original_rows = copy.deepcopy(result.rows)
    columns = [c.name for c in result.columns]

    too_many = (
        spec["chart_type"] == "pie"
        and _pie_categories(spec, columns, result.rows) > PIE_MAX_CATEGORIES
    )
    if too_many:
        with pytest.raises(ChartSpecError) as exc_info:
            bind_data(model, result)
        assert exc_info.value.code == "AXIS_STRUCTURE"
        return

    option = bind_data(model, result)

    assert option["dataset"] == {
        "dimensions": columns,
        "source": [[_iso(cell) for cell in row] for row in result.rows],
    }
    assert {k: v for k, v in option.items() if k != "dataset"} == original_spec["option"]
    # Input tidak dimutasi.
    assert model.option == original_spec["option"]
    assert "dataset" not in model.option
    assert result.rows == original_rows
