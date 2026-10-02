"""Data_Engine: registrasi tabel Workspace, SQL_Validator penuh, dan eksekusi query.

Alur (Req 10.1, 10.2, 10.5, 10.7, 11.1, 11.2, 22.2)::

    validate(ws, sql)
      ├─ analyze_sql(sql, katalog, Confirmed_Relation)       # V1–V6 (statik, sqlglot)
      └─ worker mode "schema": SQLContext.execute(sql)
                                .collect_schema()            # V7 (lazy, tanpa eksekusi)
    execute(ws, sql, filters)
      ├─ validate(...)
      ├─ plan_propagation(RelationGraph(confirmed), filters)
      ├─ worker mode "execute" (QueryWorkerPool / InlineQueryRunner)
      └─ simpan ke tabel ``queries`` (SQL apa adanya + snapshot ≤ 1.000 baris)
    run_saved(query_id, filters)                             # render/refresh tanpa query baru

Setiap Dataset Workspace didaftarkan dengan ``table_name``-nya (unik per Workspace)
sebagai ``pl.scan_parquet`` di dalam worker; Global_Filter/Cross_Filter diterapkan
dengan mendaftarkan LazyFrame terfilter bernama sama. **Teks SQL tidak pernah
diubah**: string yang divalidasi, dieksekusi, dan disimpan identik dengan masukan.

Satu-satunya query turunan adalah ``SELECT COUNT(*) FROM (<sql>)`` untuk menghitung
jumlah baris total bila hasil melebihi ``max_result_rows``; query ini tidak
disimpan dan tidak memengaruhi SQL yang direferensikan Chart_Spec/Insight_Card.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import polars as pl

from studio.api.errors import StudioError
from studio.core.filters import Predicate, dump_filter_set, parse_filter_set
from studio.core.identifiers import safe_join
from studio.core.models import Cell, ColumnInfo, LogicalType, QueryResult
from studio.core.propagation import PropagationPlan, RelationGraph, plan_propagation
from studio.core.sql_rules import ColumnRef, ConfirmedRelation, SqlAnalysis, analyze_sql
from studio.data.worker import DEFAULT_TIMEOUT_S, QueryJob, QueryOutput
from studio.store.repos import (
    MAX_SNAPSHOT_ROWS,
    DatasetRecord,
    QueryCreatedBy,
    QueryRecord,
    RelationRecord,
    Repositories,
    not_found,
)

__all__ = [
    "DEFAULT_MAX_RESULT_ROWS",
    "VALIDATOR_ERROR_CODES",
    "Catalog",
    "DataEngine",
    "QueryRunner",
    "ValidatedQuery",
    "WorkspaceTables",
    "format_catalog",
    "logical_type_of_dtype",
]

log = logging.getLogger(__name__)

#: Batas baris hasil yang dimaterialisasi & dikembalikan ke pemanggil per eksekusi.
#: ``row_count`` tetap jumlah total (dihitung terpisah bila batas terlampaui).
DEFAULT_MAX_RESULT_ROWS = 1_000_000

#: Kode error SQL_Validator; detail error-nya dilengkapi katalog tabel/kolom (Req 11.2).
VALIDATOR_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PARSE_ERROR",
        "MULTIPLE_STATEMENTS",
        "NOT_SELECT",
        "FORBIDDEN_STATEMENT",
        "FORBIDDEN_SOURCE",
        "UNKNOWN_TABLE",
        "UNCONFIRMED_JOIN",
        "POLARS_ERROR",
    }
)

#: ``{table: [(column, logical_type)]}``
Catalog = dict[str, list[tuple[str, LogicalType]]]


class QueryRunner(Protocol):
    """API bersama ``QueryWorkerPool`` dan ``InlineQueryRunner``."""

    async def run(self, job: QueryJob, timeout_s: float | None = None) -> QueryOutput: ...


# ---------------------------------------------------------------------------
# Pemetaan tipe
# ---------------------------------------------------------------------------

_INT_DTYPE = re.compile(r"^U?Int(8|16|32|64|128)$")
_FLOAT_DTYPE = re.compile(r"^Float(16|32|64)$")


def logical_type_of_dtype(dtype: str | pl.DataType) -> LogicalType:
    """dtype Polars (objek atau string ``str(dtype)``) → tipe logis.

    ``Int*/UInt*`` → integer, ``Float*``/``Decimal`` → float, ``Boolean`` → boolean,
    ``Date`` → date, ``Datetime(...)`` → datetime; lainnya (String, Categorical,
    Time, Duration, List, Struct, Null, ...) → string.
    """
    text = str(dtype).strip()
    if _INT_DTYPE.match(text):
        return "integer"
    if _FLOAT_DTYPE.match(text) or text.startswith("Decimal"):
        return "float"
    if text == "Boolean":
        return "boolean"
    if text == "Date":
        return "date"
    if text.startswith("Datetime"):
        return "datetime"
    return "string"


def _to_string_cell(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _to_float_cell(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (Decimal, int)) and not isinstance(value, bool):
        return float(value)
    return value


def _cell_converter(dtype: str, logical: LogicalType) -> Any:
    """Konverter sel untuk kolom yang nilai Python-nya bukan ``Cell`` yang sesuai."""
    if logical == "string" and dtype != "String":
        return _to_string_cell
    if logical == "float" and dtype.startswith("Decimal"):
        return _to_float_cell
    return None


def _frame_rows(df: pl.DataFrame, schema: Sequence[tuple[str, str]]) -> list[list[Cell]]:
    converters = [
        (i, conv)
        for i, (_, dtype) in enumerate(schema)
        if (conv := _cell_converter(dtype, logical_type_of_dtype(dtype))) is not None
    ]
    if not converters:
        return [list(r) for r in df.rows()]
    out: list[list[Cell]] = []
    for r in df.rows():
        row = list(r)
        for i, conv in converters:
            row[i] = conv(row[i])
        out.append(row)
    return out


def _columns(schema: Sequence[tuple[str, str]]) -> list[ColumnInfo]:
    return [ColumnInfo(name=n, type=logical_type_of_dtype(t)) for n, t in schema]


def format_catalog(catalog: Mapping[str, Sequence[tuple[str, str]]]) -> dict[str, list[str]]:
    """Katalog → ``{tabel: ["kolom:tipe", ...]}`` (bentuk detail error, Req 11.2)."""
    return {t: [f"{c}:{ty}" for c, ty in cols] for t, cols in catalog.items()}


def _catalog_text(catalog: Mapping[str, Sequence[tuple[str, str]]]) -> str:
    if not catalog:
        return "(Workspace belum memiliki tabel)"
    return "; ".join(
        f"{t}({', '.join(f'{c}:{ty}' for c, ty in cols)})" for t, cols in catalog.items()
    )


# ---------------------------------------------------------------------------
# Model hasil
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceTables:
    """Snapshot tabel & Confirmed_Relation Workspace untuk satu permintaan."""

    workspace_id: str
    datasets: tuple[DatasetRecord, ...]
    #: ``table_name → path Parquet absolut``
    paths: dict[str, str]
    relations: tuple[ConfirmedRelation, ...]

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(d.table_name for d in self.datasets)

    @property
    def schemas(self) -> dict[str, tuple[ColumnInfo, ...]]:
        return {d.table_name: d.schema for d in self.datasets}

    @property
    def catalog(self) -> Catalog:
        return {d.table_name: [(c.name, c.type) for c in d.schema] for d in self.datasets}

    @property
    def graph(self) -> RelationGraph:
        return RelationGraph(self.relations)

    def dataset_ids_for(self, tables: Iterable[str]) -> tuple[str, ...]:
        wanted = set(tables)
        return tuple(d.id for d in self.datasets if d.table_name in wanted)


@dataclass(frozen=True)
class ValidatedQuery:
    """Output SQL_Validator (V1–V7)."""

    sql: str
    tables_used: tuple[str, ...]
    #: ``relation_id`` Confirmed_Relation yang dipakai sebagai kondisi JOIN.
    relations_used: tuple[str, ...]
    output_schema: list[ColumnInfo]
    #: ``{kolom output (nama Polars): (table, column) | None}``
    lineage: dict[str, ColumnRef | None] = field(default_factory=dict)
    dataset_ids: tuple[str, ...] = ()
    #: Skema Polars mentah ``[(nama, dtype)]`` hasil ``collect_schema``.
    polars_schema: tuple[tuple[str, str], ...] = ()

    def lineage_json(self) -> dict[str, dict[str, str] | None]:
        return {
            col: None if ref is None else {"table": ref[0], "column": ref[1]}
            for col, ref in self.lineage.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "tables_used": list(self.tables_used),
            "relations_used": list(self.relations_used),
            "output_schema": [c.model_dump() for c in self.output_schema],
            "lineage": self.lineage_json(),
            "dataset_ids": list(self.dataset_ids),
        }


def _map_lineage(
    analysis: SqlAnalysis, schema: Sequence[tuple[str, str]]
) -> dict[str, ColumnRef | None]:
    """Nama kolom Polars → lineage; per posisi bila jumlah kolom cocok, selain itu per nama."""
    if len(analysis.output_columns) == len(schema):
        return {name: lin for (name, _), (_, lin) in zip(schema, analysis.output_columns)}
    return {name: analysis.lineage.get(name) for name, _ in schema}


def _relation(rec: RelationRecord) -> ConfirmedRelation:
    return ConfirmedRelation(
        id=rec.id,
        table_a=rec.from_table,
        column_a=rec.from_column,
        table_b=rec.to_table,
        column_b=rec.to_column,
    )


# ---------------------------------------------------------------------------
# DataEngine
# ---------------------------------------------------------------------------


class DataEngine:
    """Validasi & eksekusi SQL atas tabel Workspace melalui worker pool.

    ``runner``: ``QueryWorkerPool`` (produksi; timeout membunuh proses) atau
    ``InlineQueryRunner`` (test). ``data_dir``: ``DATA_DIR``; ``datasets.parquet_path``
    relatif di-resolve terhadap ``DATA_DIR/uploads``. ``max_result_rows``: batas
    baris yang dimaterialisasi per eksekusi (``None`` = tanpa batas).
    """

    def __init__(
        self,
        repos: Repositories,
        runner: QueryRunner,
        data_dir: str | Path,
        *,
        max_result_rows: int | None = DEFAULT_MAX_RESULT_ROWS,
    ) -> None:
        if max_result_rows is not None and max_result_rows < 1:
            raise ValueError("max_result_rows harus >= 1 atau None")
        self.repos = repos
        self.runner = runner
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.uploads_root = self.data_dir / "uploads"
        self.max_result_rows = max_result_rows

    # -- tabel Workspace ----------------------------------------------------

    def parquet_path(self, dataset: DatasetRecord | str) -> Path:
        """Path absolut Parquet dari ``DatasetRecord`` atau nilai ``parquet_path``."""
        raw = dataset.parquet_path if isinstance(dataset, DatasetRecord) else str(dataset)
        path = Path(raw)
        return path if path.is_absolute() else safe_join(self.uploads_root, raw)

    async def workspace_tables(self, ws_id: str) -> WorkspaceTables:
        """Semua Dataset Workspace (nama tabel unik, Req 10.1) + Confirmed_Relation."""
        await self.repos.workspaces.get(ws_id)  # NOT_FOUND bila Workspace tidak ada
        datasets = tuple(await self.repos.datasets.list_by_workspace(ws_id))
        relations = tuple(_relation(r) for r in await self.repos.relations.list_confirmed(ws_id))
        return WorkspaceTables(
            workspace_id=ws_id,
            datasets=datasets,
            paths={d.table_name: str(self.parquet_path(d)) for d in datasets},
            relations=relations,
        )

    async def catalog(self, ws_id: str) -> Catalog:
        """``{tabel: [(kolom, tipe)]}`` untuk prompt/pesan error Query_Agent."""
        return (await self.workspace_tables(ws_id)).catalog

    # -- SQL_Validator ------------------------------------------------------

    async def validate(
        self, ws_id: str, sql: str, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> ValidatedQuery:
        """SQL_Validator penuh: V1–V6 statik lalu V7 ``collect_schema`` (Req 11.1)."""
        return await self._validate(await self.workspace_tables(ws_id), sql, timeout_s)

    async def _validate(
        self, tables: WorkspaceTables, sql: str, timeout_s: float
    ) -> ValidatedQuery:
        analysis = self._analyze(tables, sql)
        job = QueryJob(tables=tables.paths, sql=sql, mode="schema")
        try:
            out = await self.runner.run(job, timeout_s=timeout_s)  # V7
        except StudioError as exc:
            raise self._with_catalog(exc, tables) from exc
        schema = tuple(out.schema)
        return ValidatedQuery(
            sql=sql,
            tables_used=analysis.tables_used,
            relations_used=analysis.relations_used,
            output_schema=_columns(schema),
            lineage=_map_lineage(analysis, schema),
            dataset_ids=tables.dataset_ids_for(analysis.tables_used),
            polars_schema=schema,
        )

    async def validate_metric(
        self, ws_id: str, metric: Any, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> ValidatedQuery:
        """Validasi Business_Metric: struktur ``expr`` + probe V1–V7 (Req 31.5, 31.6)."""
        from studio.core.semantic import MetricError, validate_metric_static

        tables = await self.workspace_tables(ws_id)
        probe = validate_metric_static(metric, tables.schemas, tables.relations)
        try:
            return await self._validate(tables, probe, timeout_s)
        except StudioError as exc:
            if isinstance(exc, MetricError):
                raise
            raise MetricError(
                "METRIC_INVALID",
                f"probe ditolak SQL_Validator ({exc.code}): {exc.message}",
                {"validator_code": exc.code},
            ) from exc

    def _analyze(self, tables: WorkspaceTables, sql: str) -> SqlAnalysis:
        try:
            return analyze_sql(sql, tables.schemas, tables.relations)  # V1–V6
        except StudioError as exc:
            raise self._with_catalog(exc, tables) from exc

    @staticmethod
    def _with_catalog(exc: StudioError, tables: WorkspaceTables) -> StudioError:
        """Lengkapi error validator dengan katalog; ``POLARS_ERROR`` + pesan Polars (Req 11.2)."""
        if exc.code not in VALIDATOR_ERROR_CODES:
            return exc
        catalog = tables.catalog
        details = dict(exc.details)
        details.setdefault("catalog", format_catalog(catalog))
        details.setdefault("tables", list(tables.table_names))
        if exc.code != "POLARS_ERROR":
            exc.details = details
            return exc
        details.setdefault("polars_message", exc.message)
        return StudioError(
            "POLARS_ERROR",
            f"Polars menolak SQL: {exc.message}\nTabel tersedia: {_catalog_text(catalog)}",
            details,
            http_status=exc.http_status or 422,
        )

    # -- eksekusi -----------------------------------------------------------

    async def execute(
        self,
        ws_id: str,
        sql: str,
        filters: Sequence[Any] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        *,
        created_by: QueryCreatedBy = "agent",
    ) -> QueryResult:
        """Validasi → eksekusi via worker → simpan ke ``queries`` (Req 10.2, 10.5, 10.7)."""
        tables = await self.workspace_tables(ws_id)
        validated = await self._validate(tables, sql, timeout_s)
        predicates = parse_filter_set(filters)
        schema, rows, row_count = await self._run(tables, sql, predicates, timeout_s)
        columns = _columns(schema)
        record = await self.repos.queries.create(
            ws_id,
            sql=sql,  # disimpan apa adanya (Req 22.2)
            output_schema=columns,
            rows=rows[:MAX_SNAPSHOT_ROWS],
            row_count=row_count,
            tables_used=validated.tables_used,
            dataset_ids=validated.dataset_ids,
            relations_used=validated.relations_used,
            lineage=validated.lineage_json(),
            filters=dump_filter_set(predicates),
            created_by=created_by,
        )
        return QueryResult(
            query_id=record.id,
            columns=columns,
            rows=rows,
            row_count=row_count,
            truncated_for_storage=row_count > MAX_SNAPSHOT_ROWS,
        )

    async def run_saved(
        self,
        query: str | QueryRecord,
        filters: Sequence[Any] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        *,
        workspace_id: str | None = None,
    ) -> QueryResult:
        """Eksekusi ulang SQL tersimpan dengan filter aktif tanpa membuat query baru.

        Dipakai render/refresh (Global_Filter/Cross_Filter; pemanggil menentukan
        filter per chart, mis. Cross_Filter dikecualikan dari chart sumbernya).
        Aturan statik V1–V6 dicek ulang terhadap tabel & relasi saat ini (mis.
        relasi yang dihapus → ``UNCONFIRMED_JOIN``). ``query_id`` hasil = id tersimpan.
        """
        record = (
            await self.repos.queries.get(query, workspace_id=workspace_id)
            if isinstance(query, str)
            else query
        )
        if workspace_id is not None and record.workspace_id != workspace_id:
            raise not_found("Query", record.id)
        tables = await self.workspace_tables(record.workspace_id)
        self._analyze(tables, record.sql)
        predicates = parse_filter_set(filters)
        schema, rows, row_count = await self._run(tables, record.sql, predicates, timeout_s)
        return QueryResult(
            query_id=record.id,
            columns=_columns(schema),
            rows=rows,
            row_count=row_count,
            truncated_for_storage=row_count > MAX_SNAPSHOT_ROWS,
        )

    def plan_for(
        self, tables: WorkspaceTables, predicates: Sequence[Predicate]
    ) -> PropagationPlan | None:
        """Rencana propagasi filter lewat Confirmed_Relation (``None`` bila tanpa filter)."""
        if not predicates:
            return None
        return plan_propagation(tables.graph, predicates)

    async def _run(
        self,
        tables: WorkspaceTables,
        sql: str,
        predicates: Sequence[Predicate],
        timeout_s: float,
    ) -> tuple[list[tuple[str, str]], list[list[Cell]], int]:
        filters = dump_filter_set(predicates)
        plan = self.plan_for(tables, predicates)
        job = QueryJob(
            tables=tables.paths,
            sql=sql,  # teks SQL tidak diubah (Req 22.2)
            filters=filters,
            plan=plan,
            mode="execute",
            row_limit=self.max_result_rows,
        )
        try:
            out = await self.runner.run(job, timeout_s=timeout_s)
        except StudioError as exc:
            raise self._with_catalog(exc, tables) from exc
        schema = list(out.schema)
        rows = _frame_rows(out.to_frame(), schema)
        row_count = len(rows)
        if out.truncated:
            row_count = await self._count_rows(tables, sql, filters, plan, timeout_s, row_count)
        return schema, rows, row_count

    async def _count_rows(
        self,
        tables: WorkspaceTables,
        sql: str,
        filters: list[dict[str, Any]],
        plan: PropagationPlan | None,
        timeout_s: float,
        fallback: int,
    ) -> int:
        """Jumlah baris total hasil yang terpotong ``max_result_rows`` (query turunan, tidak disimpan)."""
        inner = sql.strip().rstrip(";").strip()
        job = QueryJob(
            tables=tables.paths,
            sql=f"SELECT COUNT(*) AS n FROM ({inner}) AS __studio_count",
            filters=filters,
            plan=plan,
        )
        try:
            out = await self.runner.run(job, timeout_s=timeout_s)
            return int(out.to_frame().item(0, 0))
        except (StudioError, pl.exceptions.PolarsError, ValueError, IndexError) as exc:
            log.warning("Gagal menghitung row_count total; memakai batas baris: %s", exc)
            return fallback

    # -- profiling (task 14.5) ---------------------------------------------
    # ``profile(dataset_id) -> list[ColumnProfile]`` dan ``overlap(a, b) -> OverlapStats``
    # ditambahkan oleh ``studio/data/profiler.py`` di atas ``parquet_path`` /
    # ``workspace_tables`` (scan Parquet per Dataset, satu query agregat lazy).
