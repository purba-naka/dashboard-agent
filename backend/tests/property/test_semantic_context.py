"""Feature: dashboard-studio-agent, Property 39: Anggaran dan prioritas blok konteks semantik.

Blok ≤ anggaran, tanpa entri ``rejected``, entri ``candidate`` ditandai, tanpa
enum untuk tabel privasi, dan entri yang dimuat adalah prefiks urutan prioritas.

**Validates: Requirements 33.2, 33.3, 33.4, 33.5, 33.8**
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.semantic import entry_key
from studio.core.semantic_context import SCOPES, build_semantic_block, ordered_entries
from tests.property.strategies_semantic import business_metrics, semantic_columns
from tests.property.strategies_chart import safe_text


@dataclass(frozen=True)
class _Entry:
    kind: str
    status: str
    entry_key: str
    body: dict[str, Any]


_STATUS = st.sampled_from(["candidate", "confirmed", "rejected"])


@st.composite
def _entries(draw: st.DrawFn) -> list[_Entry]:
    out: dict[str, _Entry] = {}
    for col in draw(st.lists(semantic_columns(), max_size=8)):
        body = col.model_dump(mode="json")
        out.setdefault(entry_key("column", body), _Entry("column", draw(_STATUS), entry_key("column", body), body))
    for m in draw(st.lists(business_metrics(), max_size=6)):
        body = m.model_dump(mode="json")
        out.setdefault(entry_key("metric", body), _Entry("metric", draw(_STATUS), entry_key("metric", body), body))
    for text in draw(st.lists(safe_text.filter(lambda s: s.strip() != ""), max_size=3)):
        body = {"text": text}
        out.setdefault(entry_key("instruction", body), _Entry("instruction", draw(_STATUS), entry_key("instruction", body), body))
    for term in draw(st.lists(st.from_regex(r"[a-z]{2,8}", fullmatch=True), max_size=3)):
        body = {"term": term, "description": "d", "synonyms": []}
        out.setdefault(entry_key("term", body), _Entry("term", draw(_STATUS), entry_key("term", body), body))
    return list(draw(st.permutations(list(out.values()))))


# Feature: dashboard-studio-agent, Property 39: Anggaran dan prioritas blok konteks semantik
@settings(max_examples=100)
@given(
    entries=_entries(),
    scope=st.sampled_from(SCOPES),
    budget=st.integers(0, 3000),
    private=st.booleans(),
)
def test_semantic_block_budget_and_priority(entries, scope, budget, private) -> None:
    no_sample = {e.body["table"] for e in entries if e.kind == "column"} if private else set()
    block = build_semantic_block(entries, scope, budget, no_sample)
    assert len(block.text) <= budget

    ordered = [e.entry_key for e in ordered_entries(entries, scope)]
    assert list(block.included_keys) == ordered[: len(block.included_keys)]
    rejected = {e.entry_key for e in entries if e.status == "rejected"}
    assert not rejected & set(block.included_keys)
    if block.truncated is False and block.text:
        assert len(block.included_keys) == len(ordered)

    if block.text:
        payload = json.loads(block.text.split("\n", 1)[1])
        by_key = {e.entry_key: e for e in entries}
        for key, item in zip(block.included_keys, payload["entries"]):
            assert item.get("unconfirmed", False) == (by_key[key].status == "candidate")
            if private:
                assert "enum_values" not in item
