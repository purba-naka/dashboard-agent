"""Pencarian Verified_Query berbasis kemiripan kata kunci (murni; Req 34.3–34.5).

``find_verified(entries, question, k)``:

* hanya entri ``confirmed`` dan ``valid`` dengan skor > 0;
* skor = Jaccard token pertanyaan (lowercase, pisah non-alfanumerik, tanpa
  stopword id/en kecil, tanpa duplikat);
* urut skor menurun, lalu ``confirmed_at`` terbaru, lalu ``id``; ambil ``k``.

Validitas (SQL masih lolos SQL_Validator) dihitung pemanggil dengan
:func:`is_valid_sql` dan diteruskan lewat field ``valid``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from studio.api.errors import StudioError
from studio.core.sql_rules import analyze_sql

__all__ = ["STOPWORDS", "VerifiedCandidate", "tokenize", "score", "find_verified", "is_valid_sql"]

STOPWORDS: frozenset[str] = frozenset(
    {
        # id
        "yang", "dan", "di", "ke", "dari", "untuk", "per", "pada", "dengan", "apa",
        "berapa", "ini", "itu", "atau", "the", "tampilkan", "buat", "buatkan", "saya",
        # en
        "a", "an", "of", "in", "on", "by", "for", "to", "and", "or", "is", "what",
        "show", "me", "how", "many", "much",
    }
)
_SPLIT = re.compile(r"[^0-9a-z]+")


def tokenize(text: str) -> frozenset[str]:
    return frozenset(t for t in _SPLIT.split(str(text).lower()) if t and t not in STOPWORDS)


def score(question: str, candidate_question: str) -> float:
    a, b = tokenize(question), tokenize(candidate_question)
    union = a | b
    return 0.0 if not union else len(a & b) / len(union)


@dataclass(frozen=True)
class VerifiedCandidate:
    id: str
    question: str
    sql: str
    status: str
    valid: bool = True
    confirmed_at: datetime | None = None
    query_id: str | None = None
    item_id: str | None = None


def find_verified(
    entries: Iterable[VerifiedCandidate], question: str, k: int = 3
) -> list[tuple[VerifiedCandidate, float]]:
    """Paling banyak ``k`` pasangan ``(entri, skor)`` terbaik (lihat docstring modul)."""
    if k < 1:
        return []
    scored: list[tuple[VerifiedCandidate, float]] = []
    for entry in entries:
        if entry.status != "confirmed" or not entry.valid:
            continue
        s = score(question, entry.question)
        if s > 0:
            scored.append((entry, s))
    scored.sort(
        key=lambda pair: (
            -pair[1],
            -(pair[0].confirmed_at.timestamp() if pair[0].confirmed_at else float("-inf")),
            pair[0].id,
        )
    )
    return scored[:k]


def is_valid_sql(
    sql: str, tables: Mapping[str, Sequence[Any]], confirmed_relations: Iterable[Any] = ()
) -> bool:
    """``True`` bila SQL masih lolos V1–V6 terhadap tabel & relasi saat ini (Req 34.4)."""
    try:
        analyze_sql(sql, tables, confirmed_relations)
    except StudioError:
        return False
    return True
