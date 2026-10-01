"""Data Card: konteks tabel ringkas untuk LLM + pemilih tabel per pertanyaan (murni).

Semua dihitung dari ``ColumnProfile`` yang sudah tersimpan (tanpa membaca data):

* ``table_card``: teks per tabel. Satu baris header (jumlah baris, grain/kunci unik,
  kolom waktu utama + rentang + perkiraan granularitas), lalu satu baris per kolom
  ``nama tipe peran | statistik``. Jauh lebih hemat token daripada JSON profil.
* ``catalog_line``: satu baris per tabel (untuk Root/Architect dan tabel non-relevan).
* ``select_tables``: skor overlap token pertanyaan × nama tabel/kolom/istilah semantik
  + kecocokan nilai, ditambah tetangga Confirmed_Relation agar JOIN mungkin.
* ``value_matches``: nilai kategori (``top_values``) yang disebut di pertanyaan,
  dipetakan ke ``tabel.kolom``.

Privacy_Guard: tabel ``no_samples`` tidak menampilkan nilai contoh kolom string
(``top_values``/``min``/``max``) dan tidak ikut ``value_matches`` (Req 27.4, 27.5).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from studio.core.models import ColumnProfile
from studio.core.verified_queries import tokenize

__all__ = [
    "DEFAULT_TOP_K",
    "TableDoc",
    "catalog_line",
    "select_tables",
    "table_card",
    "value_matches",
]

#: Tabel yang mendapat Data Card lengkap per pertanyaan.
DEFAULT_TOP_K = 3
_MAX_TEXT = 40
_VALUE_HIT_WEIGHT = 3
#: Identifier cukup contoh format (mis. ``CUST-0112``), bukan daftar nilai.
_IDENTIFIER_EXAMPLES = 2


@dataclass(frozen=True)
class TableDoc:
    table: str
    row_count: int
    columns: tuple[ColumnProfile, ...]
    no_samples: bool = False
    #: Token tambahan dari model semantik (label, sinonim, metrik ber-base_table ini).
    terms: frozenset[str] = field(default_factory=frozenset)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    if isinstance(value, float):
        return f"{value:.4g}"
    text = str(value)
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def _hides_values(doc: TableDoc, col: ColumnProfile) -> bool:
    return doc.no_samples and col.type == "string"


def _key_column(doc: TableDoc) -> ColumnProfile | None:
    for c in doc.columns:
        if c.role == "identifier" and c.null_count == 0 and doc.row_count > 0 and c.distinct_count == doc.row_count:
            return c
    return None


def _time_column(doc: TableDoc) -> ColumnProfile | None:
    times = [c for c in doc.columns if c.role == "time" and c.min is not None and c.max is not None]
    return min(times, key=lambda c: c.null_count) if times else None


def _granularity(col: ColumnProfile) -> str | None:
    # ponytail: tebakan dari rentang/distinct, bukan jarak antar-nilai sebenarnya.
    # Upgrade: hitung median selisih tanggal di profiler bila tebakan sering meleset.
    try:
        lo = datetime.fromisoformat(str(col.min)).date()
        hi = datetime.fromisoformat(str(col.max)).date()
    except ValueError:
        return None
    if col.distinct_count < 2:
        return None
    step = ((hi - lo).days + 1) / col.distinct_count
    if step <= 1.5:
        return "harian"
    if step <= 8:
        return "mingguan"
    if step <= 40:
        return "bulanan"
    return "jarang"


def _header(doc: TableDoc) -> str:
    parts = [f"{_fmt(doc.row_count)} baris"]
    key = _key_column(doc)
    parts.append(f"1 baris per {key.name}" if key else "tanpa kunci unik")
    t = _time_column(doc)
    if t is not None:
        grain = _granularity(t)
        parts.append(f"waktu {t.name} {_fmt(t.min)}..{_fmt(t.max)}" + (f" ({grain})" if grain else ""))
    return f"{doc.table}: " + "; ".join(parts)


def _column_line(doc: TableDoc, col: ColumnProfile) -> str:
    stats: list[str] = []
    if col.distinct_count == doc.row_count and doc.row_count > 0:
        stats.append("unik")
    elif col.top_values and col.role in ("dimension", "identifier") and not _hides_values(doc, col):
        shown = col.top_values[:_IDENTIFIER_EXAMPLES] if col.role == "identifier" else col.top_values
        values = ", ".join(_fmt(v) for v, _ in shown)
        more = "" if col.distinct_count <= len(shown) else ", …"
        stats.append(f"{_fmt(col.distinct_count)} nilai: {values}{more}")
    else:
        stats.append(f"{_fmt(col.distinct_count)} nilai")
    if col.min is not None and col.max is not None and not _hides_values(doc, col) and col.role != "dimension":
        stats.append(f"{_fmt(col.min)}..{_fmt(col.max)}")
    if col.mean is not None:
        stats.append(f"rata {_fmt(col.mean)}")
    if col.null_pct:
        stats.append(f"null {col.null_pct:.3g}%")
    return f"- {col.name} {col.type} {col.role} | " + "; ".join(stats)


def table_card(doc: TableDoc) -> str:
    """Data Card lengkap satu tabel (header + satu baris per kolom)."""
    return "\n".join([_header(doc), *(_column_line(doc, c) for c in doc.columns)])


def catalog_line(doc: TableDoc) -> str:
    """Ringkasan satu baris: header + daftar nama kolom."""
    return f"{_header(doc)}; kolom: {', '.join(c.name for c in doc.columns)}"


# ---------------------------------------------------------------------------
# Pemilihan per pertanyaan
# ---------------------------------------------------------------------------


def _doc_terms(doc: TableDoc) -> frozenset[str]:
    terms = set(tokenize(doc.table)) | set(doc.terms)
    for c in doc.columns:
        terms |= tokenize(c.name)
    return frozenset(terms)


def value_matches(question: str, docs: Iterable[TableDoc]) -> list[str]:
    """``tabel.kolom = 'nilai'`` untuk nilai kategori yang disebut di pertanyaan."""
    # ponytail: hanya ``top_values`` profil (≤ 5 per kolom). Upgrade: indeks nilai
    # distinct (≤ 1.000/kolom) di profiler bila pengguna sering menyebut nilai langka.
    q = tokenize(question)
    out: list[str] = []
    for doc in docs:
        for col in doc.columns:
            if col.type != "string" or doc.no_samples:
                continue
            for value, _ in col.top_values:
                tokens = tokenize(str(value))
                if tokens and tokens <= q:
                    out.append(f"{doc.table}.{col.name} = '{value}'")
    return out


def select_tables(
    question: str,
    docs: Sequence[TableDoc],
    relations: Iterable[tuple[str, str]] = (),
    k: int = DEFAULT_TOP_K,
) -> list[str]:
    """Nama tabel paling relevan (≤ ``k``). Workspace kecil (≤ ``k`` tabel) → semua."""
    if len(docs) <= k:
        return [d.table for d in docs]
    q = tokenize(question)
    hits: dict[str, int] = {}
    for m in value_matches(question, docs):
        table = m.split(".", 1)[0]
        hits[table] = hits.get(table, 0) + 1
    scored = sorted(
        ((len(q & _doc_terms(d)) + _VALUE_HIT_WEIGHT * hits.get(d.table, 0), d.table) for d in docs),
        key=lambda s: (-s[0], s[1]),
    )
    picked = [t for s, t in scored if s > 0][:k]
    if not picked:
        return []
    # Tetangga Confirmed_Relation tabel teratas: JOIN yang sah ikut terlihat.
    for a, b in relations:
        if len(picked) >= k:
            break
        other = b if a == picked[0] else a if b == picked[0] else None
        if other and other not in picked:
            picked.append(other)
    return picked
