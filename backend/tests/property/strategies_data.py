"""Generator Hypothesis bersama untuk property test Filter_Engine & propagasi.

Dipakai oleh Property 27–31 (soundness, idempotence, confluence, semi-join,
cakupan propagasi) dan render terfilter. Semua domain nilai sengaja kecil agar
predikat sering cocok dengan baris dan kunci relasi sering beririsan.

- ``tables()``: ``pl.DataFrame`` dengan kolom kunci (``k0``, ``k1``, ... bertipe
  Int64, domain ``KEY_DOMAIN``) serta kolom ``n`` (Int64), ``s`` (String),
  ``d`` (Date), ``ts`` (Datetime[us]); semua kolom dapat berisi null.
- ``filter_sets(schemas)``: list ``Predicate`` (belum dinormalisasi, boleh
  duplikat) atas kolom yang ada: ``date_range`` untuk kolom Date/Datetime,
  ``in`` untuk semua kolom (termasuk nilai ``None``).
- ``relation_graphs()``: ``DataGraph(frames, relations)`` berisi 2–6 tabel dan
  relasi antar kolom kunci dengan status acak (confirmed/candidate/rejected),
  termasuk sisi paralel dan siklus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import NamedTuple

import polars as pl
from hypothesis import strategies as st

from studio.core.filters import Predicate, date_range, in_values
from studio.core.sql_rules import ConfirmedRelation

__all__ = [
    "KEY_DOMAIN",
    "INT_DOMAIN",
    "STR_DOMAIN",
    "DATE_DOMAIN",
    "DATETIME_DOMAIN",
    "RELATION_STATUSES",
    "RelationSpec",
    "DataGraph",
    "key_column_names",
    "domain_for",
    "tables",
    "predicates",
    "filter_sets",
    "relation_graphs",
    "graphs_with_filters",
]

# ---------------------------------------------------------------------------
# Domain nilai kecil
# ---------------------------------------------------------------------------

KEY_DOMAIN: tuple[int, ...] = (0, 1, 2, 3, 4)
INT_DOMAIN: tuple[int, ...] = (-1, 0, 1, 2, 10)
STR_DOMAIN: tuple[str, ...] = ("", "a", "b", "B", "ab", "é")
DATE_DOMAIN: tuple[date, ...] = tuple(date(2024, 1, d) for d in range(1, 8))
#: Datetime pada tanggal ``DATE_DOMAIN`` termasuk tengah malam dan akhir hari.
DATETIME_DOMAIN: tuple[datetime, ...] = tuple(
    datetime.combine(d, t)
    for d in DATE_DOMAIN[::2]
    for t in (time(0, 0), time(12, 30), time(23, 59, 59, 999_999))
)

RELATION_STATUSES: tuple[str, ...] = ("confirmed", "candidate", "rejected")

#: Kolom non-kunci yang selalu ada pada setiap tabel.
_VALUE_COLUMNS: dict[str, tuple[pl.DataType, tuple[object, ...]]] = {
    "n": (pl.Int64(), INT_DOMAIN),
    "s": (pl.String(), STR_DOMAIN),
    "d": (pl.Date(), DATE_DOMAIN),
    "ts": (pl.Datetime("us"), DATETIME_DOMAIN),
}


def key_column_names(n: int) -> tuple[str, ...]:
    return tuple(f"k{i}" for i in range(n))


def domain_for(dtype: pl.DataType) -> tuple[object, ...]:
    """Domain nilai non-null untuk kolom bertipe ``dtype`` yang dihasilkan ``tables()``."""
    if dtype == pl.Date:
        return DATE_DOMAIN
    if isinstance(dtype, pl.Datetime):
        return DATETIME_DOMAIN
    if dtype == pl.String:
        return STR_DOMAIN
    return tuple(sorted(set(KEY_DOMAIN) | set(INT_DOMAIN)))


def _cell(domain: Sequence[object]) -> st.SearchStrategy[object]:
    return st.one_of(st.none(), st.sampled_from(domain)) if domain else st.none()


# ---------------------------------------------------------------------------
# Tabel
# ---------------------------------------------------------------------------


@st.composite
def tables(
    draw: st.DrawFn,
    *,
    min_rows: int = 0,
    max_rows: int = 8,
    key_columns: int | None = None,
) -> pl.DataFrame:
    """``pl.DataFrame`` kecil: kunci ``k0..`` (Int64) + ``n``/``s``/``d``/``ts``.

    ``key_columns=None`` → 1–2 kolom kunci diacak.
    """
    n_keys = key_columns if key_columns is not None else draw(st.integers(1, 2))
    schema: dict[str, pl.DataType] = {k: pl.Int64() for k in key_column_names(n_keys)}
    domains: dict[str, tuple[object, ...]] = {k: KEY_DOMAIN for k in schema}
    for name, (dtype, domain) in _VALUE_COLUMNS.items():
        schema[name] = dtype
        domains[name] = domain

    rows = draw(
        st.lists(
            st.tuples(*(_cell(domains[c]) for c in schema)),
            min_size=min_rows,
            max_size=max_rows,
        )
    )
    columns = {c: [r[i] for r in rows] for i, c in enumerate(schema)}
    return pl.DataFrame(columns, schema=schema)


# ---------------------------------------------------------------------------
# Predikat & FilterSet
# ---------------------------------------------------------------------------


def _is_temporal(dtype: pl.DataType) -> bool:
    return dtype == pl.Date or isinstance(dtype, pl.Datetime)


@st.composite
def predicates(
    draw: st.DrawFn, table: str, schema: Mapping[str, pl.DataType]
) -> Predicate:
    """Satu ``Predicate`` pada kolom yang ada di ``schema``."""
    column = draw(st.sampled_from(sorted(schema)))
    dtype = schema[column]
    if _is_temporal(dtype) and draw(st.booleans()):
        bound = st.one_of(st.none(), st.sampled_from(DATE_DOMAIN))
        start, end = draw(bound), draw(bound)
        return date_range(table, column, start, end)
    values = draw(st.lists(_cell(domain_for(dtype)), max_size=3))
    return in_values(table, column, values)


@st.composite
def filter_sets(
    draw: st.DrawFn,
    schemas: Mapping[str, Mapping[str, pl.DataType]],
    *,
    min_size: int = 0,
    max_size: int = 4,
) -> list[Predicate]:
    """List ``Predicate`` (belum dinormalisasi; boleh duplikat) atas ``schemas``.

    ``schemas`` = ``{nama tabel: {kolom: dtype}}``.
    """
    names = sorted(schemas)
    if not names:
        return []
    table = st.sampled_from(names)
    pred = table.flatmap(lambda t: predicates(t, schemas[t]))
    preds = draw(st.lists(pred, min_size=min_size, max_size=max_size))
    # Sesekali duplikasikan predikat agar normalisasi/dedupe ikut teruji.
    if preds and draw(st.booleans()):
        preds.append(draw(st.sampled_from(preds)))
    return preds


# ---------------------------------------------------------------------------
# Graf relasi
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RelationSpec:
    """Relasi antar kolom kunci dengan status; diterima langsung oleh ``RelationGraph``."""

    id: str
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    status: str = "confirmed"

    def to_confirmed(self) -> ConfirmedRelation:
        return ConfirmedRelation(
            id=self.id,
            table_a=self.table_a,
            column_a=self.column_a,
            table_b=self.table_b,
            column_b=self.column_b,
        )


class DataGraph(NamedTuple):
    """Kumpulan tabel dan relasinya; dapat di-unpack ``frames, relations = g``."""

    frames: dict[str, pl.DataFrame]
    relations: tuple[RelationSpec, ...]

    @property
    def schemas(self) -> dict[str, dict[str, pl.DataType]]:
        return {t: dict(df.schema) for t, df in self.frames.items()}

    @property
    def confirmed(self) -> tuple[ConfirmedRelation, ...]:
        return tuple(r.to_confirmed() for r in self.relations if r.status == "confirmed")

    def lazy(self) -> dict[str, pl.LazyFrame]:
        return {t: df.lazy() for t, df in self.frames.items()}


@st.composite
def relation_graphs(
    draw: st.DrawFn,
    *,
    min_tables: int = 2,
    max_tables: int = 6,
    max_rows: int = 6,
    max_relations: int | None = None,
    statuses: Sequence[str] = RELATION_STATUSES,
) -> DataGraph:
    """2–6 tabel ``t0..`` dengan relasi acak antar kolom kunci (status acak).

    Relasi menghubungkan dua tabel berbeda; sisi paralel (kolom berbeda atau
    sama) dan siklus diperbolehkan. Kolom kunci berbagi ``KEY_DOMAIN``.
    """
    n = draw(st.integers(min_tables, max_tables))
    names = [f"t{i}" for i in range(n)]
    frames = {name: draw(tables(max_rows=max_rows)) for name in names}
    keys = {name: [c for c in df.columns if c.startswith("k")] for name, df in frames.items()}

    limit = max_relations if max_relations is not None else 2 * n
    pair = st.tuples(st.sampled_from(names), st.sampled_from(names)).filter(
        lambda ab: ab[0] != ab[1]
    )
    raw_edges = draw(st.lists(pair, max_size=limit))
    relations: list[RelationSpec] = []
    for i, (a, b) in enumerate(raw_edges):
        relations.append(
            RelationSpec(
                id=f"r{i}",
                table_a=a,
                column_a=draw(st.sampled_from(keys[a])),
                table_b=b,
                column_b=draw(st.sampled_from(keys[b])),
                status=draw(st.sampled_from(tuple(statuses))),
            )
        )
    return DataGraph(frames=frames, relations=tuple(relations))


@st.composite
def graphs_with_filters(
    draw: st.DrawFn, *, max_filters: int = 4, **graph_kwargs: object
) -> tuple[DataGraph, list[Predicate]]:
    """``(DataGraph, FilterSet mentah)`` dengan predikat atas tabel-tabel graf."""
    graph = draw(relation_graphs(**graph_kwargs))  # type: ignore[arg-type]
    preds = draw(filter_sets(graph.schemas, max_size=max_filters))
    return graph, preds
