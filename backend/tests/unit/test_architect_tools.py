"""Unit test tool Architect, Blueprint, KPI, knowledge, dan Verified_Query (task 31.8).

_Requirements: 34.1, 35.4, 37.3, 37.6, 37.9, 37.10, 37.11, 38.8_
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from studio.agents.tools.architect_tools import activate_blueprint, make_architect_tools, skip_remaining_slots
from studio.agents.tools.context import ToolServices
from studio.agents.tools.knowledge_tools import make_knowledge_tools
from studio.agents.turn_policy import BLUEPRINT_ACTIVE_KEY, retry_failures_key
from tests.unit.test_agent_tools import DataEnv, env  # noqa: F401 — fixture

KPI_SQL = "SELECT SUM(revenue) AS total, SUM(revenue) AS prev FROM sales"


def _services(env: DataEnv) -> ToolServices:
    return ToolServices(repos=env.repos, engine=env.engine, profiler=None, dashboard_store=env.store)  # type: ignore[arg-type]


def _blueprint(**extra: Any) -> dict[str, Any]:
    return {
        "brief": {"purpose": "Pantau penjualan", "sections": ["kpi_row", "breakdown"]},
        "slots": [
            {"slot_id": "kpi_rev", "section": "kpi_row", "purpose": "Total revenue", "visual": "kpi", "metrics": ["revenue"]},
            {"slot_id": "by_region", "section": "breakdown", "purpose": "Revenue per region", "visual": "bar",
             "metrics": ["revenue"], "dimension": "region", "cross_filter_column": "region"},
            {"slot_id": "share", "section": "composition", "purpose": "Porsi region", "visual": "pie", "metrics": ["revenue"]},
        ],
        **extra,
    }


async def _approved(env: DataEnv, selected: list[str] | None = None) -> tuple[SimpleNamespace, dict[str, Any]]:
    tools = make_architect_tools(_services(env))
    ctx = env.ctx()
    out = await tools["propose_dashboard_plan"]("Rancangan penjualan", _blueprint(), ctx)
    assert out["ok"], out
    (evt,) = out["chat_events"]
    assert evt["event"] == "approval.request" and evt["data"]["kind"] == "blueprint"
    proposal = await env.repos.proposals.get(out["proposal_id"])
    assert await env.repos.proposals.approve(proposal.id, session_id=env.session_id)
    state: dict[str, Any] = {}
    await activate_blueprint(_services(env), workspace_id=env.ws_id, proposal=proposal, selected_slot_ids=selected, state=state)
    run_ctx = env.ctx()
    run_ctx.state.update(state)
    run_ctx.state["temp:mutation_allowed"] = True
    return run_ctx, tools


async def test_unknown_topic_lists_available(env: DataEnv) -> None:
    tool = make_knowledge_tools(_services(env))["get_bi_knowledge"]
    out = await tool("astrologi", env.ctx())
    assert out["ok"] is False and out["error"]["code"] == "UNKNOWN_TOPIC"
    topics = {t["topic"] for t in out["error"]["details"]["available"]}
    assert {"principles", "kpi_catalog", "playbooks/finance"} <= topics
    ok = await tool("finance", env.ctx())
    assert ok["ok"] and ok["topic"] == "playbooks/finance" and "waterfall" in ok["content"].lower()


async def test_invalid_blueprint_emits_no_card(env: DataEnv) -> None:
    tools = make_architect_tools(_services(env))
    bad = _blueprint()
    bad["slots"][0]["metrics"] = ["ghost_metric"]
    out = await tools["propose_dashboard_plan"]("x", bad, env.ctx())
    assert out["ok"] is False and out["error"]["code"] == "BLUEPRINT_INVALID"
    assert "chat_events" not in out
    assert out["error"]["details"]["issues"][0]["slot_id"] == "kpi_rev"


async def test_build_loop_selected_slots_retry_reset_failure_continues(env: DataEnv) -> None:
    ctx, tools = await _approved(env, ["kpi_rev", "by_region"])
    bp_id = ctx.state[BLUEPRINT_ACTIVE_KEY]
    record = await env.repos.blueprints.get(bp_id)
    assert [s["slot_id"] for s in record.blueprint["slots"]] == ["kpi_rev", "by_region"]

    # Slot 1: KPI via add_kpi + slot_id → ditandai done otomatis, layout dari slot.
    ctx.state[retry_failures_key("chart_spec")] = 2
    first = await tools["next_blueprint_slot"](ctx)
    assert first["ok"] and first["slot"]["slot_id"] == "kpi_rev"
    assert ctx.state[retry_failures_key("chart_spec")] == 0  # retry direset per slot
    q = await env.tools["run_sql"](KPI_SQL, ctx)
    assert q["ok"], q
    kpi = await env.dash_tools["add_kpi"](
        0, "Revenue", "total", ctx, comparison_column="prev", format_style="currency", slot_id="kpi_rev"
    )
    assert kpi["ok"], kpi
    assert any(e["event"] == "blueprint.progress" and e["data"]["status"] == "done" for e in kpi["chat_events"])
    snap = await env.store.get(kpi["dashboard_id"])
    assert snap.content.layout[kpi["item_id"]].model_dump() == first["slot"]["layout"]
    vqs = await env.repos.semantic.list(env.ws_id, kind="verified_query")
    assert len(vqs) == 1 and vqs[0].status == "candidate" and vqs[0].body["question"] == "Total revenue"

    # Slot 2 gagal → loop lanjut.
    second = await tools["next_blueprint_slot"](ctx)
    assert second["slot"]["slot_id"] == "by_region"
    failed = await tools["mark_slot_done"]("by_region", ctx, error="kolom tidak cocok")
    assert failed["ok"] and failed["status"] == "failed"

    done = await tools["next_blueprint_slot"](ctx)
    assert done["ok"] and done["done"] is True
    assert [f["slot_id"] for f in done["failed_slots"]] == ["by_region"]
    final = await env.repos.blueprints.get(bp_id)
    assert final.status == "completed"
    assert final.slot_status["kpi_rev"]["status"] == "done"


async def test_stop_marks_remaining_skipped(env: DataEnv) -> None:
    ctx, tools = await _approved(env)
    await tools["next_blueprint_slot"](ctx)
    skipped = await skip_remaining_slots(_services(env), ctx.state[BLUEPRINT_ACTIVE_KEY])
    assert sorted(skipped) == ["by_region", "kpi_rev", "share"]
    record = await env.repos.blueprints.get(ctx.state[BLUEPRINT_ACTIVE_KEY])
    assert record.status == "stopped" and {s["status"] for s in record.slot_status.values()} == {"skipped"}


async def test_add_kpi_requires_approval(env: DataEnv) -> None:
    ctx = env.ctx()
    await env.tools["run_sql"](KPI_SQL, ctx)
    out = await env.dash_tools["add_kpi"](0, "Revenue", "total", ctx)
    assert out["ok"] is False and out["error"]["code"] == "APPROVAL_REQUIRED"


async def test_review_dashboard_emits_findings(env: DataEnv) -> None:
    ctx = env.ctx()
    await env.allow(ctx)
    await env.tools["run_sql"](KPI_SQL, ctx)
    out = await env.dash_tools["add_kpi"](0, "Revenue", "total", ctx)
    assert out["ok"], out
    review = await make_architect_tools(_services(env))["review_dashboard"](ctx)
    assert review["ok"]
    codes = {f["code"] for f in review["findings"]}
    assert "KPI_NO_COMPARISON" in codes
    assert review["chat_events"][0]["event"] == "review.findings"
