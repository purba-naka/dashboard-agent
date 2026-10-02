"""Kolom indeks (NTP) di-draft sebagai AVG dengan acuan 100, bukan SUM."""

from studio.core.models import ColumnProfile
from studio.core.semantic import DatasetInput, heuristic_draft


def _col(name: str) -> ColumnProfile:
    return ColumnProfile(
        name=name, type="float", role="measure", null_count=0, null_pct=0.0,
        distinct_count=100, min=86.0, max=228.0,
    )


def test_index_column_avg_with_reference():
    entries = heuristic_draft([DatasetInput(dataset_id="d", table="t", columns=(_col("ntp"), _col("omzet")))])
    by_key = {e.entry_key: e.body for e in entries}
    assert by_key["col:t.ntp"]["default_aggregation"] == "avg"
    assert by_key["col:t.ntp"]["reference_value"] == 100.0
    assert by_key["metric:rata_ntp"]["expr"] == 'AVG("ntp")'
    assert by_key["col:t.omzet"]["default_aggregation"] == "sum"
    assert by_key["col:t.omzet"]["reference_value"] is None
    assert by_key["metric:total_omzet"]["expr"] == 'SUM("omzet")'
