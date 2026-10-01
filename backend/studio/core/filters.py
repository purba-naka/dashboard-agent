"""Predikat filter (Global_Filter & Cross_Filter) dan penerapannya pada LazyFrame.

Bentuk engine: ``Predicate`` adalah dataclass frozen (hashable) sehingga
``FilterSet`` dapat dinormalisasi menjadi tuple kanonik yang terurut & bebas
duplikat. Karena FilterSet adalah himpunan dan filter adalah konjungsi,
``apply_predicates`` bersifat idempoten (Req 22.6) dan konfluen (Req 22.7).

Bentuk API/store: model Pydantic ``DateRangePredicate`` / ``InPredicate`` di
``core/models.py``; konversi dua arah lewat ``from_model`` / ``to_model``,
``parse_filter_set`` dan ``dump_filter_set``.

Semantik:
- ``date_range``: rentang inklusif ``start <= col <= end``; ujung ``None``
  berarti tidak dibatasi. Pada kolom ``Datetime`` ujung akhir inklusif sampai
  akhir hari ``end`` (``col < end + 1 hari``). Nilai null tidak lolos bila
  setidaknya satu ujung terisi; ``date_range`` tanpa kedua ujung adalah no-op
  dan dibuang oleh ``normalize_filters``.
- ``in``: keanggotaan himpunan; ``None`` pada ``values`` mencocokkan null.
  Nilai di-coerce ke dtype kolom (mis. string ISO → Date dari JSON Cross_Filter);
  nilai yang tidak dapat direpresentasikan pada dtype kolom tidak mencocokkan
  baris apa pun. ``values`` kosong tidak mencocokkan baris apa pun.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

import polars as pl

from studio.api.errors import StudioError
from studio.core.models import (
    FILTER_SET_ADAPTER,
    DateRangePredicate,
    InPredicate,
    Scalar,
)
from studio.core.models import Predicate as PredicateModel

__all__ = [
    "PredicateKind",
    "Predicate",
    "FilterSet",
    "InvalidFilter",
    "date_range",
    "in_values",
    "normalize_filters",
    "predicates_by_table",
    "tables_with_predicates",
    "to_expr",
    "apply_predicates",
    "from_model",
    "to_model",
    "parse_filter_set",
    "dump_filter_set",
]

PredicateKind = Literal["date_range", "in"]


class InvalidFilter(StudioError):
    """Predikat tidak valid atau tidak dapat diterapkan pada kolom target."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="INVALID_FILTER", message=message, details=details, http_status=422
        )


# ---------------------------------------------------------------------------
# Predicate (bentuk engine, frozen)
# ---------------------------------------------------------------------------


def _value_sort_key(v: Scalar) -> tuple[str, str]:
    """Kunci urut total untuk skalar bertipe campuran (tidak pernah crash)."""
    return (type(v).__name__, repr(v))


def _as_date(value: Any, name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError as exc:
            raise InvalidFilter(
                f"Nilai {name} bukan tanggal ISO-8601: {value!r}", {name: value}
            ) from exc
    raise InvalidFilter(f"Nilai {name} harus berupa tanggal.", {name: repr(value)})


@dataclass(frozen=True)
class Predicate:
    """Predikat pada ``(table, column)``; hashable dan immutable."""

    table: str
    column: str
    kind: PredicateKind
    #: Ujung rentang inklusif (hanya ``date_range``); salah satu boleh ``None``.
    start: date | None = None
    end: date | None = None
    #: Himpunan nilai (hanya ``in``).
    values: frozenset[Scalar] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.kind not in ("date_range", "in"):
            raise InvalidFilter(f"Jenis predikat tidak dikenal: {self.kind!r}")
        if not self.table or not self.column:
            raise InvalidFilter("Predikat wajib memiliki table dan column.")
        if self.kind == "date_range":
            if self.values:
                raise InvalidFilter("Predikat date_range tidak boleh memiliki values.")
            object.__setattr__(self, "start", _as_date(self.start, "start"))
            object.__setattr__(self, "end", _as_date(self.end, "end"))
        else:
            if self.start is not None or self.end is not None:
                raise InvalidFilter("Predikat in tidak boleh memiliki start/end.")
            if not isinstance(self.values, frozenset):
                object.__setattr__(self, "values", frozenset(self.values))

    @property
    def is_noop(self) -> bool:
        """``date_range`` tanpa kedua ujung tidak membatasi apa pun."""
        return self.kind == "date_range" and self.start is None and self.end is None

    def sort_key(self) -> tuple[Any, ...]:
        """Kunci urutan kanonik (total, aman untuk nilai bertipe campuran)."""
        start = "" if self.start is None else self.start.isoformat()
        end = "" if self.end is None else self.end.isoformat()
        values = tuple(sorted((_value_sort_key(v) for v in self.values)))
        return (self.table, self.column, self.kind, start, end, values)


#: FilterSet ternormalisasi: tuple predikat unik dalam urutan kanonik.
FilterSet = tuple[Predicate, ...]


def date_range(
    table: str, column: str, start: date | None = None, end: date | None = None
) -> Predicate:
    return Predicate(table=table, column=column, kind="date_range", start=start, end=end)


def in_values(table: str, column: str, values: Iterable[Scalar]) -> Predicate:
    return Predicate(table=table, column=column, kind="in", values=frozenset(values))


# ---------------------------------------------------------------------------
# Normalisasi
# ---------------------------------------------------------------------------


def normalize_filters(preds: Iterable[Predicate | PredicateModel]) -> FilterSet:
    """Dedupe (himpunan), buang no-op, dan urutkan secara kanonik.

    Hasil tidak bergantung urutan masukan dan idempoten:
    ``normalize_filters(normalize_filters(x)) == normalize_filters(x)``.
    """
    unique: set[Predicate] = set()
    for p in preds:
        pred = p if isinstance(p, Predicate) else from_model(p)
        if not pred.is_noop:
            unique.add(pred)
    return tuple(sorted(unique, key=Predicate.sort_key))


def predicates_by_table(preds: Iterable[Predicate]) -> dict[str, FilterSet]:
    """Kelompokkan predikat per tabel (masing-masing ternormalisasi)."""
    grouped: dict[str, list[Predicate]] = defaultdict(list)
    for p in normalize_filters(preds):
        grouped[p.table].append(p)
    return {t: tuple(ps) for t, ps in sorted(grouped.items())}


def tables_with_predicates(preds: Iterable[Predicate]) -> tuple[str, ...]:
    """Tabel sumber (memiliki predikat langsung) dalam urutan kanonik."""
    return tuple(predicates_by_table(preds))


# ---------------------------------------------------------------------------
# Ekspresi Polars
# ---------------------------------------------------------------------------


def _date_range_expr(p: Predicate, dtype: pl.DataType | None) -> pl.Expr:
    col = pl.col(p.column)
    conds: list[pl.Expr] = []

    if isinstance(dtype, pl.Datetime):
        if dtype.time_zone is not None:
            # Bandingkan pada wall-clock zona waktu kolom.
            col = col.dt.replace_time_zone(None)
        unit = dtype.time_unit or "us"
        if p.start is not None:
            lower = datetime.combine(p.start, time.min)
            conds.append(col >= pl.lit(lower, dtype=pl.Datetime(unit)))
        if p.end is not None and p.end < date.max:
            upper = datetime.combine(p.end + timedelta(days=1), time.min)
            conds.append(col < pl.lit(upper, dtype=pl.Datetime(unit)))
        elif p.end is not None:  # end == date.max: batas atas tak efektif
            conds.append(col.is_not_null())
    elif dtype is None or dtype == pl.Date:
        if p.start is not None:
            conds.append(col >= pl.lit(p.start, dtype=pl.Date))
        if p.end is not None:
            conds.append(col <= pl.lit(p.end, dtype=pl.Date))
    else:
        raise InvalidFilter(
            f"Filter rentang tanggal tidak dapat diterapkan pada kolom "
            f"'{p.table}.{p.column}' bertipe {dtype}.",
            {"table": p.table, "column": p.column, "dtype": str(dtype)},
        )

    if not conds:
        return pl.lit(True)
    expr = conds[0]
    for c in conds[1:]:
        expr = expr & c
    return expr


def _coerce_values(values: Iterable[Scalar], dtype: pl.DataType) -> pl.Series:
    """Coerce nilai non-null ke ``dtype``; nilai yang gagal di-cast dibuang."""
    by_type: dict[type, list[Scalar]] = defaultdict(list)
    for v in values:
        by_type[type(v)].append(v)
    parts: list[pl.Series] = []
    for py_type, group in by_type.items():
        try:
            s = pl.Series("v", group)
            if py_type is str and dtype == pl.Date:
                s = s.str.to_date(strict=False)
            elif py_type is str and isinstance(dtype, pl.Datetime):
                s = s.str.to_datetime(
                    time_unit=dtype.time_unit or "us",
                    time_zone=dtype.time_zone,
                    strict=False,
                )
            s = s.cast(dtype, strict=False).drop_nulls()
        except Exception:  # tipe tidak kompatibel sama sekali → tidak ada yang cocok
            continue
        if len(s):
            parts.append(s)
    if not parts:
        return pl.Series("v", [], dtype=dtype)
    return pl.concat(parts).unique(maintain_order=False)


def _in_expr(p: Predicate, dtype: pl.DataType | None) -> pl.Expr:
    col = pl.col(p.column)
    match_null = None in p.values
    non_null = sorted((v for v in p.values if v is not None), key=_value_sort_key)

    member: pl.Expr | None = None
    if non_null:
        if dtype is None:
            series = pl.Series("v", non_null, strict=False)
        else:
            series = _coerce_values(non_null, dtype)
        if len(series):
            member = col.is_in(series.implode()).fill_null(False)

    if member is not None and match_null:
        return member | col.is_null()
    if member is not None:
        return member
    if match_null:
        return col.is_null()
    return pl.lit(False)


def to_expr(p: Predicate, dtype: pl.DataType | None = None) -> pl.Expr:
    """Ekspresi boolean Polars untuk satu predikat.

    ``dtype`` (tipe kolom) dipakai untuk menangani kolom ``Datetime`` dan
    coercion nilai ``in``; ``None`` berarti diasumsikan ``Date``/tanpa coercion.
    """
    if p.kind == "date_range":
        return _date_range_expr(p, dtype)
    return _in_expr(p, dtype)


def apply_predicates(
    lf: pl.LazyFrame,
    preds: Iterable[Predicate | PredicateModel],
    *,
    table: str | None = None,
    schema: Mapping[str, pl.DataType] | None = None,
) -> pl.LazyFrame:
    """Terapkan konjungsi semua predikat pada ``lf``.

    Bila ``table`` diberikan, hanya predikat untuk tabel tersebut yang dipakai;
    selain itu semua predikat diasumsikan milik ``lf``. Tanpa predikat, ``lf``
    dikembalikan apa adanya. Kolom yang tidak ada → ``InvalidFilter``.
    """
    normalized = normalize_filters(preds)
    if table is not None:
        normalized = tuple(p for p in normalized if p.table == table)
    if not normalized:
        return lf

    resolved = schema if schema is not None else lf.collect_schema()
    exprs: list[pl.Expr] = []
    for p in normalized:
        if p.column not in resolved:
            raise InvalidFilter(
                f"Kolom '{p.column}' tidak ditemukan pada tabel '{p.table}'.",
                {"table": p.table, "column": p.column},
            )
        exprs.append(to_expr(p, resolved[p.column]))
    return lf.filter(*exprs)


# ---------------------------------------------------------------------------
# Konversi dengan model Pydantic (API/store)
# ---------------------------------------------------------------------------


def from_model(m: PredicateModel | Mapping[str, Any]) -> Predicate:
    """Model Pydantic (atau dict JSON) → ``Predicate`` frozen."""
    if isinstance(m, Mapping):
        m = FILTER_SET_ADAPTER.validate_python([m])[0]
    if isinstance(m, DateRangePredicate):
        return date_range(m.table, m.column, m.start, m.end)
    if isinstance(m, InPredicate):
        return in_values(m.table, m.column, m.values)
    raise InvalidFilter(f"Tipe predikat tidak dikenal: {type(m).__name__}")


def to_model(p: Predicate) -> PredicateModel:
    """``Predicate`` frozen → model Pydantic (values terurut kanonik)."""
    if p.kind == "date_range":
        return DateRangePredicate(table=p.table, column=p.column, start=p.start, end=p.end)
    values = tuple(sorted(p.values, key=_value_sort_key))
    return InPredicate(table=p.table, column=p.column, values=values)


def parse_filter_set(data: Sequence[Any] | None) -> FilterSet:
    """List JSON / model Pydantic / ``Predicate`` → FilterSet ternormalisasi."""
    if not data:
        return ()
    items: list[Predicate] = []
    raw: list[Any] = []
    for d in data:
        if isinstance(d, Predicate):
            items.append(d)
        elif isinstance(d, (DateRangePredicate, InPredicate)):
            items.append(from_model(d))
        else:
            raw.append(d)
    if raw:
        try:
            models = FILTER_SET_ADAPTER.validate_python(raw)
        except Exception as exc:  # pydantic.ValidationError
            raise InvalidFilter("FilterSet tidak valid.", {"error": str(exc)}) from exc
        items.extend(from_model(m) for m in models)
    return normalize_filters(items)


def dump_filter_set(preds: Iterable[Predicate]) -> list[dict[str, Any]]:
    """FilterSet → list dict JSON-ready (urutan kanonik)."""
    return [to_model(p).model_dump(mode="json") for p in normalize_filters(preds)]
