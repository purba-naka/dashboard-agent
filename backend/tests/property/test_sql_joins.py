"""Feature: dashboard-studio-agent, Property 13: JOIN hanya melalui Confirmed_Relation.

Untuk himpunan Confirmed_Relation acak dan query JOIN antar-tabel Workspace berbeda
(dengan alias tabel, CTE, dan kolom CTE yang di-rename), SQL_Validator menerima query
jika dan hanya jika setiap pasangan kolom kesetaraan pada kondisi JOIN — setelah
resolusi alias/CTE ke kolom tabel dasar — ada di Confirmed_Relation sebagai pasangan
tak berurut. Setiap penolakan menyebutkan pasangan kolom yang belum dikonfirmasi.

**Validates: Requirements 7.6, 7.7, 14.6**
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import event, given
from hypothesis import strategies as st
from sqlglot import exp

from studio.api.errors import StudioError
from studio.core.sql_rules import DIALECT, ConfirmedRelation, analyze_sql

TABLES: dict[str, list[str]] = {
    "customers": ["customer_id", "name", "city", "segment"],
    "orders": ["order_id", "customer_id", "product_id", "amount", "order_date"],
    "products": ["product_id", "category", "price"],
    "regions": ["city", "region", "manager"],
}

ColumnRef = tuple[str, str]
Pair = frozenset[ColumnRef]

_ALL_COLUMNS: list[ColumnRef] = [(t, c) for t, cols in TABLES.items() for c in cols]
_CROSS_PAIRS: list[tuple[ColumnRef, ColumnRef]] = [
    (a, b) for i, a in enumerate(_ALL_COLUMNS) for b in _ALL_COLUMNS[i + 1:] if a[0] != b[0]
]
# Nama alias/CTE: tidak pernah bentrok dengan nama tabel Workspace.
_NAME_POOL = [f"{p}{i}" for p in ("a", "src", "cte_") for i in range(6)]


@st.composite
def confirmed_relations(draw: st.DrawFn) -> list[ConfirmedRelation]:
    """Himpunan Confirmed_Relation antar-tabel berbeda (unik per pasangan tak berurut)."""
    pairs = draw(st.lists(st.sampled_from(_CROSS_PAIRS), max_size=8, unique_by=lambda p: frozenset(p)))
    rels = []
    for i, (a, b) in enumerate(pairs):
        if draw(st.booleans()):  # urutan sisi relasi tidak bermakna
            a, b = b, a
        rels.append(ConfirmedRelation(id=f"rel_{i}", table_a=a[0], column_a=a[1], table_b=b[0], column_b=b[1]))
    return rels


@dataclass
class _Source:
    """Satu sumber FROM/JOIN: qualifier yang dipakai di SQL + kolom yang diekspos beserta lineage."""

    node: exp.Expression
    qualifier: str
    columns: list[tuple[str, ColumnRef]]  # (nama kolom yang terlihat, kolom tabel dasar)


@dataclass
class JoinCase:
    sql: str
    #: Pasangan kolom tabel dasar pada setiap kesetaraan JOIN.
    pairs: list[Pair]


@st.composite
def _source(draw: st.DrawFn, table: str, names: list[str], ctes: list[tuple[str, exp.Select]]) -> _Source:
    kind = draw(st.sampled_from(["table", "aliased", "cte", "cte_aliased"]))
    cols = TABLES[table]
    if kind == "table":
        return _Source(exp.table_(table), table, [(c, (table, c)) for c in cols])
    if kind == "aliased":
        alias = names.pop()
        return _Source(exp.table_(table, alias=alias), alias, [(c, (table, c)) for c in cols])

    cte_name = names.pop()
    if draw(st.booleans()):
        body = exp.select("*").from_(table)
        exposed = [(c, (table, c)) for c in cols]
    else:
        picked = draw(st.lists(st.sampled_from(cols), min_size=1, max_size=len(cols), unique=True))
        projs, exposed = [], []
        for c in picked:
            out = f"{c}_r" if draw(st.booleans()) else c  # rename → harus di-resolve lewat lineage
            projs.append(exp.alias_(exp.column(c), out) if out != c else exp.column(c))
            exposed.append((out, (table, c)))
        body = exp.select(*projs).from_(table)
    ctes.append((cte_name, body))
    if kind == "cte":
        return _Source(exp.table_(cte_name), cte_name, exposed)
    alias = names.pop()
    return _Source(exp.table_(cte_name, alias=alias), alias, exposed)


@st.composite
def join_queries(draw: st.DrawFn, relations: list[ConfirmedRelation]) -> JoinCase:
    """Query JOIN 2–3 tabel Workspace berbeda; kondisi dipilih dari relasi atau acak."""
    confirmed: set[Pair] = {
        frozenset({(r.table_a, r.column_a), (r.table_b, r.column_b)}) for r in relations
    }
    tables = draw(st.lists(st.sampled_from(sorted(TABLES)), min_size=2, max_size=3, unique=True))
    names = draw(st.permutations(_NAME_POOL))
    names = list(names)
    ctes: list[tuple[str, exp.Select]] = []
    sources = [draw(_source(t, names, ctes)) for t in tables]

    pairs: list[Pair] = []
    query = exp.select(
        *[exp.column(sources[0].columns[0][0], table=sources[0].qualifier)]
    ).from_(sources[0].node)
    for k in range(1, len(sources)):
        right = sources[k]
        # Kesetaraan kandidat yang terkonfirmasi antara sisi kanan dan salah satu sisi kiri.
        good = [
            (left, lc, rc)
            for left in sources[:k]
            for lc, lref in left.columns
            for rc, rref in right.columns
            if frozenset({lref, rref}) in confirmed
        ]
        equalities = []
        for _ in range(draw(st.integers(1, 2))):
            if good and draw(st.integers(0, 4)) > 0:  # condong ke relasi terkonfirmasi (~80%)
                left, lc, rc = draw(st.sampled_from(good))
            else:
                left = draw(st.sampled_from(sources[:k]))
                lc = draw(st.sampled_from([n for n, _ in left.columns]))
                rc = draw(st.sampled_from([n for n, _ in right.columns]))
            lref = dict(left.columns)[lc]
            rref = dict(right.columns)[rc]
            pairs.append(frozenset({lref, rref}))
            sides = [exp.column(lc, table=left.qualifier), exp.column(rc, table=right.qualifier)]
            if draw(st.booleans()):
                sides.reverse()
            equalities.append(exp.EQ(this=sides[0], expression=sides[1]))
        on = exp.and_(*equalities) if len(equalities) > 1 else equalities[0]
        query = query.join(right.node, on=on, join_type=draw(st.sampled_from(["inner", "left", "right", "full"])))
    for name, body in ctes:
        query = query.with_(name, as_=body)
    return JoinCase(sql=query.sql(dialect=DIALECT), pairs=pairs)


@st.composite
def relations_and_join(draw: st.DrawFn) -> tuple[list[ConfirmedRelation], JoinCase]:
    rels = draw(confirmed_relations())
    join = draw(join_queries(rels))
    if draw(st.booleans()):
        # Konfirmasi sebagian/semua pasangan yang belum ada agar cabang "diterima" sering teruji.
        known = {frozenset({(r.table_a, r.column_a), (r.table_b, r.column_b)}) for r in rels}
        missing = list(dict.fromkeys(p for p in join.pairs if p not in known))
        keep_out = draw(st.sampled_from([0, 0, 1])) if missing else 0
        for i, pair in enumerate(missing[keep_out:]):
            a, b = sorted(pair)
            if draw(st.booleans()):
                a, b = b, a
            rels.append(ConfirmedRelation(f"rel_x{i}", a[0], a[1], b[0], b[1]))
    return rels, join


def _fmt(ref: ColumnRef) -> str:
    return f"{ref[0]}.{ref[1]}"


@given(relations_and_join())
def test_join_accepted_iff_all_pairs_confirmed(case: tuple[list[ConfirmedRelation], JoinCase]) -> None:
    """Feature: dashboard-studio-agent, Property 13: JOIN hanya melalui Confirmed_Relation.

    **Validates: Requirements 7.6, 7.7, 14.6**
    """
    relations, join = case
    by_pair = {frozenset({(r.table_a, r.column_a), (r.table_b, r.column_b)}): r.id for r in relations}
    unconfirmed = [p for p in join.pairs if p not in by_pair]

    event("accepted" if not unconfirmed else "rejected")
    event("with CTE" if join.sql.startswith("WITH") else "without CTE")
    if not unconfirmed:
        analysis = analyze_sql(join.sql, TABLES, relations)
        expected_ids = {by_pair[p] for p in join.pairs}
        assert set(analysis.relations_used) == expected_ids, join.sql
        return

    with pytest.raises(StudioError) as info:
        analyze_sql(join.sql, TABLES, relations)
    err = info.value
    assert err.code == "UNCONFIRMED_JOIN", (join.sql, err.code, err.message)
    reported = {frozenset(pair) for pair in err.details["unconfirmed_pairs"]}
    expected = {frozenset({_fmt(a) for a in p}) for p in unconfirmed}
    assert reported == expected, join.sql
    for p in unconfirmed:  # pesan menyebut setiap pasangan kolom yang belum dikonfirmasi
        assert any(f"{_fmt(a)} = {_fmt(b)}" in err.message for a, b in (sorted(p), sorted(p)[::-1]))
