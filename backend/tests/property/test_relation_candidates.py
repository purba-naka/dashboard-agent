"""Feature: dashboard-studio-agent, Property 10: Kandidat relasi benar dan menghormati penolakan.

2–4 tabel kecil dengan kolom kunci berdomain kecil (``id``, ``customer_id``,
``{tabel}_id``, ...). Profil dari ``compute_column_profiles``; overlap nyata dari
``compute_overlap`` di atas LazyFrame. Hasil ``score_candidates`` dibandingkan
dengan referensi berbasis himpunan Python.

**Validates: Requirements 7.1, 7.2, 7.5**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import polars as pl
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from studio.core.models import ColumnInfo, ColumnProfile
from studio.core.relations import (
    MIN_OVERLAP_PCT,
    ColumnRef,
    OverlapStats,
    TableInfo,
    candidate_key,
    normalize_name,
    score_candidates,
)
from studio.data.profiler import compute_column_profiles, compute_overlap

_TABLE_NAMES = ("customers", "orders", "products", "store")
_KEY_TYPES = {"integer", "string", "date"}
_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_many"}

#: tipe logis → (dtype Polars, domain nilai non-null kecil)
_TYPES: dict[str, tuple[pl.DataType, tuple[Any, ...]]] = {
    "integer": (pl.Int64(), (1, 2, 3, 4)),
    "string": (pl.String(), ("a", "b", "c", "d")),
    "date": (pl.Date(), (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3))),
    "float": (pl.Float64(), (1.0, 2.0, 3.0)),
}


def _singular(table: str) -> str:
    return table[:-1] if len(table) > 1 and table.endswith("s") else table


@dataclass(frozen=True)
class Case:
    frames: dict[str, pl.DataFrame]
    schemas: dict[str, list[ColumnInfo]]
    rejected: frozenset[str]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def _column_pool(table: str, others: list[str]) -> list[str]:
    pool = ["id", "customer_id", "CustomerID", "kode", "tanggal", "amount"]
    for other in others:
        pool += [f"{_singular(other)}_id", f"{other}_id"]
    return list(dict.fromkeys(pool))  # unik, urutan stabil


def _type_for(draw: st.DrawFn, column: str) -> str:
    if column == "tanggal":
        return "date"
    if column == "amount":
        return "float"
    # Kunci kadang integer, kadang string → kompatibilitas tipe ikut teruji.
    return draw(st.sampled_from(("integer", "integer", "integer", "string")))


@st.composite
def cases(draw: st.DrawFn) -> Case:
    names = draw(
        st.lists(st.sampled_from(_TABLE_NAMES), min_size=2, max_size=4, unique=True)
    )
    frames: dict[str, pl.DataFrame] = {}
    schemas: dict[str, list[ColumnInfo]] = {}
    for table in names:
        pool = _column_pool(table, [t for t in names if t != table])
        cols = draw(st.lists(st.sampled_from(pool), min_size=1, max_size=4, unique=True))
        # Bias ke kolom kunci agar kandidat sering muncul.
        fk_names = [n for n in pool if n.endswith("_id") and n != "customer_id"]
        if draw(st.booleans()):
            cols.append("id")
        if draw(st.booleans()):
            cols.append(draw(st.sampled_from(fk_names)))
        cols = list(dict.fromkeys(cols))
        types = {c: _type_for(draw, c) for c in cols}
        n_rows = draw(st.sampled_from(range(9)))  # 0 baris jarang, tetapi teruji
        data: dict[str, list[Any]] = {}
        for c in cols:
            _, domain = _TYPES[types[c]]
            sub = draw(st.lists(st.sampled_from(domain), min_size=1, unique=True))
            if draw(st.booleans()):  # kolom unik (permutasi domain) → kardinalitas "one"
                values: list[Any] = draw(st.permutations(sub))[:n_rows]
                values += [None] * (n_rows - len(values))
            else:
                cell = st.one_of(st.none(), st.sampled_from(sub))
                values = draw(st.lists(cell, min_size=n_rows, max_size=n_rows))
            data[c] = values
        frames[table] = pl.DataFrame(data, schema={c: _TYPES[types[c]][0] for c in cols})
        schemas[table] = [ColumnInfo(name=c, type=types[c]) for c in cols]  # type: ignore[arg-type]

    # Tolak sebagian pasangan yang lolos aturan tipe & nama, plus kunci acak tak terpakai.
    eligible = sorted(_eligible_keys(schemas))
    rejected = {k for k in eligible if draw(st.integers(0, 2)) == 0}  # ~1/3 ditolak
    if draw(st.booleans()):
        rejected.add("nope.x|nope.y")
    return Case(frames=frames, schemas=schemas, rejected=frozenset(rejected))


# ---------------------------------------------------------------------------
# Referensi
# ---------------------------------------------------------------------------


def _ref_names_match(a: ColumnRef, b: ColumnRef) -> bool:
    na, nb = normalize_name(a.column), normalize_name(b.column)
    if na == nb:
        return True

    def fk_to_id(fk: ColumnRef, pk: ColumnRef) -> bool:
        base = normalize_name(pk.table)
        forms = {f"{base}_id", f"{_singular(base)}_id"}
        return normalize_name(pk.column) == "id" and normalize_name(fk.column) in forms

    return fk_to_id(a, b) or fk_to_id(b, a)


def _ref_types_ok(ta: str, tb: str) -> bool:
    return ta == tb and ta in _KEY_TYPES


def _eligible_pairs(
    schemas: dict[str, list[ColumnInfo]],
) -> list[tuple[ColumnInfo, ColumnRef, ColumnInfo, ColumnRef]]:
    out = []
    tables = sorted(schemas)
    for i, ta in enumerate(tables):
        for tb in tables[i + 1 :]:
            for ca in schemas[ta]:
                for cb in schemas[tb]:
                    ra, rb = ColumnRef(ta, ca.name), ColumnRef(tb, cb.name)
                    if _ref_types_ok(ca.type, cb.type) and _ref_names_match(ra, rb):
                        out.append((ca, ra, cb, rb))
    return out


def _eligible_keys(schemas: dict[str, list[ColumnInfo]]) -> set[str]:
    return {_ref_key(ra, rb) for _, ra, _, rb in _eligible_pairs(schemas)}


def _ref_key(a: ColumnRef, b: ColumnRef) -> str:
    first, second = sorted([str(a), str(b)])
    return f"{first}|{second}"


def _set_stats(values: list[Any]) -> tuple[set[Any], bool]:
    non_null = [v for v in values if v is not None]
    distinct = set(non_null)
    return distinct, len(distinct) == len(non_null)


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much])
@given(cases())
def test_relation_candidates_correct_and_respect_rejection(case: Case) -> None:
    """Feature: dashboard-studio-agent, Property 10: Kandidat relasi benar dan menghormati penolakan.

    **Validates: Requirements 7.1, 7.2, 7.5**
    """
    lazies = {t: df.lazy() for t, df in case.frames.items()}
    tables = [TableInfo(f"ds_{t}", t, case.schemas[t]) for t in case.frames]
    profiles: dict[str, list[ColumnProfile]] = {
        f"ds_{t}": compute_column_profiles(lazies[t], case.schemas[t]).columns for t in case.frames
    }
    types = {(t, c.name): c.type for t, cols in case.schemas.items() for c in cols}

    calls: list[str] = []

    def overlap_fn(a: ColumnRef, b: ColumnRef) -> OverlapStats:
        calls.append(candidate_key(a, b))
        return compute_overlap(lazies[a.table], a.column, lazies[b.table], b.column)

    found = score_candidates(tables, profiles, case.rejected, overlap_fn)

    # Req 7.5: pasangan ditolak tidak pernah dihitung maupun diusulkan.
    assert not set(calls) & case.rejected
    assert all(c.candidate_key not in case.rejected for c in found)

    # Referensi berbasis himpunan untuk setiap pasangan yang lolos aturan tipe & nama.
    expected: dict[str, tuple[float, str, set[str]]] = {}
    for _, ra, _, rb in _eligible_pairs(case.schemas):
        key = _ref_key(ra, rb)
        if key in case.rejected:
            continue
        set_a, uniq_a = _set_stats(case.frames[ra.table][ra.column].to_list())
        set_b, uniq_b = _set_stats(case.frames[rb.table][rb.column].to_list())
        denom = min(len(set_a), len(set_b))
        inter = len(set_a & set_b)
        if denom == 0 or inter * 100 < MIN_OVERLAP_PCT * denom:
            continue
        if uniq_a and uniq_b:
            card = "one_to_one"
        elif uniq_a or uniq_b:
            card = "one_to_many"
        else:
            card = "many_to_many"
        unique_sides = {str(r) for r, u in ((ra, uniq_a), (rb, uniq_b)) if u}
        expected[key] = (inter * 100.0 / denom, card, unique_sides)

    # Kelengkapan & tanpa duplikat: tepat himpunan pasangan yang memenuhi syarat.
    keys = [c.candidate_key for c in found]
    event(f"candidates={min(len(keys), 3)}{'+' if len(keys) >= 3 else ''}")
    event(f"rejected_eligible={bool(case.rejected & _eligible_keys(case.schemas))}")
    assert len(keys) == len(set(keys))
    assert set(keys) == set(expected)

    for cand in found:
        src, dst = cand.from_ref, cand.to_ref
        # Req 7.2: tabel/kolom sumber & tujuan lintas Dataset yang terdaftar.
        assert src.table != dst.table
        assert cand.from_dataset_id == f"ds_{src.table}"
        assert cand.to_dataset_id == f"ds_{dst.table}"
        # Req 7.1: tipe kompatibel & aturan nama.
        assert _ref_types_ok(types[(src.table, src.column)], types[(dst.table, dst.column)])
        assert _ref_names_match(src, dst)
        # candidate_key kanonik (pasangan tak berurut).
        assert cand.candidate_key == _ref_key(src, dst) == candidate_key(dst, src)
        # Overlap & kardinalitas sama dengan referensi.
        pct, card, unique_sides = expected[cand.candidate_key]
        assert cand.overlap_pct >= MIN_OVERLAP_PCT
        assert abs(cand.overlap_pct - pct) < 1e-9
        assert cand.cardinality in _CARDINALITIES
        assert cand.cardinality == card
        if card == "one_to_many":
            assert unique_sides == {str(src)}  # sisi unik ("one") menjadi from
        event(f"cardinality={card}")
