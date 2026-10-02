"""Blok konteks semantik per agent dengan anggaran karakter (murni; Req 33.2–33.5, 33.8).

``build_semantic_block(entries, scope, budget, no_sample_tables)`` menghasilkan
teks JSON ringkas berisi entri Semantic_Model yang relevan untuk ``scope``:

* entri ``rejected`` tidak pernah dimuat; entri ``candidate`` diberi
  ``"unconfirmed": true``;
* urutan prioritas tetap: Workspace_Instruction → Business_Metric confirmed →
  Semantic_Column berdeskripsi → Business_Metric candidate → Glossary_Term →
  Semantic_Column lainnya (dalam satu tingkat: urut ``entry_key``);
* entri ditambahkan berurutan selama teks ≤ anggaran; begitu satu entri tidak
  muat, penambahan berhenti (prefiks prioritas) dan penanda ``truncated``
  ditambahkan;
* ``enum_values`` dihapus untuk tabel dengan ``privacy_no_samples``.

Verified_Query tidak termasuk di sini; Query_Agent menerimanya lewat
``core/verified_queries.find_verified``.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

__all__ = [
    "DEFAULT_SEMANTIC_BUDGET",
    "SCOPES",
    "Scope",
    "EntryLike",
    "priority_tier",
    "ordered_entries",
    "project_entry",
    "build_semantic_block",
    "SemanticBlock",
]

DEFAULT_SEMANTIC_BUDGET = 12_000
Scope = Literal["root", "query", "architect", "chart", "insight"]
SCOPES: tuple[str, ...] = ("root", "query", "architect", "chart", "insight")

#: Jenis entri yang relevan per cakupan agent (Req 33.1).
_SCOPE_KINDS: dict[str, frozenset[str]] = {
    "root": frozenset({"instruction", "metric"}),
    "query": frozenset({"instruction", "metric", "column", "term"}),
    "architect": frozenset({"instruction", "metric", "column", "term"}),
    "chart": frozenset({"metric", "column"}),
    "insight": frozenset({"metric", "column", "instruction"}),
}

_HEADER = "Model semantik Workspace (fakta bisnis; entri unconfirmed belum dikonfirmasi pengguna):\n"
_HINT = "entri lain tersedia lewat tool search_semantic"


class EntryLike(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def status(self) -> str: ...
    @property
    def entry_key(self) -> str: ...
    @property
    def body(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class SemanticBlock:
    text: str
    included_keys: tuple[str, ...]
    truncated: bool


def priority_tier(entry: EntryLike) -> int:
    kind, status = entry.kind, entry.status
    if kind == "instruction":
        return 0
    if kind == "metric":
        return 1 if status == "confirmed" else 3
    if kind == "column":
        return 2 if str(entry.body.get("description") or "").strip() else 5
    if kind == "term":
        return 4
    return 6


def ordered_entries(entries: Iterable[EntryLike], scope: str) -> list[EntryLike]:
    """Entri non-rejected yang relevan untuk ``scope``, terurut prioritas."""
    kinds = _SCOPE_KINDS.get(scope, frozenset())
    eligible = [e for e in entries if e.status != "rejected" and e.kind in kinds]
    return sorted(eligible, key=lambda e: (priority_tier(e), e.entry_key))


def _format_brief(fmt: Any) -> Any:
    if not isinstance(fmt, Mapping):
        return fmt
    return {k: fmt[k] for k in ("style", "currency", "decimals") if k in fmt}


def project_entry(entry: EntryLike, scope: str, no_sample_tables: Collection[str] = ()) -> dict[str, Any]:
    """Proyeksi ringkas satu entri sesuai cakupan agent."""
    body = dict(entry.body)
    out: dict[str, Any] = {"kind": entry.kind}
    if entry.kind == "instruction":
        out["text"] = body.get("text")
    elif entry.kind == "metric":
        keys = ["name", "label"]
        if scope in ("query", "architect", "insight"):
            keys += ["description", "expr", "base_table", "synonyms", "good_direction", "time_column"]
        for k in keys:
            if body.get(k) not in (None, "", []):
                out[k] = body[k]
        if scope != "root":
            out["format"] = _format_brief(body.get("format"))
    elif entry.kind == "column":
        keys = ["table", "column", "label"]
        if scope in ("query", "architect"):
            keys += ["description", "synonyms", "default_aggregation", "reference_value", "is_enum"]
        for k in keys:
            if body.get(k) not in (None, "", [], False):
                out[k] = body[k]
        out["format"] = _format_brief(body.get("format"))
        if scope == "query" and body.get("is_enum") and body.get("table") not in no_sample_tables:
            values = body.get("enum_values") or []
            if values:
                out["enum_values"] = list(values)
    elif entry.kind == "term":
        for k in ("term", "description", "synonyms"):
            if body.get(k) not in (None, "", []):
                out[k] = body[k]
    if entry.status == "candidate":
        out["unconfirmed"] = True
    return out


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def build_semantic_block(
    entries: Iterable[EntryLike],
    scope: str,
    budget: int = DEFAULT_SEMANTIC_BUDGET,
    no_sample_tables: Collection[str] = (),
) -> SemanticBlock:
    """Teks blok konteks semantik ≤ ``budget`` karakter (lihat docstring modul)."""
    ordered = ordered_entries(entries, scope)
    if not ordered:
        return SemanticBlock("", (), False)

    opening = _HEADER + '{"entries":['
    closing_full = "]}"
    closing_cut = '],"truncated":true,"hint":' + _dumps(_HINT) + "}"

    parts: list[str] = []
    keys: list[str] = []
    length = len(opening)
    truncated = False
    for entry in ordered:
        piece = _dumps(project_entry(entry, scope, no_sample_tables))
        extra = len(piece) + (1 if parts else 0)
        is_last = entry is ordered[-1]
        tail = closing_full if is_last else closing_cut
        if length + extra + len(tail) > budget:
            truncated = True
            break
        parts.append(piece)
        keys.append(entry.entry_key)
        length += extra
    else:
        truncated = False

    text = opening + ",".join(parts) + (closing_cut if truncated else closing_full)
    if len(text) > budget:
        # Bahkan blok kosong + penanda tidak muat.
        return SemanticBlock("", (), bool(ordered))
    return SemanticBlock(text, tuple(keys), truncated)
