"""Propagasi filter lintas tabel melalui Confirmed_Relation (Req 23).

Graf ``RelationGraph`` tak berarah: simpul = tabel, sisi = Confirmed_Relation
``(A.a ↔ B.b)``. Hanya relasi berstatus confirmed yang masuk graf (Req 23.2).

``plan_propagation`` menjalankan BFS per tabel sumber (tabel yang memiliki
predikat langsung) dalam urutan kanonik; setiap tabel dikunjungi paling banyak
sekali per sumber sehingga propagasi selalu berhenti meski graf bersiklus
(Req 23.4). Jalur yang dipilih adalah jalur BFS terpendek dengan tie-break
urutan kanonik (nama tabel tetangga, lalu ``relation_id``).

``materialize`` menerapkan rencana: setiap constraint adalah rantai semi-join
yang dimulai dari predikat langsung sumber dan melewati tabel antara dalam
bentuk scan dasar (tanpa filter). Karena setiap constraint hanya bergantung
pada predikat langsung sumbernya (bukan hasil propagasi lain), hasil akhir
adalah konjungsi constraint independen → tidak bergantung urutan filter, dan
setiap nilai kunci pada tabel hasil propagasi terdapat pada kunci tabel induk
yang terfilter pada jalur tersebut (invariant semi-join, Req 23.5).

``PropagationPlan`` dapat diserialisasi ke JSON (``to_dict``/``from_dict``)
agar dapat dikirim ke proses worker query.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from studio.core.filters import (
    InvalidFilter,
    Predicate,
    apply_predicates,
    from_model,
    predicates_by_table,
)
from studio.core.models import Predicate as PredicateModel
from studio.core.sql_rules import ConfirmedRelation

__all__ = [
    "Step",
    "Constraint",
    "PropagationPlan",
    "RelationGraph",
    "plan_propagation",
    "materialize",
    "affected_tables",
    "is_filter_unaffected",
]

_CONFIRMED = "confirmed"


# ---------------------------------------------------------------------------
# Struktur rencana
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """Satu langkah terarah pada jalur BFS: ``parent.parent_column → child.child_column``."""

    relation_id: str
    parent_table: str
    parent_column: str
    child_table: str
    child_column: str

    def to_dict(self) -> dict[str, str]:
        return {
            "relation_id": self.relation_id,
            "parent_table": self.parent_table,
            "parent_column": self.parent_column,
            "child_table": self.child_table,
            "child_column": self.child_column,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Step:
        return cls(
            relation_id=str(d["relation_id"]),
            parent_table=str(d["parent_table"]),
            parent_column=str(d["parent_column"]),
            child_table=str(d["child_table"]),
            child_column=str(d["child_column"]),
        )


@dataclass(frozen=True)
class Constraint:
    """Constraint propagasi dari ``source`` ke tabel ujung ``path``."""

    source: str
    #: Jalur BFS dari ``source`` ke tabel target (tidak kosong).
    path: tuple[Step, ...]

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Constraint wajib memiliki jalur tidak kosong.")
        if self.path[0].parent_table != self.source:
            raise ValueError("Jalur constraint harus dimulai dari tabel sumber.")
        for prev, nxt in zip(self.path, self.path[1:]):
            if prev.child_table != nxt.parent_table:
                raise ValueError("Jalur constraint tidak bersambung.")

    @property
    def target(self) -> str:
        return self.path[-1].child_table

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "path": [s.to_dict() for s in self.path]}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Constraint:
        return cls(
            source=str(d["source"]),
            path=tuple(Step.from_dict(s) for s in d["path"]),
        )


@dataclass(frozen=True)
class PropagationPlan:
    """Rencana propagasi: ``{tabel target: constraints}`` dalam urutan kanonik."""

    #: Tabel yang memiliki predikat langsung (urutan kanonik).
    sources: tuple[str, ...] = ()
    #: ``((target, (constraint, ...)), ...)`` terurut menurut nama target;
    #: constraint per target terurut menurut urutan sumber.
    constraints: tuple[tuple[str, tuple[Constraint, ...]], ...] = ()
    _by_target: dict[str, tuple[Constraint, ...]] = field(
        default_factory=dict, init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_target", dict(self.constraints))

    def for_table(self, table: str) -> tuple[Constraint, ...]:
        return self._by_target.get(table, ())

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(t for t, _ in self.constraints)

    @property
    def affected_tables(self) -> frozenset[str]:
        """``sources ∪ reachable(sources)``."""
        return frozenset(self.sources) | frozenset(self._by_target)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": list(self.sources),
            "constraints": [
                {"table": t, "constraints": [c.to_dict() for c in cs]}
                for t, cs in self.constraints
            ],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> PropagationPlan:
        return cls(
            sources=tuple(str(s) for s in d.get("sources", ())),
            constraints=tuple(
                (
                    str(item["table"]),
                    tuple(Constraint.from_dict(c) for c in item["constraints"]),
                )
                for item in d.get("constraints", ())
            ),
        )


# ---------------------------------------------------------------------------
# Graf relasi
# ---------------------------------------------------------------------------


def _field(rel: Any, *names: str) -> Any:
    for name in names:
        value = rel.get(name) if isinstance(rel, Mapping) else getattr(rel, name, None)
        if value is not None:
            return value
    return None


def _coerce_relation(rel: Any) -> ConfirmedRelation | None:
    """Relasi (dataclass/objek/mapping) → ``ConfirmedRelation``; non-confirmed → ``None``."""
    if isinstance(rel, ConfirmedRelation):
        return rel
    status = _field(rel, "status")
    if status is not None and str(getattr(status, "value", status)) != _CONFIRMED:
        return None
    values = (
        _field(rel, "id", "relation_id"),
        _field(rel, "table_a", "from_table"),
        _field(rel, "column_a", "from_column"),
        _field(rel, "table_b", "to_table"),
        _field(rel, "column_b", "to_column"),
    )
    if any(v is None for v in values):
        raise ValueError(f"Relasi tidak lengkap: {rel!r}")
    rid, ta, ca, tb, cb = (str(v) for v in values)
    return ConfirmedRelation(id=rid, table_a=ta, column_a=ca, table_b=tb, column_b=cb)


class RelationGraph:
    """Graf tak berarah tabel ↔ tabel dari Confirmed_Relation.

    Relasi dengan atribut/field ``status`` selain ``"confirmed"`` diabaikan
    (Req 23.2). Sisi tiap tabel diurutkan kanonik: nama tabel tetangga, lalu
    ``relation_id`` (lalu kolom, untuk determinisme penuh).
    """

    def __init__(self, relations: Iterable[Any] = ()) -> None:
        adjacency: dict[str, list[Step]] = {}
        seen_ids: set[str] = set()
        for raw in relations:
            rel = _coerce_relation(raw)
            if rel is None or rel.id in seen_ids:
                continue
            seen_ids.add(rel.id)
            adjacency.setdefault(rel.table_a, []).append(
                Step(rel.id, rel.table_a, rel.column_a, rel.table_b, rel.column_b)
            )
            if rel.table_a != rel.table_b or rel.column_a != rel.column_b:
                adjacency.setdefault(rel.table_b, []).append(
                    Step(rel.id, rel.table_b, rel.column_b, rel.table_a, rel.column_a)
                )
        self._adjacency: dict[str, tuple[Step, ...]] = {
            t: tuple(
                sorted(
                    steps,
                    key=lambda s: (s.child_table, s.relation_id, s.parent_column, s.child_column),
                )
            )
            for t, steps in sorted(adjacency.items())
        }
        self.relation_ids: frozenset[str] = frozenset(seen_ids)

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(self._adjacency)

    def edges(self, table: str) -> tuple[Step, ...]:
        """Sisi terarah keluar dari ``table`` dalam urutan kanonik."""
        return self._adjacency.get(table, ())

    def bfs_paths(self, source: str) -> dict[str, tuple[Step, ...]]:
        """Jalur BFS terpendek dari ``source`` ke setiap tabel terjangkau (tanpa ``source``)."""
        visited = {source}
        parent: dict[str, Step] = {}
        queue: deque[str] = deque([source])
        while queue:
            u = queue.popleft()
            for step in self.edges(u):
                v = step.child_table
                if v in visited:  # siklus: tiap tabel paling banyak sekali (Req 23.4)
                    continue
                visited.add(v)
                parent[v] = step
                queue.append(v)

        paths: dict[str, tuple[Step, ...]] = {}
        for v in sorted(parent):
            chain: list[Step] = []
            node = v
            while node != source:
                step = parent[node]
                chain.append(step)
                node = step.parent_table
            paths[v] = tuple(reversed(chain))
        return paths

    def reachable(self, sources: Iterable[str]) -> frozenset[str]:
        """Tabel yang terjangkau dari salah satu ``sources`` (tidak termasuk sumber itu sendiri
        kecuali terjangkau dari sumber lain)."""
        out: set[str] = set()
        for s in sources:
            out.update(self.bfs_paths(s))
        return frozenset(out)


# ---------------------------------------------------------------------------
# Perencanaan
# ---------------------------------------------------------------------------


def plan_propagation(
    graph: RelationGraph, filter_set: Iterable[Predicate | PredicateModel]
) -> PropagationPlan:
    """BFS per sumber (urutan kanonik); tiap target menerima ≤ 1 constraint per sumber."""
    sources = tuple(predicates_by_table(_as_predicates(filter_set)))
    plan: dict[str, list[Constraint]] = {}
    for s in sources:
        for v, path in graph.bfs_paths(s).items():
            plan.setdefault(v, []).append(Constraint(source=s, path=path))
    return PropagationPlan(
        sources=sources,
        constraints=tuple((t, tuple(cs)) for t, cs in sorted(plan.items())),
    )


def affected_tables(
    graph: RelationGraph, filter_set: Iterable[Predicate | PredicateModel]
) -> frozenset[str]:
    """``sources ∪ reachable(sources)`` (Req 23.3)."""
    sources = tuple(predicates_by_table(_as_predicates(filter_set)))
    return frozenset(sources) | graph.reachable(sources)


def is_filter_unaffected(tables_used: Iterable[str], affected: Iterable[str]) -> bool:
    """Chart tidak terpengaruh filter ⇔ ``tables_used ∩ affected = ∅`` (Req 23.3)."""
    return frozenset(tables_used).isdisjoint(frozenset(affected))


def _as_predicates(filter_set: Iterable[Predicate | PredicateModel]) -> list[Predicate]:
    return [p if isinstance(p, Predicate) else from_model(p) for p in filter_set]


# ---------------------------------------------------------------------------
# Materialisasi
# ---------------------------------------------------------------------------

_INT_SIGNED = {pl.Int8, pl.Int16, pl.Int32, pl.Int64}
_INT_UNSIGNED = {pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64}


def _join_dtype(left: pl.DataType, right: pl.DataType) -> pl.DataType | None:
    """Dtype bersama untuk kunci join; ``None`` bila sudah sama (tanpa cast)."""
    if left == right and not isinstance(left, (pl.Categorical, pl.Enum)):
        return None
    lb, rb = left.base_type(), right.base_type()
    if lb is pl.Null:
        return right if rb is not pl.Null else None
    if rb is pl.Null:
        return left
    if left.is_integer() and right.is_integer():
        if lb in _INT_UNSIGNED and rb in _INT_UNSIGNED:
            return pl.UInt64
        return pl.Int64
    if left.is_numeric() and right.is_numeric():
        return pl.Float64
    if lb is pl.Datetime and rb is pl.Datetime:
        return pl.Datetime("us")
    if {lb, rb} <= {pl.Date, pl.Datetime}:
        return pl.Datetime("us")
    # Teks/kategori/campuran lain: bandingkan sebagai string.
    return pl.String


def _semi_join(
    left: pl.LazyFrame,
    left_col: str,
    keys: pl.LazyFrame,
    key_col: str,
    *,
    left_table: str,
    key_table: str,
) -> pl.LazyFrame:
    """``left ⋉ keys`` pada ``left.left_col = keys.key_col`` (null tidak cocok)."""
    left_schema = left.collect_schema()
    key_schema = keys.collect_schema()
    for table, col, schema in ((left_table, left_col, left_schema), (key_table, key_col, key_schema)):
        if col not in schema:
            raise InvalidFilter(
                f"Kolom relasi '{table}.{col}' tidak ditemukan untuk propagasi filter.",
                {"table": table, "column": col},
            )
    common = _join_dtype(left_schema[left_col], key_schema[key_col])
    right_key = pl.col(key_col)
    left_key: str | pl.Expr = left_col
    if common is not None:
        right_key = right_key.cast(common, strict=False)
        if left_schema[left_col] != common:
            left_key = pl.col(left_col).cast(common, strict=False)
    key_frame = keys.select(right_key.alias("__key")).unique()
    return left.join(key_frame, left_on=left_key, right_on="__key", how="semi")


def _require_frame(frames: Mapping[str, pl.LazyFrame], table: str) -> pl.LazyFrame:
    try:
        return frames[table]
    except KeyError:
        raise InvalidFilter(
            f"Tabel '{table}' tidak tersedia untuk propagasi filter.", {"table": table}
        ) from None


def materialize(
    frames: Mapping[str, pl.LazyFrame],
    filter_set: Iterable[Predicate | PredicateModel],
    plan: PropagationPlan,
) -> dict[str, pl.LazyFrame]:
    """Terapkan predikat langsung dan rencana propagasi pada ``frames``.

    Mengembalikan dict baru dengan kunci yang sama. Tabel tanpa predikat
    langsung maupun constraint dikembalikan tanpa perubahan. Predikat untuk
    tabel yang tidak ada di ``frames`` diabaikan kecuali tabel itu dibutuhkan
    sebagai sumber/tabel antara constraint dari tabel yang ada.
    """
    preds = predicates_by_table(_as_predicates(filter_set))
    direct: dict[str, pl.LazyFrame] = {}

    def direct_of(table: str) -> pl.LazyFrame:
        if table not in direct:
            direct[table] = apply_predicates(
                _require_frame(frames, table), preds.get(table, ()), table=table
            )
        return direct[table]

    result: dict[str, pl.LazyFrame] = {}
    for table, lf in frames.items():
        out = direct_of(table) if table in preds else lf
        for c in plan.for_table(table):
            if c.source not in preds:
                continue  # rencana basi: sumber tidak lagi punya predikat
            keys = direct_of(c.source)
            # Tabel antara: scan dasar yang di-semi-join dengan kunci induknya.
            for step in c.path[:-1]:
                keys = _semi_join(
                    _require_frame(frames, step.child_table),
                    step.child_column,
                    keys,
                    step.parent_column,
                    left_table=step.child_table,
                    key_table=step.parent_table,
                )
            last = c.path[-1]
            out = _semi_join(
                out,
                last.child_column,
                keys,
                last.parent_column,
                left_table=table,
                key_table=last.parent_table,
            )
        result[table] = out
    return result
