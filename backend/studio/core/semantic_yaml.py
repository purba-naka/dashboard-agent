"""Ekspor/impor Semantic_Model dalam YAML (murni; Req 31.10, 31.11, 31.12).

Format mengikuti gaya semantic view Snowflake (tabel → kolom, lalu metrik,
glosarium, instruksi, dan verified query)::

    domain: retail_sales
    domain_confidence: 0.8
    assumptions: [...]
    tables:
      - name: transactions
        columns:
          - column: amt_net
            label: Pendapatan bersih
            status: confirmed
            ...
    metrics: [{name, expr, base_table, ..., status}]
    glossary: [{term, description, synonyms, status}]
    instructions: [{text, status}]
    verified_queries: [{question, sql, ..., status}]

Ekspor deterministik (entri diurutkan berdasarkan ``entry_key``, kunci dict
diurutkan). Impor hanya memakai ``yaml.safe_load`` dan memvalidasi setiap entri
dengan model Pydantic; validasi SQL metrik/verified query dilakukan pemanggil.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from studio.api.errors import StudioError
from studio.core.models import SEMANTIC_BODY_MODELS
from studio.core.semantic import entry_key

__all__ = [
    "DocEntry",
    "SemanticDoc",
    "SemanticImportInvalid",
    "export_yaml",
    "import_yaml",
]

_STATUSES = ("candidate", "confirmed", "rejected")
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("metrics", "metric"),
    ("glossary", "term"),
    ("instructions", "instruction"),
    ("verified_queries", "verified_query"),
)


@dataclass(frozen=True)
class DocEntry:
    kind: str
    status: str
    body: dict[str, Any]

    @property
    def entry_key(self) -> str:
        return entry_key(self.kind, self.body)


@dataclass(frozen=True)
class SemanticDoc:
    domain: str | None = None
    domain_confidence: float | None = None
    assumptions: tuple[str, ...] = ()
    entries: tuple[DocEntry, ...] = field(default_factory=tuple)

    def normalized(self) -> SemanticDoc:
        """Entri terurut berdasarkan ``entry_key`` (perbandingan ekuivalensi)."""
        return SemanticDoc(
            domain=self.domain,
            domain_confidence=self.domain_confidence,
            assumptions=tuple(self.assumptions),
            entries=tuple(sorted(self.entries, key=lambda e: (e.entry_key, e.kind))),
        )


class SemanticImportInvalid(StudioError):
    def __init__(self, issues: Sequence[Mapping[str, Any]]) -> None:
        super().__init__(
            "SEMANTIC_IMPORT_INVALID",
            f"Berkas Semantic_Model tidak valid ({len(issues)} masalah).",
            {"issues": [dict(i) for i in issues]},
            http_status=422,
        )


# ---------------------------------------------------------------------------
# Ekspor
# ---------------------------------------------------------------------------


def export_yaml(doc: SemanticDoc, *, name: str | None = None) -> str:
    doc = doc.normalized()
    tables: dict[str, list[dict[str, Any]]] = {}
    sections: dict[str, list[dict[str, Any]]] = {s: [] for s, _ in _SECTIONS}
    kind_to_section = {k: s for s, k in _SECTIONS}
    for entry in doc.entries:
        body = dict(entry.body)
        if entry.kind == "column":
            table = body.pop("table")
            tables.setdefault(table, []).append({**body, "status": entry.status})
        else:
            sections[kind_to_section[entry.kind]].append({**body, "status": entry.status})

    out: dict[str, Any] = {}
    if name is not None:
        out["name"] = name
    out["domain"] = doc.domain
    out["domain_confidence"] = doc.domain_confidence
    out["assumptions"] = list(doc.assumptions)
    out["tables"] = [{"name": t, "columns": cols} for t, cols in sorted(tables.items())]
    out.update(sections)
    return yaml.safe_dump(out, sort_keys=True, allow_unicode=True, default_flow_style=False)


# ---------------------------------------------------------------------------
# Impor
# ---------------------------------------------------------------------------


def _entry(
    kind: str, raw: Any, path: str, issues: list[dict[str, Any]], extra: Mapping[str, Any] | None = None
) -> DocEntry | None:
    if not isinstance(raw, Mapping):
        issues.append({"path": path, "entry_key": None, "reason": "entri harus berupa objek"})
        return None
    data = dict(raw)
    status = data.pop("status", "confirmed")
    if status not in _STATUSES:
        issues.append({"path": f"{path}.status", "entry_key": None, "reason": f"status tidak dikenal: {status!r}"})
        return None
    if extra:
        data.update(extra)
    try:
        model = SEMANTIC_BODY_MODELS[kind].model_validate(data)
    except ValidationError as exc:
        err = exc.errors(include_url=False)[0]
        loc = ".".join(str(p) for p in err.get("loc", ()))
        issues.append({"path": f"{path}.{loc}" if loc else path, "entry_key": None, "reason": err.get("msg", "tidak valid")})
        return None
    return DocEntry(kind=kind, status=status, body=model.model_dump(mode="json"))


def import_yaml(text: str) -> SemanticDoc:
    """Parse + validasi struktur. Raise ``SemanticImportInvalid`` (tanpa efek samping)."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SemanticImportInvalid([{"path": "$", "entry_key": None, "reason": f"YAML tidak valid: {exc}"}]) from exc
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise SemanticImportInvalid([{"path": "$", "entry_key": None, "reason": "dokumen harus berupa objek"}])

    issues: list[dict[str, Any]] = []
    entries: list[DocEntry] = []

    tables = data.get("tables") or []
    if not isinstance(tables, list):
        issues.append({"path": "tables", "entry_key": None, "reason": "tables harus berupa list"})
        tables = []
    for ti, table in enumerate(tables):
        if not isinstance(table, Mapping) or not isinstance(table.get("name"), str):
            issues.append({"path": f"tables[{ti}]", "entry_key": None, "reason": "tabel wajib punya name"})
            continue
        cols = table.get("columns") or []
        if not isinstance(cols, list):
            issues.append({"path": f"tables[{ti}].columns", "entry_key": None, "reason": "columns harus berupa list"})
            continue
        for ci, col in enumerate(cols):
            e = _entry("column", col, f"tables[{ti}].columns[{ci}]", issues, {"table": table["name"]})
            if e is not None:
                entries.append(e)

    for section, kind in _SECTIONS:
        items = data.get(section) or []
        if not isinstance(items, list):
            issues.append({"path": section, "entry_key": None, "reason": f"{section} harus berupa list"})
            continue
        for i, raw in enumerate(items):
            e = _entry(kind, raw, f"{section}[{i}]", issues)
            if e is not None:
                entries.append(e)

    seen: dict[str, int] = {}
    for idx, e in enumerate(entries):
        key = e.entry_key
        if key in seen:
            issues.append({"path": "$", "entry_key": key, "reason": "entri duplikat"})
        seen.setdefault(key, idx)

    assumptions = data.get("assumptions") or []
    if not isinstance(assumptions, list) or not all(isinstance(a, str) for a in assumptions):
        issues.append({"path": "assumptions", "entry_key": None, "reason": "assumptions harus list string"})
        assumptions = []
    domain = data.get("domain")
    if domain is not None and not isinstance(domain, str):
        issues.append({"path": "domain", "entry_key": None, "reason": "domain harus string"})
    confidence = data.get("domain_confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))):
        issues.append({"path": "domain_confidence", "entry_key": None, "reason": "domain_confidence harus angka"})

    if issues:
        raise SemanticImportInvalid(issues)
    return SemanticDoc(
        domain=domain,
        domain_confidence=None if confidence is None else float(confidence),
        assumptions=tuple(assumptions),
        entries=tuple(entries),
    ).normalized()
