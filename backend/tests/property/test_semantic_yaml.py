"""Feature: dashboard-studio-agent, Property 37: Round-trip YAML Semantic_Model.

``import_yaml(export_yaml(m))`` ekuivalen dengan ``m`` dan ekspor deterministik.

**Validates: Requirements 31.10, 31.12**
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.models import SemanticModel
from studio.core.semantic_yaml import DocEntry, SemanticDoc, SemanticImportInvalid, export_yaml, import_yaml
from tests.property.strategies_semantic import semantic_models

_STATUSES = st.sampled_from(["candidate", "confirmed", "rejected"])


@st.composite
def _docs(draw: st.DrawFn) -> SemanticDoc:
    model: SemanticModel = draw(semantic_models())
    entries = []
    for kind, items in (
        ("column", model.columns),
        ("metric", model.metrics),
        ("term", model.terms),
        ("instruction", model.instructions),
        ("verified_query", model.verified_queries),
    ):
        for item in items:
            entries.append(DocEntry(kind=kind, status=draw(_STATUSES), body=item.model_dump(mode="json")))
    return SemanticDoc(
        domain=model.domain,
        domain_confidence=model.domain_confidence,
        assumptions=tuple(model.assumptions),
        entries=tuple(draw(st.permutations(entries))),
    )


# Feature: dashboard-studio-agent, Property 37: Round-trip YAML Semantic_Model
@settings(max_examples=100)
@given(doc=_docs())
def test_yaml_round_trip(doc: SemanticDoc) -> None:
    text = export_yaml(doc)
    assert import_yaml(text) == doc.normalized()
    # Model ekuivalen (urutan entri berbeda) → teks identik.
    reordered = SemanticDoc(doc.domain, doc.domain_confidence, doc.assumptions, tuple(reversed(doc.entries)))
    assert export_yaml(reordered) == text


def test_invalid_import_reports_every_issue() -> None:
    text = """
metrics:
  - name: revenue
    base_table: sales
    status: confirmed
  - name: margin
    expr: SUM(a)
    base_table: sales
    status: maybe
glossary: "bukan list"
"""
    with pytest.raises(SemanticImportInvalid) as info:
        import_yaml(text)
    paths = [i["path"] for i in info.value.details["issues"]]
    assert any(p.startswith("metrics[0]") for p in paths)
    assert "metrics[1].status" in paths
    assert "glossary" in paths


def test_unsafe_yaml_rejected() -> None:
    with pytest.raises(SemanticImportInvalid):
        import_yaml("!!python/object/apply:os.system ['echo x']")
