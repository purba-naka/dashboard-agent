"""Feature: dashboard-studio-agent, Property 34: Perbandingan skema re-upload.

**Validates: Requirements 26.3, 26.4**
"""

from __future__ import annotations

from typing import get_args

from hypothesis import given
from hypothesis import strategies as st

from studio.core.models import ColumnInfo, LogicalType
from studio.core.schema_diff import diff_schemas, types_compatible

_TYPES: tuple[str, ...] = get_args(LogicalType)
_NUMERIC = {"integer", "float"}

_logical_type = st.sampled_from(_TYPES)
# Pool nama kecil agar kedua skema sering beririsan.
_names = st.sampled_from(["id", "tanggal", "region", "sales", "qty", "harga", "kategori", "x"])
_schemas = st.lists(
    st.tuples(_names, _logical_type), unique_by=lambda t: t[0], max_size=8
).map(lambda cols: [ColumnInfo(name=n, type=t) for n, t in cols])


def _compatible_oracle(a: str, b: str) -> bool:
    return a == b or (a in _NUMERIC and b in _NUMERIC)


@given(_logical_type, _logical_type)
def test_types_compatible_matches_rule(a: str, b: str) -> None:
    """Feature: dashboard-studio-agent, Property 34: Perbandingan skema re-upload.

    **Validates: Requirements 26.3, 26.4**
    """
    assert types_compatible(a, b) == _compatible_oracle(a, b)
    assert types_compatible(a, b) == types_compatible(b, a)


@given(_schemas, st.one_of(_schemas, st.none()))
def test_diff_schemas_reports_exact_differences(
    old: list[ColumnInfo], new: list[ColumnInfo] | None
) -> None:
    """Feature: dashboard-studio-agent, Property 34: Perbandingan skema re-upload.

    **Validates: Requirements 26.3, 26.4**
    """
    if new is None:  # sering uji skema identik
        new = list(old)
    diff = diff_schemas(old, new)

    old_map = {c.name: c.type for c in old}
    new_map = {c.name: c.type for c in new}
    expected_missing = [n for n in old_map if n not in new_map]
    expected_added = [n for n in new_map if n not in old_map]
    expected_changed = [
        {"name": n, "old_type": t, "new_type": new_map[n]}
        for n, t in old_map.items()
        if n in new_map and not _compatible_oracle(t, new_map[n])
    ]

    assert list(diff.missing) == expected_missing
    assert list(diff.added) == expected_added
    assert [c.as_dict() for c in diff.changed] == expected_changed

    same = set(old_map) == set(new_map) and all(
        _compatible_oracle(old_map[n], new_map[n]) for n in old_map
    )
    assert diff.is_empty == same
    assert diff.as_details() == {
        "missing": expected_missing,
        "added": expected_added,
        "changed": expected_changed,
    }
