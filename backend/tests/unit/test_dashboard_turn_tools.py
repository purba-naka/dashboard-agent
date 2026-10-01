"""Smoke test Agent_Tools Dashboard & giliran (task 18.7).

Memakai SQLite + DashboardStore sungguhan; ``ToolContext`` ADK diganti objek
duck-typed (``state``, ``invocation_id``, ``user_content``, ``session``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.tools import FunctionTool

from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    STATE_DASHBOARD_ID,
    STATE_LAST_CHART_ID,
    STATE_LAST_QUERY_ID,
    STATE_WORKSPACE_ID,
    ToolServices,
    resolve_dataset,
)
from studio.agents.tools.dashboard_tools import DASHBOARD_TOOL_NAMES, make_dashboard_tools
from studio.agents.tools.turn_tools import TURN_TOOL_NAMES, make_turn_tools
from studio.agents.turn_policy import APPROVAL_REQUIRED, RETRY_EXHAUSTED, begin_turn
from studio.core.models import ColumnInfo
from studio.store.dashboard_store import DashboardStore
from studio.store.db import Database
from studio.store.repos import Repositories

MESSAGE = "Tolong tambahkan chart revenue per region ke dashboard"
BAR_OPTION = {
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
}


@dataclass
class Env:
    repos: Repositories
    store: DashboardStore
    ws_id: str
    session_id: str
    query_id: str
    dataset_id: str
    tools: dict[str, Any]

    def ctx(self, message: str = MESSAGE, invocation_id: str = "inv-1") -> SimpleNamespace:
        state: dict[str, Any] = {
            STATE_WORKSPACE_ID: self.ws_id,
            STATE_LAST_QUERY_ID: self.query_id,
        }
        begin_turn(state, user_message=message, invocation_id=invocation_id)
        return SimpleNamespace(
            state=state,
            invocation_id=invocation_id,
            user_content=SimpleNamespace(parts=[SimpleNamespace(text=message)]),
            session=SimpleNamespace(id=self.session_id),
        )

    async def allow(self, ctx: SimpleNamespace) -> None:
        out = await self.tools["classify_turn"](
            "dashboard_modification", True, ctx, evidence="tambahkan chart revenue"
        )
        assert out["ok"] and out["mutation_allowed"] is True


@pytest.fixture
async def env(tmp_path: Path) -> AsyncIterator[Env]:
    db = await Database(tmp_path / "studio.db").open()
    try:
        repos = Repositories(db)
        store = DashboardStore(repos, None)
        ws = await repos.workspaces.create("WS")
        session = await repos.chat_sessions.create(ws.id, "Sesi")
        ds = await repos.datasets.create(
            ws.id,
            table_name="sales",
            source_name="sales.csv",
            parquet_path=f"{ws.id}/sales.parquet",
            schema=[ColumnInfo(name="region", type="string"), ColumnInfo(name="revenue", type="integer")],
            column_mapping=[("Region", "region"), ("Revenue", "revenue")],
            row_count=2,
        )
        query = await repos.queries.create(
            ws.id,
            sql="SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region",
            output_schema=[
                ColumnInfo(name="region", type="string"),
                ColumnInfo(name="revenue", type="integer"),
            ],
            rows=[["Jawa", 1500], ["Bali", 700]],
            tables_used=["sales"],
            dataset_ids=[ds.id],
        )
        services = ToolServices(repos=repos, engine=None, profiler=None, dashboard_store=store)  # type: ignore[arg-type]
        tools = {**make_dashboard_tools(services), **make_turn_tools(services)}
        yield Env(repos, store, ws.id, session.id, query.id, ds.id, tools)
    finally:
        await db.close()


async def test_resolve_dataset_accepts_upload_filename(env: Env) -> None:
    for ref in ("sales", "sales.csv", "Sales.xlsx", env.dataset_id):
        assert (await resolve_dataset(env.repos, env.ws_id, ref)).id == env.dataset_id


async def test_all_tools_have_adk_declarations(env: Env) -> None:
    assert set(env.tools) == set(DASHBOARD_TOOL_NAMES) | set(TURN_TOOL_NAMES)
    for name, fn in env.tools.items():
        decl = FunctionTool(fn)._get_declaration()
        assert decl.name == name
        schema = decl.parameters_json_schema or (
            decl.parameters.model_dump(exclude_none=True) if decl.parameters else {}
        )
        props = schema.get("properties") or {}
        assert "tool_context" not in props
        if name in DASHBOARD_TOOL_NAMES and name != "get_dashboard_state":
            assert "base_version" in props and "base_version" in schema.get("required", [])


async def test_mutation_without_approval_is_rejected(env: Env) -> None:
    ctx = env.ctx(message="apa isi datanya?")
    out = await env.tools["add_chart"](0, "Revenue", "bar", BAR_OPTION, ctx)
    assert out["ok"] is False and out["error"]["code"] == APPROVAL_REQUIRED
    assert await env.repos.dashboards.list_by_workspace(env.ws_id) == []

    # Evidence yang bukan kutipan verbatim tidak membuka gate.
    res = await env.tools["classify_turn"]("dashboard_modification", True, ctx, evidence="buat chart")
    assert res["ok"] and res["mutation_allowed"] is False and "note" in res
    out = await env.tools["undo_last"](0, ctx)
    assert out["error"]["code"] == APPROVAL_REQUIRED


async def test_add_update_undo_chart_flow(env: Env) -> None:
    ctx = env.ctx()
    await env.allow(ctx)
    out = await env.tools["add_chart"](0, "Revenue per region", "bar", BAR_OPTION, ctx)
    assert out["ok"], out
    assert out["dashboard_version"] == 1 and out["base_version"] == 0
    chart_id = out["item_id"]
    assert ctx.state[STATE_LAST_CHART_ID] == chart_id
    dashboard_id = ctx.state[STATE_DASHBOARD_ID]

    state = await env.tools["get_dashboard_state"](ctx)
    items = state["dashboard"]["items"]
    assert state["dashboard"]["version"] == 1
    assert [(i["id"], i["chart_type"]) for i in items] == [(chart_id, "bar")]

    out = await env.tools["update_chart"](1, chart_id, ctx, chart_type="line", title="Tren")
    assert out["ok"], out
    snap = await env.store.get(dashboard_id)
    item = snap.content.items[chart_id]
    assert snap.version == 2 and item.spec.chart_type == "line" and item.title == "Tren"

    out = await env.tools["undo_last"](2, ctx)
    assert out["ok"] and out["kind"] == "undo"
    snap = await env.store.get(dashboard_id)
    assert snap.version == 3 and snap.content.items[chart_id].spec.chart_type == "bar"


async def test_version_conflict_returns_latest_state_and_exhausts(env: Env) -> None:
    ctx = env.ctx()
    await env.allow(ctx)
    assert (await env.tools["add_chart"](0, "A", "bar", BAR_OPTION, ctx))["ok"]
    for expected_left in (2, 1):
        out = await env.tools["update_layout"](0, {"x": {"x": 0, "y": 0, "w": 6, "h": 4}}, ctx)
        assert out["error"]["code"] == "VERSION_CONFLICT"
        assert out["error"]["details"]["current_version"] == 1
        assert out["dashboard"]["version"] == 1
        assert out["retries_left"] == expected_left
    out = await env.tools["remove_chart"](0, "x", ctx)
    assert out["error"]["code"] == RETRY_EXHAUSTED and out["retries_left"] == 0
    assert out["error"]["details"]["last_error"]["code"] == "VERSION_CONFLICT"
    assert "dashboard" in out
    # Setelah habis, tool mutasi ditolak tanpa eksekusi.
    out = await env.tools["undo_last"](1, ctx)
    assert out["error"]["code"] == RETRY_EXHAUSTED
    assert (await env.store.get(ctx.state[STATE_DASHBOARD_ID])).version == 1


async def test_chart_spec_errors_count_retries(env: Env) -> None:
    ctx = env.ctx()
    await env.allow(ctx)
    bad = {**BAR_OPTION, "series": [{"type": "bar", "encode": {"x": "region", "y": "profit"}}]}
    lefts = []
    for _ in range(3):
        out = await env.tools["add_chart"](0, "Bad", "bar", bad, ctx)
        assert out["ok"] is False and out["error"]["code"] != RETRY_EXHAUSTED
        lefts.append(out["retries_left"])
    assert lefts == [3, 2, 1]
    out = await env.tools["add_chart"](0, "Bad", "bar", bad, ctx)
    assert out["error"]["code"] == RETRY_EXHAUSTED
    out = await env.tools["add_chart"](0, "Good", "bar", BAR_OPTION, ctx)
    assert out["error"]["code"] == RETRY_EXHAUSTED  # jatah habis untuk giliran ini


async def test_add_insight_verifies_numbers_and_relations(env: Env) -> None:
    ctx = env.ctx()
    await env.allow(ctx)
    out = await env.tools["add_insight"](
        0, "comparison", "Jawa teratas", "Jawa mencatat revenue 9.999", ctx
    )
    assert out["error"]["code"] == "INSIGHT_NUMBER_MISMATCH"
    assert out["error"]["details"]["unmatched"]

    out = await env.tools["add_insight"](
        0, "cross_dataset_correlation", "Korelasi", "Jawa mencatat revenue 1.500", ctx
    )
    assert out["error"]["code"] == "INVALID_INSIGHT_TYPE"

    out = await env.tools["add_insight"](
        0, "comparison", "Jawa teratas", "Jawa mencatat revenue 1.500, Bali 700", ctx
    )
    assert out["ok"], out
    snap = await env.store.get(ctx.state[STATE_DASHBOARD_ID])
    item = snap.content.items[out["item_id"]]
    assert item.query_id == env.query_id and item.sql.startswith("SELECT")
    assert item.evidence.row_count == 2 and item.dataset_ids == [env.dataset_id]
    assert item.dataset_versions == {env.dataset_id: 1}

    out = await env.tools["update_insight"](1, item.id, ctx, text="Bali hanya 700")
    assert out["ok"] and out["dashboard_version"] == 2


async def test_propose_changes_and_presenters(env: Env) -> None:
    ctx = env.ctx(message="data sudah saya upload")
    first = await env.tools["propose_changes"]("Buat draft", ["Tren", "Region"], ctx)
    assert first["ok"]
    (event,) = first[CHAT_EVENTS_KEY]
    assert event["event"] == "approval.request"
    assert event["data"] == {
        "proposal_id": first["proposal_id"],
        "summary": "Buat draft",
        "themes": ["Tren", "Region"],
    }
    second = await env.tools["propose_changes"]("Draft baru", [], ctx)
    assert (await env.repos.proposals.get(first["proposal_id"])).status == "expired"
    assert (await env.repos.proposals.get(second["proposal_id"])).status == "pending"

    out = await env.tools["present_profile_summary"]("sales", "2 baris, 2 kolom", ctx)
    assert out[CHAT_EVENTS_KEY] == [
        {"event": "profile.summary", "data": {"dataset_id": env.dataset_id, "summary": "2 baris, 2 kolom"}}
    ]
    out = await env.tools["present_relation_candidates"](ctx)
    assert out["ok"] and out["count"] == 0 and CHAT_EVENTS_KEY not in out
    out = await env.tools["present_profile_summary"]("nope", "x", ctx)
    assert out["error"]["code"] == "UNKNOWN_DATASET"
