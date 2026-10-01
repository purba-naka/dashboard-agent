"""Logika Semantic_Model murni: kunci kanonik, draft heuristik, merge, validasi metrik.

Tidak ada I/O di modul ini (Req 30.1). Pemanggil (``data/semantic_drafter.py``,
``api/semantic.py``) bertanggung jawab atas penyimpanan dan validasi V7
(``DataEngine.validate_metric``).

- ``entry_key(kind, body)`` — kunci kanonik untuk penolakan & merge (Req 31.8).
- ``heuristic_draft(datasets)`` — draft deterministik dari profil (Req 32.2).
- ``merge_draft(existing, draft, rejected)`` — entri yang boleh ditulis (Req 32.5, 32.6).
- ``check_metric_expr`` / ``validate_metric_static`` — ekspresi Business_Metric
  wajib agregat dan hanya merujuk kolom tabel dasar; probe
  ``SELECT <expr> AS value FROM <tabel>`` lolos V1–V6 (Req 31.5, 31.6).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from studio.api.errors import StudioError
from studio.core.models import (
    Aggregation,
    BusinessMetric,
    ColumnProfile,
    ColumnRole,
    NumberFormat,
    SemanticColumn,
)
from studio.core.sql_rules import DIALECT, analyze_sql

__all__ = [
    "MetricError",
    "DatasetInput",
    "DraftEntry",
    "ExistingEntry",
    "ENUM_MAX_DISTINCT",
    "normalize_key_part",
    "column_key",
    "metric_key",
    "term_key",
    "instruction_key",
    "verified_query_key",
    "entry_key",
    "default_aggregation",
    "heuristic_label",
    "heuristic_format",
    "heuristic_draft",
    "merge_draft",
    "metric_probe_sql",
    "check_metric_expr",
    "validate_metric_static",
]

#: Kolom dimensi dengan nilai unik ≤ batas ini ditandai enum (Req 32.2).
ENUM_MAX_DISTINCT = 50

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_PERCENT_NAME = re.compile(r"pct|persen|percent|rate|ratio|margin", re.IGNORECASE)
_CURRENCY_NAME = re.compile(
    r"harga|price|revenue|omzet|pendapatan|sales|amount|amt|cost|biaya|total",
    re.IGNORECASE,
)
_NUMERIC = frozenset({"integer", "float"})


class MetricError(StudioError):
    """Ekspresi Business_Metric ditolak (Req 31.6)."""

    def __init__(self, code: str, reason: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(
            code,
            f"Business_Metric tidak valid: {reason}",
            {"reason": reason, **dict(details or {})},
            http_status=422,
        )


# ---------------------------------------------------------------------------
# Kunci kanonik
# ---------------------------------------------------------------------------


def normalize_key_part(text: str) -> str:
    """Lowercase, non-alfanumerik → ``_``, tanpa ``_`` di tepi."""
    return _NON_ALNUM.sub("_", str(text).strip().lower()).strip("_")


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _norm_text(text: str) -> str:
    return " ".join(str(text).lower().split())


def column_key(table: str, column: str) -> str:
    return f"col:{normalize_key_part(table)}.{normalize_key_part(column)}"


def metric_key(name: str) -> str:
    return f"metric:{normalize_key_part(name)}"


def term_key(term: str) -> str:
    return f"term:{normalize_key_part(term)}"


def instruction_key(text: str) -> str:
    return f"instr:{_hash(_norm_text(text))}"


def verified_query_key(sql: str) -> str:
    return f"vq:{_hash(_norm_text(sql).rstrip(';').strip())}"


def entry_key(kind: str, body: Mapping[str, Any]) -> str:
    """Kunci kanonik entri dari ``kind`` dan isi body."""
    if kind == "column":
        return column_key(body["table"], body["column"])
    if kind == "metric":
        return metric_key(body["name"])
    if kind == "term":
        return term_key(body["term"])
    if kind == "instruction":
        return instruction_key(body["text"])
    if kind == "verified_query":
        return verified_query_key(body["sql"])
    raise ValueError(f"kind tidak dikenal: {kind!r}")


# ---------------------------------------------------------------------------
# Draft heuristik
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetInput:
    """Masukan draft: satu Dataset beserta profil kolomnya."""

    dataset_id: str
    table: str
    columns: tuple[ColumnProfile, ...]
    #: ``nama ternormalisasi → nama asli`` (label diambil dari nama asli).
    original_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DraftEntry:
    kind: str
    entry_key: str
    body: dict[str, Any]
    dataset_id: str | None = None


@dataclass(frozen=True)
class ExistingEntry:
    entry_key: str
    status: str
    source: str


_ROLE_AGG: dict[str, Aggregation] = {
    "measure": "sum",
    "identifier": "count_distinct",
    "dimension": "none",
    "time": "none",
}


def default_aggregation(role: ColumnRole) -> Aggregation:
    return _ROLE_AGG.get(role, "none")


def heuristic_label(original: str) -> str:
    """``total_amt`` → ``Total Amt``; kapital hanya pada huruf pertama tiap kata."""
    words = str(original).replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words)


def _in_range(lo: Any, hi: Any, upper: float) -> bool:
    if isinstance(lo, bool) or isinstance(hi, bool):
        return False
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return False
    return 0 <= lo and hi <= upper


def heuristic_format(profile: ColumnProfile) -> NumberFormat:
    decimals = 2 if profile.type == "float" else 0
    if profile.type not in _NUMERIC:
        return NumberFormat(style="number", decimals=0)
    if _PERCENT_NAME.search(profile.name) and (
        _in_range(profile.min, profile.max, 1) or _in_range(profile.min, profile.max, 100)
    ):
        return NumberFormat(style="percent", currency=None, decimals=max(decimals, 1))
    if _CURRENCY_NAME.search(profile.name):
        return NumberFormat(style="currency", currency="IDR", decimals=0)
    return NumberFormat(style="number", decimals=decimals)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def heuristic_draft(datasets: Sequence[DatasetInput]) -> list[DraftEntry]:
    """Draft deterministik (Req 32.2): Semantic_Column per kolom + metrik dasar.

    Metrik: ``total_<kolom>`` = ``SUM(kolom)`` per kolom ``measure`` (nama
    menjadi ``total_<tabel>_<kolom>`` bila kolom yang sama ada di beberapa
    tabel) dan ``jumlah_baris_<tabel>`` = ``COUNT(*)``.
    """
    out: list[DraftEntry] = []
    measure_owners: dict[str, int] = {}
    for ds in datasets:
        for col in ds.columns:
            if col.role == "measure" and col.type in _NUMERIC:
                measure_owners[col.name] = measure_owners.get(col.name, 0) + 1

    for ds in sorted(datasets, key=lambda d: d.table):
        for col in ds.columns:
            original = ds.original_names.get(col.name, col.name)
            is_enum = col.role == "dimension" and col.distinct_count <= ENUM_MAX_DISTINCT
            body = SemanticColumn(
                table=ds.table,
                column=col.name,
                label=heuristic_label(original),
                default_aggregation=default_aggregation(col.role),
                format=heuristic_format(col),
                is_enum=is_enum,
                enum_values=[v for v, _ in col.top_values] if is_enum else [],
            ).model_dump(mode="json")
            out.append(DraftEntry("column", column_key(ds.table, col.name), body, ds.dataset_id))

        for col in ds.columns:
            if col.role != "measure" or col.type not in _NUMERIC:
                continue
            name = (
                f"total_{col.name}"
                if measure_owners.get(col.name, 0) <= 1
                else f"total_{ds.table}_{col.name}"
            )
            metric = BusinessMetric(
                name=normalize_key_part(name)[:64] or "total",
                label=f"Total {heuristic_label(ds.original_names.get(col.name, col.name))}",
                expr=f"SUM({_quote(col.name)})",
                base_table=ds.table,
                format=heuristic_format(col),
                good_direction="up",
            ).model_dump(mode="json")
            out.append(DraftEntry("metric", metric_key(metric["name"]), metric, None))

        count_name = normalize_key_part(f"jumlah_baris_{ds.table}")[:64]
        count_metric = BusinessMetric(
            name=count_name,
            label=f"Jumlah baris {ds.table}",
            expr="COUNT(*)",
            base_table=ds.table,
            format=NumberFormat(style="number", decimals=0),
            good_direction="neutral",
        ).model_dump(mode="json")
        out.append(DraftEntry("metric", metric_key(count_name), count_metric, None))
    return out


def merge_draft(
    existing: Iterable[ExistingEntry],
    draft: Iterable[DraftEntry],
    rejected_keys: Iterable[str],
) -> list[DraftEntry]:
    """Entri draft yang boleh di-upsert sebagai ``candidate`` (Req 32.5, 32.6).

    Dibuang: kunci yang ditolak, kunci entri ``confirmed``/``rejected``, atau
    bersumber ``user``. Duplikat kunci di ``draft``: yang pertama menang.
    Hasil diurutkan berdasarkan ``entry_key`` (deterministik).
    """
    blocked = set(rejected_keys)
    for e in existing:
        if e.status in ("confirmed", "rejected") or e.source == "user":
            blocked.add(e.entry_key)
    chosen: dict[str, DraftEntry] = {}
    for entry in draft:
        if entry.entry_key in blocked or entry.entry_key in chosen:
            continue
        chosen[entry.entry_key] = entry
    return [chosen[k] for k in sorted(chosen)]


# ---------------------------------------------------------------------------
# Validasi ekspresi metrik
# ---------------------------------------------------------------------------


def metric_probe_sql(expr: str, base_table: str) -> str:
    return f"SELECT {expr} AS value FROM {_quote(base_table)}"


def _inside_agg(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        parent = parent.parent
    return False


def check_metric_expr(expr: str, base_table: str, columns: Iterable[str]) -> None:
    """Validasi struktur ``expr`` (tanpa skema Polars); raise ``MetricError``."""
    try:
        tree = sqlglot.parse_one(expr, dialect=DIALECT)
    except (SqlglotError, ValueError) as e:
        raise MetricError("METRIC_INVALID", f"ekspresi tidak dapat di-parse: {e}") from e
    if tree is None:
        raise MetricError("METRIC_INVALID", "ekspresi kosong")
    if isinstance(tree, exp.Query) or tree.find(exp.Query) is not None:
        raise MetricError("METRIC_INVALID", "subquery tidak diizinkan dalam ekspresi metrik")
    if tree.find(exp.Window) is not None:
        raise MetricError("METRIC_INVALID", "window function tidak diizinkan dalam ekspresi metrik")
    if tree.find(exp.AggFunc) is None:
        raise MetricError(
            "METRIC_NOT_AGGREGATE",
            "ekspresi wajib memakai fungsi agregat (SUM, AVG, COUNT, MIN, MAX)",
        )
    known = set(columns)
    for col in tree.find_all(exp.Column):
        table = col.table
        if table and table != base_table:
            raise MetricError(
                "METRIC_INVALID",
                f"kolom '{table}.{col.name}' bukan milik tabel dasar '{base_table}'",
                {"table": table, "column": col.name},
            )
        if col.name not in known:
            raise MetricError(
                "METRIC_INVALID",
                f"kolom '{col.name}' tidak ada di tabel '{base_table}'",
                {"table": base_table, "column": col.name, "available": sorted(known)},
            )
        if not _inside_agg(col):
            raise MetricError(
                "METRIC_NOT_AGGREGATE",
                f"kolom '{col.name}' dipakai di luar fungsi agregat",
                {"column": col.name},
            )


def validate_metric_static(
    metric: BusinessMetric,
    tables: Mapping[str, Sequence[Any]],
    confirmed_relations: Iterable[Any] = (),
) -> str:
    """Validasi metrik tanpa eksekusi: struktur ``expr`` + probe lolos V1–V6.

    ``tables``: ``{table_name: kolom}`` (format sama dengan ``analyze_sql``).
    Mengembalikan SQL probe. Raise ``MetricError``.
    """
    if metric.base_table not in tables:
        raise MetricError(
            "METRIC_INVALID",
            f"tabel dasar '{metric.base_table}' tidak ada di Workspace",
            {"table": metric.base_table, "tables": sorted(tables)},
        )
    raw_cols = tables[metric.base_table]
    if isinstance(raw_cols, Mapping):
        names = [str(k) for k in raw_cols]
    else:
        names = [c if isinstance(c, str) else getattr(c, "name", str(c)) for c in raw_cols]
    check_metric_expr(metric.expr, metric.base_table, names)
    probe = metric_probe_sql(metric.expr, metric.base_table)
    try:
        analyze_sql(probe, tables, confirmed_relations)
    except StudioError as exc:
        raise MetricError(
            "METRIC_INVALID",
            f"probe ditolak SQL_Validator ({exc.code}): {exc.message}",
            {"validator_code": exc.code},
        ) from exc
    return probe
