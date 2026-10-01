"""Deteksi Relation_Candidate antar-Dataset (Req 7.1, 7.2, 7.5; Property 10).

Modul murni tanpa I/O. Overlap nilai dihitung oleh Data_Engine dan
diinjeksikan sebagai ``overlap_fn`` sehingga logika penilaian dapat diuji
tanpa Polars/DuckDB.

Aturan (design "Profiling & relasi"):

- Pasangan kolom **lintas tabel** dengan tipe kompatibel untuk kunci relasi:
  tipe identik di antara ``integer``, ``string``, ``date``
  (``integer``↔``integer``, ``string``↔``string``, ``date``↔``date``).
- Nama cocok (``names_match``): nama kolom sama setelah normalisasi, **atau**
  pola ``{tabel}_id ↔ id`` (kolom ``{tabel_B}_id`` di tabel A ↔ kolom ``id`` di
  tabel B, termasuk bentuk tunggal nama tabel tanpa akhiran ``s``).
- ``overlap_pct = |distinct(A) ∩ distinct(B)| / min(|distinct(A)|, |distinct(B)|) × 100``
  (nilai null diabaikan); kandidat diusulkan bila ``overlap_pct ≥ 50``.
- Kardinalitas: kedua sisi unik → ``one_to_one``; tepat satu sisi unik →
  ``one_to_many`` (sisi unik menjadi ``from``); selainnya ``many_to_many``.
- Pasangan dengan ``candidate_key`` (pasangan tak berurut kanonik) yang ada di
  ``rejected`` dikecualikan dan ``overlap_fn`` tidak dipanggil untuknya.

Pemakaian dari konteks async (Data_Engine ``overlap`` bersifat async)::

    pairs = candidate_pairs(tables, profiles, rejected)
    stats = {candidate_key(a, b): await engine.overlap(a, b) for a, b in pairs}
    found = score_candidates(tables, profiles, rejected,
                             lambda a, b: stats[candidate_key(a, b)])
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from studio.core.models import ColumnInfo, ColumnProfile, LogicalType
from studio.core.schema_diff import types_compatible

__all__ = [
    "Cardinality",
    "KEY_TYPES",
    "MIN_OVERLAP_PCT",
    "ColumnRef",
    "TableInfo",
    "OverlapStats",
    "RelationCandidate",
    "OverlapFn",
    "normalize_name",
    "names_match",
    "key_types_compatible",
    "candidate_key",
    "cardinality",
    "compute_overlap_stats",
    "candidate_pairs",
    "score_candidates",
]

Cardinality = Literal["one_to_one", "one_to_many", "many_to_many"]

#: Tipe logis yang layak menjadi kunci relasi.
KEY_TYPES: frozenset[str] = frozenset({"integer", "string", "date"})

#: Ambang minimal persentase overlap nilai untuk diusulkan.
MIN_OVERLAP_PCT = 50


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class ColumnRef:
    """Referensi kolom ``table.column`` (nama tabel SQL terdaftar di Workspace)."""

    table: str
    column: str

    def __str__(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True, slots=True)
class TableInfo:
    """Tabel Workspace yang dipertimbangkan: dataset, nama tabel SQL, skema."""

    dataset_id: str
    table: str
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))


@dataclass(frozen=True, slots=True)
class OverlapStats:
    """Statistik overlap nilai non-null dua kolom (dihitung Data_Engine).

    ``*_a`` merujuk argumen pertama ``overlap_fn``, ``*_b`` argumen kedua.
    ``unique_*`` benar bila kolom tidak memiliki nilai non-null duplikat.
    """

    distinct_a: int
    distinct_b: int
    intersection: int
    unique_a: bool
    unique_b: bool

    def __post_init__(self) -> None:
        if min(self.distinct_a, self.distinct_b, self.intersection) < 0:
            raise ValueError("jumlah distinct/intersection tidak boleh negatif")
        if self.intersection > min(self.distinct_a, self.distinct_b):
            raise ValueError("intersection tidak boleh melebihi min(distinct_a, distinct_b)")

    @property
    def overlap_pct(self) -> float:
        denom = min(self.distinct_a, self.distinct_b)
        return 0.0 if denom == 0 else self.intersection * 100.0 / denom

    @property
    def meets_threshold(self) -> bool:
        """``overlap_pct ≥ 50`` dihitung dengan bilangan bulat (tanpa galat float)."""
        denom = min(self.distinct_a, self.distinct_b)
        return denom > 0 and self.intersection * 100 >= MIN_OVERLAP_PCT * denom

    def swapped(self) -> OverlapStats:
        return OverlapStats(
            distinct_a=self.distinct_b,
            distinct_b=self.distinct_a,
            intersection=self.intersection,
            unique_a=self.unique_b,
            unique_b=self.unique_a,
        )


@dataclass(frozen=True, slots=True)
class RelationCandidate:
    """Relation_Candidate (Req 7.2); field sejajar kolom tabel ``relations``."""

    candidate_key: str
    from_dataset_id: str
    from_table: str
    from_column: str
    to_dataset_id: str
    to_table: str
    to_column: str
    cardinality: Cardinality
    overlap_pct: float

    @property
    def from_ref(self) -> ColumnRef:
        return ColumnRef(self.from_table, self.from_column)

    @property
    def to_ref(self) -> ColumnRef:
        return ColumnRef(self.to_table, self.to_column)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "from_dataset_id": self.from_dataset_id,
            "from_table": self.from_table,
            "from_column": self.from_column,
            "to_dataset_id": self.to_dataset_id,
            "to_table": self.to_table,
            "to_column": self.to_column,
            "cardinality": self.cardinality,
            "overlap_pct": self.overlap_pct,
        }


OverlapFn = Callable[[ColumnRef, ColumnRef], OverlapStats]


# ---------------------------------------------------------------------------
# Aturan dasar
# ---------------------------------------------------------------------------

_CAMEL_1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_2 = re.compile(r"([a-z0-9])([A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Normalisasi nama kolom/tabel ke snake_case ASCII huruf kecil.

    ``"Customer ID"``, ``"customerId"``, ``"CustomerID"``, ``" customer-id "``
    → ``"customer_id"``.
    """
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    text = _CAMEL_1.sub(r"\1_\2", text)
    text = _CAMEL_2.sub(r"\1_\2", text)
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


def _table_id_forms(table: str) -> set[str]:
    """Bentuk ``{tabel}_id`` yang dikenali untuk tabel (termasuk bentuk tunggal)."""
    base = normalize_name(table)
    forms = {f"{base}_id"} if base else set()
    if len(base) > 1 and base.endswith("s"):
        forms.add(f"{base[:-1]}_id")
    return forms


def _fk_to_id(fk_table: str, fk_column: str, pk_table: str, pk_column: str) -> bool:
    del fk_table  # hanya tabel tujuan (pemilik ``id``) yang menentukan pola
    return normalize_name(pk_column) == "id" and normalize_name(fk_column) in _table_id_forms(
        pk_table
    )


def names_match(a: ColumnRef, b: ColumnRef) -> bool:
    """Aturan kecocokan nama: sama setelah normalisasi atau pola ``{tabel}_id ↔ id``."""
    norm_a, norm_b = normalize_name(a.column), normalize_name(b.column)
    if norm_a and norm_a == norm_b:
        return True
    return _fk_to_id(a.table, a.column, b.table, b.column) or _fk_to_id(
        b.table, b.column, a.table, a.column
    )


def key_types_compatible(a: LogicalType, b: LogicalType) -> bool:
    """Tipe kompatibel untuk kunci relasi: kompatibel dan keduanya ``KEY_TYPES``."""
    return a in KEY_TYPES and b in KEY_TYPES and types_compatible(a, b)


def _ordered(a: ColumnRef, b: ColumnRef) -> tuple[ColumnRef, ColumnRef]:
    return (a, b) if str(a) <= str(b) else (b, a)


def candidate_key(a: ColumnRef, b: ColumnRef) -> str:
    """Kunci kanonik pasangan tak berurut: ``"tA.cA|tB.cB"`` urut leksikografis.

    ``candidate_key(a, b) == candidate_key(b, a)``.
    """
    first, second = _ordered(a, b)
    return f"{first}|{second}"


def cardinality(unique_a: bool, unique_b: bool) -> Cardinality:
    """Kedua sisi unik → one-to-one; satu sisi unik → one-to-many; selainnya many-to-many."""
    if unique_a and unique_b:
        return "one_to_one"
    if unique_a or unique_b:
        return "one_to_many"
    return "many_to_many"


def compute_overlap_stats(values_a: Iterable[Any], values_b: Iterable[Any]) -> OverlapStats:
    """Perhitungan referensi berbasis himpunan (nilai ``None`` diabaikan)."""
    list_a = [v for v in values_a if v is not None]
    list_b = [v for v in values_b if v is not None]
    set_a, set_b = set(list_a), set(list_b)
    return OverlapStats(
        distinct_a=len(set_a),
        distinct_b=len(set_b),
        intersection=len(set_a & set_b),
        unique_a=len(set_a) == len(list_a),
        unique_b=len(set_b) == len(list_b),
    )


# ---------------------------------------------------------------------------
# Penilaian kandidat
# ---------------------------------------------------------------------------


def _profile_map(
    table: TableInfo, profiles: Mapping[str, Sequence[ColumnProfile]]
) -> dict[str, ColumnProfile]:
    entries = profiles.get(table.dataset_id)
    if entries is None:
        entries = profiles.get(table.table, ())
    return {p.name: p for p in entries}


def _has_values(profile: ColumnProfile | None) -> bool:
    """Kolom tanpa nilai non-null tidak mungkin overlap; profil tak ada → anggap ada."""
    if profile is None:
        return True
    return profile.distinct_count > 0 and profile.null_pct < 100.0


def candidate_pairs(
    tables: Sequence[TableInfo],
    profiles: Mapping[str, Sequence[ColumnProfile]],
    rejected: Collection[str] = frozenset(),
) -> list[tuple[ColumnRef, ColumnRef]]:
    """Pasangan kolom lintas tabel yang lolos aturan tipe & nama dan tidak ditolak.

    Setiap pasangan dikembalikan sekali dalam urutan kanonik (``_ordered``),
    diurutkan menurut ``candidate_key``. ``profiles`` dikunci ``dataset_id``
    (atau nama tabel); kolom yang seluruhnya null dilewati.
    """
    rejected_keys = frozenset(rejected)
    columns: list[tuple[int, ColumnRef, LogicalType]] = []
    for idx, table in enumerate(tables):
        pmap = _profile_map(table, profiles)
        for col in table.columns:
            if col.type in KEY_TYPES and _has_values(pmap.get(col.name)):
                columns.append((idx, ColumnRef(table.table, col.name), col.type))

    seen: dict[str, tuple[ColumnRef, ColumnRef]] = {}
    for i, (ti, ref_a, type_a) in enumerate(columns):
        for tj, ref_b, type_b in columns[i + 1 :]:
            if ti == tj or ref_a.table == ref_b.table:
                continue
            if not key_types_compatible(type_a, type_b) or not names_match(ref_a, ref_b):
                continue
            key = candidate_key(ref_a, ref_b)
            if key in rejected_keys or key in seen:
                continue
            seen[key] = _ordered(ref_a, ref_b)
    return [seen[k] for k in sorted(seen)]


def score_candidates(
    tables: Sequence[TableInfo],
    profiles: Mapping[str, Sequence[ColumnProfile]],
    rejected: Collection[str],
    overlap_fn: OverlapFn,
) -> list[RelationCandidate]:
    """Hasilkan Relation_Candidate dengan ``overlap_pct ≥ 50`` (urut ``candidate_key``).

    ``overlap_fn(a, b)`` dipanggil sekali per pasangan dari ``candidate_pairs``
    dengan ``a``, ``b`` dalam urutan kanonik.
    """
    dataset_of = {t.table: t.dataset_id for t in tables}
    result: list[RelationCandidate] = []
    for ref_a, ref_b in candidate_pairs(tables, profiles, rejected):
        stats = overlap_fn(ref_a, ref_b)
        if not stats.meets_threshold:
            continue
        card = cardinality(stats.unique_a, stats.unique_b)
        src, dst = ref_a, ref_b
        if card == "one_to_many" and not stats.unique_a:
            src, dst = ref_b, ref_a  # sisi unik ("one") menjadi from
        result.append(
            RelationCandidate(
                candidate_key=candidate_key(ref_a, ref_b),
                from_dataset_id=dataset_of[src.table],
                from_table=src.table,
                from_column=src.column,
                to_dataset_id=dataset_of[dst.table],
                to_table=dst.table,
                to_column=dst.column,
                cardinality=card,
                overlap_pct=stats.overlap_pct,
            )
        )
    return result
