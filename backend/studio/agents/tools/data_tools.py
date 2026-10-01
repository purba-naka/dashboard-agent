"""Agent_Tools data: katalog, profil, relasi, dan SQL (Query/Profiler/Chart/Insight agent).

Semua tool dibuat oleh :func:`make_data_tools` yang menutup satu
:class:`~studio.agents.tools.context.ToolServices`; Workspace dibaca dari session
state ``workspace_id`` (lihat ``tools/context.py``). Setiap tool:

* async, parameter terakhir ``tool_context: ToolContext`` (disuntik ADK, tidak
  masuk deklarasi fungsi ke LLM);
* dibungkus :func:`~studio.agents.tools.guard.llm_output` → selalu mengembalikan
  dict JSON ``{"ok": True, ...}`` atau ``{"ok": False, "error": {...}}`` dan
  tidak pernah raise ke LLM;
* data Dataset hanya lewat ``build_dataset_context`` dan hasil query hanya lewat
  ``truncate_result`` (≤ 200 baris, Req 27.1–27.6).

=============================  ================================================
Tool                           Agent (design: Agents)
=============================  ================================================
``list_tables``                Query_Agent
``run_sql``                    Query_Agent, Insight_Agent (retry ``sql`` 1 + 3)
``get_query_schema``           Chart_Designer_Agent
``get_query_result``           Insight_Agent
``get_dataset_profile``        Data_Profiler_Agent
``set_column_roles``           Data_Profiler_Agent (hanya 4 peran, Req 6.3)
``compute_relation_candidates``  Data_Profiler_Agent (Req 7.1, 7.2)
=============================  ================================================

Pemakaian (18.9)::

    tools = make_data_tools(services)
    LlmAgent(..., tools=[tools["list_tables"], tools["run_sql"]],
             after_tool_callback=guard.after_tool_callback)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any, get_args

import polars as pl
from google.adk.tools import ToolContext

from studio.agents.tools.context import STATE_LAST_QUERY_ID, ToolServices, workspace_id_of
from studio.agents.tools.context import resolve_dataset as _resolve_dataset
from studio.agents.tools.guard import error_result, exhausted_result, llm_output, ok_result
from studio.agents.turn_policy import bind_tool_context, record_success
from studio.api.errors import StudioError
from studio.core.models import ColumnProfile, ColumnRole, QueryResult
from studio.core.privacy import MAX_RESULT_ROWS, build_dataset_context, truncate_result
from studio.store.repos import DatasetProfileRecord, DatasetRecord, RelationRecord

log = logging.getLogger(__name__)

__all__ = [
    "DATA_TOOL_NAMES",
    "VALID_ROLES",
    "make_data_tools",
]

#: Peran kolom yang valid (Req 6.3).
VALID_ROLES: tuple[str, ...] = tuple(get_args(ColumnRole))

DATA_TOOL_NAMES: tuple[str, ...] = (
    "list_tables",
    "run_sql",
    "get_query_schema",
    "get_query_result",
    "get_dataset_profile",
    "set_column_roles",
    "compute_relation_candidates",
)


def _relation_dict(rec: RelationRecord) -> dict[str, Any]:
    return {
        "relation_id": rec.id,
        "from_table": rec.from_table,
        "from_column": rec.from_column,
        "to_table": rec.to_table,
        "to_column": rec.to_column,
        "cardinality": rec.cardinality,
        "overlap_pct": rec.overlap_pct,
        "status": rec.status,
    }


def make_data_tools(services: ToolServices) -> dict[str, Callable[..., Any]]:
    """Buat tool data yang terikat ke ``services``; kunci dict = nama tool."""
    repos = services.repos
    engine = services.engine
    profiler = services.profiler
    guard = llm_output(max_sample_rows=services.sample_rows)

    # -- helper -------------------------------------------------------------

    async def resolve_dataset(ws_id: str, dataset: str) -> DatasetRecord:
        return await _resolve_dataset(repos, ws_id, dataset)

    async def load_sample(dataset: DatasetRecord, n: int) -> pl.DataFrame | None:
        if n <= 0:
            return None
        path = engine.parquet_path(dataset)
        try:
            return await asyncio.to_thread(lambda: pl.scan_parquet(path).head(n).collect())
        except Exception:  # noqa: BLE001 - konteks tetap dikirim tanpa sampel
            log.warning("Gagal membaca sample rows Dataset %s", dataset.id, exc_info=True)
            return None

    async def dataset_context(
        dataset: DatasetRecord, profile: DatasetProfileRecord | None
    ) -> dict[str, Any]:
        settings = services.privacy_for(dataset)
        # Tidak membaca data sama sekali bila toggle "jangan kirim sample rows" aktif.
        sample = None if settings.no_samples else await load_sample(dataset, settings.sample_rows)
        columns = list(profile.columns) if profile is not None else []
        return build_dataset_context(dataset, columns, sample, settings)

    async def fresh_profile(dataset: DatasetRecord) -> DatasetProfileRecord:
        """Profil tersimpan untuk ``data_version`` saat ini; dihitung Data_Engine bila belum ada."""
        profile = await repos.profiles.get_or_none(dataset.id)
        if profile is None or profile.data_version != dataset.data_version:
            profile = await profiler.profile_dataset(dataset)
        return profile

    def query_id_from(tool_context: ToolContext, query_id: str | None) -> str:
        qid = (query_id or "").strip() or tool_context.state.get(STATE_LAST_QUERY_ID)
        if not qid:
            raise StudioError(
                "NO_QUERY",
                "Belum ada query. Jalankan run_sql terlebih dahulu atau berikan query_id.",
                http_status=404,
            )
        return str(qid)

    # -- katalog --------------------------------------------------------------

    @guard
    async def list_tables(tool_context: ToolContext, table: str | None = None) -> dict[str, Any]:
        """Detail tabel Workspace untuk menulis SQL.

        Args:
            table: opsional nama tabel/dataset_id; isi agar hanya tabel itu yang
                dikembalikan (hemat). Kosong = semua tabel.

        Mengembalikan setiap tabel (nama tabel SQL, skema kolom, profil kolom, dan
        beberapa sample rows bila diizinkan pengaturan privasi) serta
        Confirmed_Relation yang boleh dipakai sebagai kondisi JOIN antar-tabel.
        """
        ws_id = workspace_id_of(tool_context)
        if table:
            datasets = [await resolve_dataset(ws_id, table)]
        else:
            datasets = await repos.datasets.list_by_workspace(ws_id)
        profiles = await repos.profiles.list_by_workspace(ws_id)
        tables = []
        for ds in datasets:
            profile = profiles.get(ds.id)
            tables.append(
                {
                    "dataset_id": ds.id,
                    "row_count": ds.row_count,
                    "profiled": profile is not None and profile.data_version == ds.data_version,
                    "context": await dataset_context(ds, profile),
                }
            )
        relations = await repos.relations.list_confirmed(ws_id)
        return ok_result(
            tables=tables,
            confirmed_relations=[_relation_dict(r) for r in relations],
        )

    # -- SQL ----------------------------------------------------------------

    @guard
    async def run_sql(sql: str, tool_context: ToolContext) -> dict[str, Any]:
        """Validasi lalu eksekusi satu pernyataan SELECT atas tabel Workspace.

        Args:
            sql: satu pernyataan SELECT/WITH (Polars SQL). JOIN antar-tabel hanya
                boleh memakai Confirmed_Relation dari list_tables.

        Sukses: ``query_id``, ``tables_used``, ``columns``, maks 200 ``rows``,
        ``row_count`` total, dan ``truncated``. Gagal: ``error`` (pesan Polars +
        katalog tabel/kolom) dan ``retries_left``; perbaiki SQL lalu panggil lagi.
        Bila ``RETRY_EXHAUSTED``, hentikan dan laporkan error terakhir.
        """
        state = bind_tool_context(tool_context)
        if (exhausted := exhausted_result(state, "sql")) is not None:
            return exhausted
        ws_id = workspace_id_of(tool_context)
        try:
            if not isinstance(sql, str) or not sql.strip():
                raise StudioError("EMPTY_SQL", "SQL kosong.", http_status=422)
            result = await engine.execute(
                ws_id, sql, timeout_s=services.query_timeout_s, created_by="agent"
            )
        except StudioError as exc:
            return error_result(exc, state=state, kind="sql")
        except Exception as exc:  # noqa: BLE001 - kegagalan eksekusi tetap dihitung retry
            log.exception("run_sql gagal di luar StudioError")
            wrapped = StudioError(
                "SQL_EXECUTION_ERROR", f"{type(exc).__name__}: {exc}", http_status=500
            )
            return error_result(wrapped, state=state, kind="sql")

        record_success(state, "sql")
        tool_context.state[STATE_LAST_QUERY_ID] = result.query_id
        record = await repos.queries.get(result.query_id, workspace_id=ws_id)
        return ok_result(
            query_id=result.query_id,
            tables_used=list(record.tables_used),
            relations_used=sorted(record.relations_used),
            **truncate_result(result),
        )

    @guard
    async def get_query_schema(
        tool_context: ToolContext, query_id: str | None = None
    ) -> dict[str, Any]:
        """Skema output query tersimpan (tanpa baris data) untuk menyusun Chart_Spec.

        Args:
            query_id: id dari run_sql; kosong = query terakhir sesi ini.
        """
        ws_id = workspace_id_of(tool_context)
        record = await repos.queries.get(query_id_from(tool_context, query_id), workspace_id=ws_id)
        return ok_result(
            query_id=record.id,
            sql=record.sql,
            columns=[c.model_dump() for c in record.output_schema],
            row_count=record.row_count,
            tables_used=list(record.tables_used),
            lineage=dict(record.lineage),
        )

    @guard
    async def get_query_result(
        tool_context: ToolContext, query_id: str | None = None, limit: int = MAX_RESULT_ROWS
    ) -> dict[str, Any]:
        """Hasil query tersimpan (maks 200 baris) sebagai bukti insight.

        Args:
            query_id: id dari run_sql; kosong = query terakhir sesi ini.
            limit: jumlah baris maksimum yang dikembalikan (1–200).
        """
        ws_id = workspace_id_of(tool_context)
        record = await repos.queries.get(query_id_from(tool_context, query_id), workspace_id=ws_id)
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = MAX_RESULT_ROWS
        n = max(0, min(MAX_RESULT_ROWS, n))
        snapshot = QueryResult(
            query_id=record.id,
            columns=list(record.output_schema),
            rows=[list(r) for r in record.rows[:n]],
            row_count=record.row_count,
            truncated_for_storage=record.truncated_for_storage,
        )
        return ok_result(
            query_id=record.id,
            sql=record.sql,
            tables_used=list(record.tables_used),
            **truncate_result(snapshot, n),
        )

    # -- profil & relasi ------------------------------------------------------

    @guard
    async def get_dataset_profile(dataset: str, tool_context: ToolContext) -> dict[str, Any]:
        """Profil kolom (statistik Data_Engine), kualitas data, dan peran kolom satu Dataset.

        Args:
            dataset: nama tabel atau dataset_id.

        Gunakan statistik ini apa adanya; jangan mengarang nilai statistik.
        """
        ws_id = workspace_id_of(tool_context)
        ds = await resolve_dataset(ws_id, dataset)
        profile = await fresh_profile(ds)
        return ok_result(
            dataset_id=ds.id,
            row_count=ds.row_count,
            data_version=ds.data_version,
            quality=dict(profile.quality),
            roles={c.name: c.role for c in profile.columns},
            context=await dataset_context(ds, profile),
        )

    @guard
    async def set_column_roles(
        dataset: str, roles: dict[str, str], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Ubah peran kolom Dataset.

        Args:
            dataset: nama tabel atau dataset_id.
            roles: pemetaan nama kolom → peran; peran hanya salah satu dari
                dimension, measure, time, identifier.
        """
        ws_id = workspace_id_of(tool_context)
        if not isinstance(roles, Mapping) or not roles:
            raise StudioError("INVALID_ROLES", "roles harus objek {kolom: peran} yang tidak kosong.")
        ds = await resolve_dataset(ws_id, dataset)
        profile = await fresh_profile(ds)
        by_name = {c.name: c for c in profile.columns}
        unknown = sorted(str(c) for c in roles if c not in by_name)
        invalid = {str(c): r for c, r in roles.items() if r not in VALID_ROLES}
        if unknown or invalid:
            raise StudioError(
                "INVALID_ROLES",
                "Kolom tidak dikenal atau peran tidak valid.",
                {
                    "unknown_columns": unknown,
                    "invalid_roles": invalid,
                    "valid_roles": list(VALID_ROLES),
                    "columns": list(by_name),
                },
                http_status=422,
            )
        columns: list[ColumnProfile] = [
            c.model_copy(update={"role": roles[c.name]}) if c.name in roles else c
            for c in profile.columns
        ]
        updated = await repos.profiles.upsert(
            ds.id, data_version=profile.data_version, columns=columns, quality=profile.quality
        )
        return ok_result(
            dataset_id=ds.id,
            table_name=ds.table_name,
            roles={c.name: c.role for c in updated.columns},
        )

    @guard
    async def compute_relation_candidates(
        tool_context: ToolContext, dataset: str | None = None
    ) -> dict[str, Any]:
        """Hitung Relation_Candidate antar-Dataset Workspace (Data_Engine).

        Args:
            dataset: opsional nama tabel/dataset_id; hanya pasangan yang melibatkan
                Dataset ini yang dihitung ulang.

        Setiap kandidat berisi tabel/kolom sumber & tujuan, kardinalitas, dan
        overlap_pct hasil perhitungan. Pasangan yang pernah ditolak tidak diusulkan.
        """
        ws_id = workspace_id_of(tool_context)
        dataset_id = None
        if dataset:
            dataset_id = (await resolve_dataset(ws_id, dataset)).id
        records = await profiler.detect_relations(ws_id, dataset_id=dataset_id)
        return ok_result(
            candidates=[_relation_dict(r) for r in records if r.status == "candidate"],
            confirmed=[_relation_dict(r) for r in records if r.status == "confirmed"],
        )

    tools = {
        "list_tables": list_tables,
        "run_sql": run_sql,
        "get_query_schema": get_query_schema,
        "get_query_result": get_query_result,
        "get_dataset_profile": get_dataset_profile,
        "set_column_roles": set_column_roles,
        "compute_relation_candidates": compute_relation_candidates,
    }
    assert tuple(tools) == DATA_TOOL_NAMES
    return tools
