"""Tool agent ``apply_canvas_patch`` + kerangka Blueprint canvas (irisan 4).

SQLite + DashboardStore + sidecar bun asli; ``ToolContext`` duck-typed seperti
``test_dashboard_turn_tools``. Dilewati bila bun tidak ada.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from studio.agents.context import openui_syntax_prompt, prompt_names
from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    STATE_DASHBOARD_ID,
    STATE_LAST_QUERY_ID,
    STATE_WORKSPACE_ID,
    ToolServices,
)
from studio.agents.tools.dashboard_tools import make_dashboard_tools
from studio.agents.tools.turn_tools import make_turn_tools
from studio.agents.turn_policy import begin_turn
from studio.core.models import ColumnInfo
from studio.core.openui.sidecar import OpenUISidecar
from studio.core.openui.skeleton import blueprint_skeleton
from studio.store.dashboard_store import DashboardStore
from studio.store.db import Database
from studio.store.repos import Repositories

pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="bun tidak terpasang")

MESSAGE = "Tolong tambahkan chart revenue per region ke dashboard"


@pytest.fixture(scope="module")
def sidecar() -> Iterator[OpenUISidecar]:
    sc = OpenUISidecar(timeout_s=30)
    yield sc
    sc.close()


@pytest.fixture
async def env(tmp_path: Path, sidecar: OpenUISidecar) -> AsyncIterator[SimpleNamespace]:
    db = await Database(tmp_path / "studio.db").open()
    try:
        repos = Repositories(db)
        store = DashboardStore(repos, None, openui=sidecar)
        ws = await repos.workspaces.create("WS")
        query = await repos.queries.create(
            ws.id,
            sql="SELECT region, SUM(revenue) AS revenue FROM sales GROUP BY region",
            output_schema=[ColumnInfo(name="region", type="string"), ColumnInfo(name="revenue", type="integer")],
            rows=[["Jawa", 1500], ["Bali", 700]],
            tables_used=["sales"],
        )
        services = ToolServices(
            repos=repos, engine=None, profiler=None, dashboard_store=store,  # type: ignore[arg-type]
            canvas_engine="openui", openui=sidecar,
        )
        tools = {**make_dashboard_tools(services), **make_turn_tools(services)}
        yield SimpleNamespace(repos=repos, store=store, ws_id=ws.id, query_id=query.id, tools=tools, services=services)
    finally:
        await db.close()


def _ctx(env: SimpleNamespace) -> SimpleNamespace:
    state: dict[str, Any] = {STATE_WORKSPACE_ID: env.ws_id, STATE_LAST_QUERY_ID: env.query_id}
    begin_turn(state, user_message=MESSAGE, invocation_id="inv-1")
    return SimpleNamespace(
        state=state,
        invocation_id="inv-1",
        user_content=SimpleNamespace(parts=[SimpleNamespace(text=MESSAGE)]),
        session=SimpleNamespace(id="s1"),
    )


async def _allow(env: SimpleNamespace, ctx: SimpleNamespace) -> None:
    out = await env.tools["classify_turn"]("dashboard_modification", True, ctx, evidence="tambahkan chart revenue")
    assert out["mutation_allowed"] is True


def _program(query_id: str) -> str:
    return "\n".join(
        [
            "root = VContainer([row])",
            f'q_region = Query("render_query", {{query: "{query_id}"}}, {{rows: []}})',
            'region_mix = BarChart("Revenue per region", q_region, "region", ["revenue"], {crossFilter: "region"})',
            "row = HContainer([region_mix])",
        ]
    )


async def test_canvas_tool_flow_and_allowlist(env: SimpleNamespace) -> None:
    ctx = _ctx(env)
    await _allow(env, ctx)
    out = await env.tools["apply_canvas_patch:data"](0, _program(env.query_id), ctx)
    assert out["ok"], out
    assert out["dashboard_version"] == 1 and "region_mix" in out["statements_changed"]
    assert out[CHAT_EVENTS_KEY][0]["event"] == "patch.applied"

    state = await env.tools["get_dashboard_state"](ctx)
    canvas = state["dashboard"]["canvas"]
    assert canvas["program"].startswith("root = VContainer")
    assert [t["name"] for t in canvas["tiles"]] == ["region_mix"]

    # Peran insight tidak boleh menulis chart; error dihitung pada jatah chart_spec.
    bad = 'region_mix = PieChart("X", q_region, "region", "revenue")'
    out = await env.tools["apply_canvas_patch:insight"](1, bad, ctx)
    assert out["ok"] is False and out["error"]["code"] == "CANVAS_PATCH_INVALID"
    assert out["error"]["details"]["errors"][0]["code"] == "FORBIDDEN_COMPONENT"
    assert out["retries_left"] == 3

    out = await env.tools["apply_canvas_patch:data"](0, bad, ctx)
    assert out["error"]["code"] == "VERSION_CONFLICT" and out["dashboard"]["version"] == 1


async def test_blueprint_skeleton_then_slot_definition(env: SimpleNamespace) -> None:
    slots = [
        {"slot_id": "kpi_rev", "section": "kpi_row", "visual": "kpi"},
        {"slot_id": "trend", "section": "trend", "visual": "line"},
        {"slot_id": "mix", "section": "breakdown", "visual": "bar"},
        {"slot_id": "rows", "section": "detail", "visual": "bar"},
    ]
    skeleton = blueprint_skeleton(slots)
    assert skeleton.splitlines()[0] == "root = VContainer([slot_kpi_rev, bp0_row1, slot_rows])"
    assert "bp0_row1 = HContainer([slot_trend, slot_mix], [8, 4])" in skeleton

    dash = await env.store.create_dashboard(env.ws_id, "D")
    event, warnings = await env.store.apply_canvas(dash.id, skeleton, 0, "agent")
    assert {w.code for w in warnings} == {"UNRESOLVED_REF"}

    ctx = _ctx(env)
    ctx.state[STATE_DASHBOARD_ID] = dash.id
    ctx.state["blueprint:current_slot"] = {"slot_id": "mix", "visual": "bar"}
    await _allow(env, ctx)
    patch = (
        f'q_mix = Query("render_query", {{query: "{env.query_id}"}}, {{rows: []}})\n'
        'slot_mix = BarChart("Per wilayah", q_mix, "region", ["revenue"])'
    )
    out = await env.tools["apply_canvas_patch:slot"](event.version, patch, ctx)
    assert out["ok"], out
    assert out["slot_done"] == "mix"


async def test_openui_prompts_include_sidecar_reference(env: SimpleNamespace) -> None:
    assert prompt_names("chart", "openui") == ("chart_openui", "canvas_openui")
    assert prompt_names("chart", "legacy") == ("chart", "chart_rules")
    text = await openui_syntax_prompt(env.services)
    assert "BarChart(title: string, data: any" in text and "Never use Mutation" in text
