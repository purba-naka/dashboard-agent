"""Feature: dashboard-studio-agent, Property 38: Draft semantik deterministik dan menghormati keputusan pengguna.

``merge_draft(existing, heuristic_draft(...), rejected)`` identik untuk masukan
yang sama; tidak memuat kunci yang ditolak; tidak mengubah entri ``confirmed``
atau bersumber ``user``; dan agregasi default mengikuti peran kolom.

**Validates: Requirements 31.8, 32.2, 32.5, 32.6**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.semantic import (
    DatasetInput,
    ExistingEntry,
    default_aggregation,
    heuristic_draft,
    merge_draft,
)
from tests.property.strategies_semantic import column_profiles

_ROLE_AGG = {"measure": "sum", "identifier": "count_distinct", "dimension": "none", "time": "none"}


@st.composite
def _datasets(draw: st.DrawFn) -> list[DatasetInput]:
    tables = draw(st.lists(st.from_regex(r"t[a-z]{1,5}", fullmatch=True), min_size=1, max_size=3, unique=True))
    out = []
    for i, table in enumerate(tables):
        names = draw(st.lists(st.from_regex(r"c[a-z_]{0,6}", fullmatch=True), min_size=1, max_size=5, unique=True))
        cols = tuple(draw(column_profiles(name=n)) for n in names)
        out.append(DatasetInput(dataset_id=f"ds{i}", table=table, columns=cols))
    return out


@st.composite
def _scenario(draw: st.DrawFn):
    datasets = draw(_datasets())
    draft = heuristic_draft(datasets)
    keys = sorted({d.entry_key for d in draft})
    existing = [
        ExistingEntry(
            entry_key=k,
            status=draw(st.sampled_from(["candidate", "confirmed", "rejected"])),
            source=draw(st.sampled_from(["auto", "user"])),
        )
        for k in keys
        if draw(st.booleans())
    ]
    rejected = set(draw(st.lists(st.sampled_from(keys), max_size=len(keys)))) if keys else set()
    return datasets, existing, rejected


# Feature: dashboard-studio-agent, Property 38: Draft semantik deterministik dan menghormati keputusan pengguna
@settings(max_examples=100)
@given(scenario=_scenario())
def test_merge_draft_respects_user_decisions(scenario) -> None:
    datasets, existing, rejected = scenario
    draft = heuristic_draft(datasets)
    assert heuristic_draft(datasets) == draft  # deterministik

    merged = merge_draft(existing, draft, rejected)
    assert merged == merge_draft(existing, heuristic_draft(datasets), rejected)

    blocked = set(rejected) | {
        e.entry_key for e in existing if e.status in ("confirmed", "rejected") or e.source == "user"
    }
    merged_keys = [m.entry_key for m in merged]
    assert merged_keys == sorted(set(merged_keys))  # unik & terurut
    assert not blocked & set(merged_keys)
    # Semua kunci draft yang tidak diblokir ikut ditulis (sebagai candidate oleh pemanggil).
    assert set(merged_keys) == {d.entry_key for d in draft} - blocked

    roles = {(ds.table, c.name): c.role for ds in datasets for c in ds.columns}
    for entry in draft:
        if entry.kind == "column":
            role = roles[(entry.body["table"], entry.body["column"])]
            assert entry.body["default_aggregation"] == _ROLE_AGG[role] == default_aggregation(role)
            if entry.body["is_enum"]:
                assert role == "dimension"
