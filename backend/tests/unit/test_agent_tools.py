"""Unit test Agent_Tools data & jaring pengaman (task 18.8).

Fokus pada kasus yang belum tercakup ``test_dashboard_turn_tools.py``
(APPROVAL_REQUIRED, INSIGHT_NUMBER_MISMATCH, VERSION_CONFLICT sudah diuji di
sana):

* ``after_tool_callback`` menolak hasil tool > 200 baris (Req 27.6);
* ``run_sql`` menyimpan ``query:last_id`` ke state giliran (Req 11.2);
* ``cross_dataset_correlation`` ditolak bila relasi query tidak confirmed
  (Req 14.6);
* smoke ``list_tables``/``get_dataset_profile``/``set_column_roles`` di atas
  Data_Engine sungguhan (parquet kecil).

``ToolContext`` ADK diganti objek duck-typed (``state``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest

from studio.agents.tools.context import STATE_LAST_QUERY_ID, STATE_WORKSPACE_ID, ToolServices
from studio.agents.tools.data_tools import make_data_tools
from studio.agents.tools.dashboard_tools import make_dashboard_tools
from studio.agents.tools.turn_tools import make_turn_tools
from studio.agents.tools.guard import RESULT_TOO_LARGE, after_tool_callback
from studio.agents.turn_policy import begin_turn
from studio.core.models import ColumnInfo
from studio.core.relations import RelationCandidate
from studio.data.engine import DataEngine
from studio.data.profiler import Profiler
from studio.data.worker import InlineQueryRunner
from studio.store.dashboard_store import DashboardStore
from studio.store.db import Database
from studio.store.repos import Repositories

from tests.unit.test_dashboard_turn_tools import MESSAGE  # pesan giliran standar

MAX_ROWS = 200


@dataclass
class DataEnv:
    repos: Repositories
    store: DashboardStore
    engine: DataEngine
    ws_id: str
    session_id: str
    dataset_id: str
    customers_id: str
    tools: dict[str, Any]
    dash_tools: dict[str, Any]
    turn_tools: dict[str, Any]

    def ctx(self, message: str = MESSAGE, invocation_id: str = "inv-1") -> SimpleNamespace:
        state: dict[str, Any] = {STATE_WORKSPACE_ID: self.ws_id}
        begin_turn(state, user_message=message, invocation_id=invocation_id)
        return SimpleNamespace(state=state, invocation_id=invocation_id, session=SimpleNamespace(id=self.session_id))

    async def allow(self, ctx: SimpleNamespace) -> None:
        out = await self.turn_tools["classify_turn"](
            "dashboard_modification", True, ctx, evidence="tambahkan chart revenue"
        )
        assert out["ok"] and out["mutation_allowed"] is True


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[DataEnv]:
    data_dir = tmp_path / "data"
    uploads = data_dir / "uploads" / "ws"
    uploads.mkdir(parents=True)
    db = await Database(tmp_path / "studio.db").open()
    runner = InlineQueryRunner(timeout_s=10.0)
    try:
        await runner.start()
        repos = Repositories(db)
        engine = DataEngine(repos, runner, data_dir)
        store = DashboardStore(repos, None)
        profiler = Profiler(repos, None, engine)
        ws = await repos.workspaces.create("WS")
        session = await repos.chat_sessions.create(ws.id, "Sesi")

        # Dataset sungguhan: sales.parquet (2 tabel: sales + customers).
        sales_path = uploads / "sales.parquet"
        pl.DataFrame({"region": ["Jawa", "Bali"], "revenue": [1500, 700]}).write_parquet(sales_path)
        customers_path = uploads / "customers.parquet"
        pl.DataFrame(
            {"region": ["Jawa", "Bali"], "nama": ["Pt Jawa", "Pt Bali"]}
        ).write_parquet(customers_path)

        ds_sales = (
            await repos.datasets.create(
                ws.id,
                table_name="sales",
                source_name="sales.csv",
                parquet_path=f"ws/{sales_path.name}",
                schema=[
                    ColumnInfo(name="region", type="string"),
                    ColumnInfo(name="revenue", type="integer"),
                ],
                column_mapping=[("Region", "region"), ("Revenue", "revenue")],
                row_count=2,
            )
        ).id
        ds_customers = (
            await repos.datasets.create(
                ws.id,
                table_name="customers",
                source_name="customers.csv",
                parquet_path=f"ws/{customers_path.name}",
                schema=[
                    ColumnInfo(name="region", type="string"),
                    ColumnInfo(name="nama", type="string"),
                ],
                column_mapping=[("Region", "region"), ("Nama", "nama")],
                row_count=2,
            )
        ).id

        services = ToolServices(repos=repos, engine=engine, profiler=profiler, dashboard_store=store)
        tools = make_data_tools(services)
        dash_tools = make_dashboard_tools(services)
        turn_tools = make_turn_tools(services)
        yield DataEnv(
            repos, store, engine, ws.id, session.id, ds_sales, ds_customers, tools, dash_tools, turn_tools
        )
    finally:
        await runner.close()
        await db.close()


# ---------------------------------------------------------------------------
# after_tool_callback (Req 27.6)
# ---------------------------------------------------------------------------


def _tool_named(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_after_tool_callback_menolak_hasil_lebih_dari_200_baris() -> None:
    besar = {"ok": True, "rows": [["x"]] * (MAX_ROWS + 1)}
    out = after_tool_callback(_tool_named("run_sql"), {}, None, besar)
    assert out is not None and out["ok"] is False
    assert out["error"]["code"] == RESULT_TOO_LARGE
    assert out["error"]["message"].startswith("Hasil tool run_sql")
    assert out["error"]["details"]["max_rows"] == MAX_ROWS


def test_after_tool_callback_menerima_hasil_sampai_200_baris() -> None:
    pas = {"ok": True, "rows": [["x"]] * MAX_ROWS, "records": [], "sample_rows": []}
    assert after_tool_callback(_tool_named("run_sql"), {}, None, pas) is None
    # Non-data juga lolos.
    assert after_tool_callback(_tool_named("list_tables"), {}, None, {"ok": True}) is None


def test_after_tool_callback_memeriksa_records_dan_sample_rows() -> None:
    for payload in (
        {"ok": True, "records": [["y"]] * (MAX_ROWS + 1)},
        {"ok": True, "sample_rows": [["z"]] * (MAX_ROWS + 1)},
    ):
        out = after_tool_callback(_tool_named("tool_x"), {}, None, payload)
        assert out is not None and out["error"]["code"] == RESULT_TOO_LARGE


# ---------------------------------------------------------------------------
# run_sql & query:last_id (Req 11.2)
# ---------------------------------------------------------------------------


async def test_run_sql_menyimpan_query_last_id_dan_hasil_terpotong(env: DataEnv) -> None:
    ctx = env.ctx()
    out = await env.tools["run_sql"]("SELECT region, revenue FROM sales", ctx)
    assert out["ok"], out
    assert out["row_count"] == 2 and out["truncated"] is False
    saved = ctx.state[STATE_LAST_QUERY_ID]
    assert saved == out["query_id"]

    # get_query_schema/get_query_result tanpa query_id memakai state terakhir.
    schema = await env.tools["get_query_schema"](ctx)
    assert schema["ok"] and [c["name"] for c in schema["columns"]] == ["region", "revenue"]
    result = await env.tools["get_query_result"](ctx, 0, 10)
    assert result["ok"] and result["row_count"] == 2


async def test_run_sql_menyimpan_query_terbaru(env: DataEnv) -> None:
    ctx = env.ctx()
    first = await env.tools["run_sql"]("SELECT region FROM sales", ctx)
    second = await env.tools["run_sql"]("SELECT revenue FROM sales", ctx)
    assert ctx.state[STATE_LAST_QUERY_ID] == second["query_id"] != first["query_id"]


async def test_run_sql_salah_menghitung_retry_tanpa_hilangkan_state(env: DataEnv) -> None:
    ctx = env.ctx()
    out = await env.tools["run_sql"]("SELECT kolom_tidak_ada FROM sales", ctx)
    assert out["ok"] is False and out["retries_left"] == 3
    assert STATE_LAST_QUERY_ID not in ctx.state
    out = await env.tools["run_sql"]("DROP TABLE sales", ctx)
    assert out["ok"] is False and out["retries_left"] == 2
    # Berhasil setelah gagal → state query terisi & retry pulih.
    out = await env.tools["run_sql"]("SELECT region FROM sales", ctx)
    assert out["ok"] and ctx.state[STATE_LAST_QUERY_ID] == out["query_id"]


# ---------------------------------------------------------------------------
# list_tables / get_dataset_profile / set_column_roles (smoke)
# ---------------------------------------------------------------------------


async def test_list_tables_menampilkan_dataset_dan_relasi_confirmed(env: DataEnv) -> None:
    ctx = env.ctx()
    out = await env.tools["list_tables"](ctx)
    assert out["ok"]
    tables = {t["context"]["table_name"] for t in out["tables"]}
    assert {"sales", "customers"} <= tables


async def test_set_column_roles_validasi_peran(env: DataEnv) -> None:
    ctx = env.ctx()
    out = await env.tools["set_column_roles"]("sales", {"region": "dimension"}, ctx)
    assert out["ok"], out
    assert out["roles"]["region"] == "dimension"

    out = await env.tools["set_column_roles"]("sales", {"region": "bukan_peran"}, ctx)
    assert out["ok"] is False and out["error"]["code"] == "INVALID_ROLES"
    assert "dimension" in str(out["error"]["details"]["valid_roles"])


# ---------------------------------------------------------------------------
# cross_dataset_correlation tanpa relasi confirmed (Req 14.6)
# ---------------------------------------------------------------------------


async def test_cross_dataset_correlation_ditolak_tanpa_relasi_confirmed(env: DataEnv) -> None:
    # Relasi candidate → dikonfirmasi agar JOIN bisa dieksekusi → lalu ditolak
    # kembali: query tersimpan menyimpan relations_used yang tidak lagi
    # confirmed, sehingga insight lintas-dataset harus ditolak (Req 14.6).
    (rel,) = await env.repos.relations.upsert_candidates(
        env.ws_id,
        [
            RelationCandidate(
                candidate_key="customers.region|sales.region",
                from_dataset_id=env.customers_id,
                from_table="customers",
                from_column="region",
                to_dataset_id=env.dataset_id,
                to_table="sales",
                to_column="region",
                cardinality="one_to_many",
                overlap_pct=100.0,
            )
        ],
    )
    await env.repos.relations.confirm(rel.id, workspace_id=env.ws_id)

    sql = "SELECT s.region, c.nama FROM sales s JOIN customers c ON s.region = c.region"
    out = await env.tools["run_sql"](sql, env.ctx())
    assert out["ok"], out

    # Relasi tidak lagi confirmed.
    await env.repos.relations.reject(rel.id, workspace_id=env.ws_id)

    ctx = env.ctx()
    ctx.state[STATE_LAST_QUERY_ID] = out["query_id"]
    await env.allow(ctx)
    insight = await env.dash_tools["add_insight"](
        0, "cross_dataset_correlation", "Korelasi", "Jawa mencatat revenue 1.500", ctx
    )
    assert insight["ok"] is False
    assert insight["error"]["code"] == "INVALID_INSIGHT_TYPE"
    assert rel.id in insight["error"]["details"]["relations_not_confirmed"]
