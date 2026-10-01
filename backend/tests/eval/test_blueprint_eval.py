"""Evaluasi agent ekstensi v2 (task 34): model semantik + Dashboard_Architect_Agent.

Marker ``eval`` (``pytest -m eval``). ``blueprint.evalset.json`` dimuat sebagai
:class:`EvalSet`; tiap kasus berjalan di Workspace segar berisi
``finance_monthly.csv``, metrik ``revenue`` (sinonim "omzet") & ``net_profit``,
Workspace_Instruction "abaikan status cancelled", dan satu chart buatan pengguna.

Metrik:

* ``tool_trajectory`` dan ``response_match`` seperti ``test_eval.py``;
* kustom (Req 30.2, 37.2, 37.7, 21.4): setiap Blueprint usulan lolos
  ``validate_blueprint``; setiap item hasil build berada di layout slotnya;
  tidak ada tool mutasi tanpa approval; item ``user`` tidak berubah;
* per kasus (Req 33.6, 33.7, 35.6, 35.7): konteks semantik sampai ke prompt
  agent, SQL memakai ekspresi metrik/instruksi, angka cocok ground truth,
  susunan Blueprint mengikuti permintaan pengguna, KPI lewat ``add_kpi``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import PrivateAttr

from tests.eval.test_eval import ScriptedModel, chat_stream, running
from tests.integration.helpers import EXPECTED_FACTS, Studio, bar_spec

from google.adk.evaluation.eval_set import EvalSet  # noqa: E402  (importorskip di test_eval)

from studio.agents.tools.architect_tools import _vocabulary
from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings
from studio.core.blueprint import validate_blueprint
from studio.core.models import DashboardBlueprint, LayoutRect

EVALSET_PATH = Path(__file__).resolve().parent / "blueprint.evalset.json"
FACTS = EXPECTED_FACTS["finance_monthly"]
AGENTS = ("root", "profiler", "architect", "query", "chart", "insight")
MUTATION_TOOLS = {"add_chart", "update_chart", "remove_chart", "update_layout", "add_insight",
                  "update_insight", "add_kpi", "update_kpi", "update_brief", "undo_last"}
TRANSFER = "transfer_to_agent"

SEMANTIC_ENTRIES = [
    {"kind": "metric", "body": {
        "name": "revenue", "label": "Revenue", "expr": "SUM(revenue)", "base_table": "finance_monthly",
        "synonyms": ["omzet", "pendapatan"], "format": {"style": "currency"}, "time_column": "month"}},
    {"kind": "metric", "body": {
        "name": "net_profit", "label": "Net profit", "expr": "SUM(revenue - cogs - opex - interest_tax)",
        "base_table": "finance_monthly", "synonyms": ["laba bersih"], "format": {"style": "currency"},
        "time_column": "month"}},
    {"kind": "instruction", "body": {"text": "abaikan status cancelled"}},
]


class RecordingModel(ScriptedModel):
    """Model skrip yang juga merekam system instruction tiap permintaan."""

    _prompts: list[str] = PrivateAttr(default_factory=list)

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        config = getattr(llm_request, "config", None)
        self._prompts.append(str(getattr(config, "system_instruction", "") or ""))
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def _extra(case: Any, key: str, default: Any) -> Any:
    return (case.model_extra or {}).get(key, default)


@pytest.fixture(scope="module")
def evalset() -> EvalSet:
    return EvalSet.model_validate(json.loads(EVALSET_PATH.read_text(encoding="utf-8")))


async def run_case(tmp_path: Path, case: Any) -> dict[str, Any]:
    """Workspace segar → jalankan semua invocation kasus → kumpulkan hasil."""
    script: dict[str, list[dict[str, Any]]] = _extra(case, "x_script", {})
    holder: dict[str, str] = {}
    app: Any = None

    async def resolve(args: dict[str, Any]) -> dict[str, Any]:
        if args.get("base_version") == "$dashboard_version":
            args = {**args, "base_version": (await app.state.dashboard_store.get(holder["dash"])).version}
        return args

    models = {name: RecordingModel.of(script.get(name, []), resolve) for name in AGENTS}
    settings = Settings(data_dir=tmp_path / "data", query_runner_mode="inline", sse_heartbeat_seconds=0.2)
    app = create_app(settings, extra_routers=[chat.router], lifespan_hooks=[configure_agents])
    app.state.models = models

    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace("Keuangan")
        await studio.upload_sample(ws, "finance_monthly.csv")
        await app.state.semantic_drafter.wait_idle()
        for entry in SEMANTIC_ENTRIES:
            resp = await client.post(f"/api/workspaces/{ws}/semantic/entries", json=entry)
            assert resp.status_code == 201, resp.text
        dash = await studio.create_dashboard(ws)
        holder["dash"] = dash["id"]
        query = await studio.execute(ws, "SELECT month, SUM(revenue) AS revenue FROM finance_monthly GROUP BY month")
        user_item, _ = await studio.add_chart(dash["id"], 0, "Chart pengguna", bar_spec(query.query_id, "month", "revenue"))
        before = (await studio.get_dashboard(dash["id"]))["content"]

        turns: list[list[Any]] = []
        session_id: str | None = None
        for i, invocation in enumerate(case.conversation):
            body: dict[str, Any] = {"message": invocation.user_content.parts[0].text}
            if session_id:
                body["session_id"] = session_id
            if i > 0:
                (card,) = [f for f in turns[-1] if f.event == "approval.request"]
                body["approval"] = {"proposal_id": card.data["proposal_id"],
                                    "selected_slot_ids": _extra(case, "x_approve", None)}
            async with chat_stream(app, ws, body) as (resp, reader):
                assert resp.status_code == 200
                frames = await reader.until(lambda f: f.event in ("run.done", "run.stopped"), timeout=60)
            session_id = session_id or frames[0].data["session_id"]
            turns.append(frames)

        after = (await studio.get_dashboard(dash["id"]))["content"]
        blueprint = (await client.get(f"/api/dashboards/{dash['id']}/blueprint")).json()
        last_query = (await app.state.repos.queries.list_by_workspace(ws, limit=1))[0]
        vocabulary = await _vocabulary(SimpleNamespace(repos=app.state.repos), ws)

    return {
        "turns": turns,
        "before": before,
        "after": after,
        "user_item": user_item,
        "blueprint": blueprint,
        "last_query": last_query,
        "vocabulary": vocabulary,
        "prompts": {name: model._prompts for name, model in models.items()},
    }


def _tool_calls(frames: list[Any]) -> list[str]:
    return [f.data["tool"] for f in frames if f.event == "tool.call" and f.data["tool"] != TRANSFER]


def _root_text(frames: list[Any]) -> str:
    return " ".join(f.data["text"] for f in frames if f.event == "text.delta" and f.data["agent"] == "Root_Agent")


def _trajectory(expected: list[str], actual: list[str]) -> float:
    remaining, matched = list(actual), 0
    for name in expected:
        if name in remaining:
            remaining.remove(name)
            matched += 1
    return matched / len(expected) if expected else 1.0


def _blueprints(result: dict[str, Any]) -> list[DashboardBlueprint]:
    return [
        DashboardBlueprint.model_validate(f.data["blueprint"])
        for frames in result["turns"]
        for f in frames
        if f.event == "approval.request" and f.data.get("kind") == "blueprint"
    ]


def check_case(case: Any, result: dict[str, Any]) -> None:
    """Semua metrik untuk satu kasus; gagal dengan pesan berisi ``eval_id``."""
    cid = case.eval_id
    expected: dict[str, Any] = _extra(case, "x_expected", {})
    turns = result["turns"]
    for frames in turns:
        assert not [f.data for f in frames if f.event == "error"], cid

    # -- trajectory & jawaban ------------------------------------------------
    for invocation, frames in zip(case.conversation, turns):
        uses = invocation.intermediate_data.tool_uses if invocation.intermediate_data else []
        score = _trajectory([u.name for u in uses], _tool_calls(frames))
        assert score > 0.99, (cid, invocation.invocation_id, _tool_calls(frames))
    final_text = _root_text(turns[-1])
    for kw in expected.get("response_keywords", []):
        assert kw in final_text, (cid, kw, final_text)

    # -- metrik kustom: tidak ada mutasi tanpa approval (Req 21.4) ---------------
    for allowed, frames in zip(expected["mutation_allowed"], turns):
        patches = [f for f in frames if f.event == "patch.applied"]
        if allowed:
            assert patches, cid
        else:
            assert not patches, (cid, _tool_calls(frames))
            assert not [f for f in frames if f.event == "tool.result" and f.data["tool"] in MUTATION_TOOLS
                        and f.data["ok"]], cid

    # -- metrik kustom: item pengguna tidak berubah (Req 35.9) -------------------
    uid = result["user_item"]
    assert result["after"]["items"][uid] == result["before"]["items"][uid], cid
    assert result["after"]["layout"][uid] == result["before"]["layout"][uid], cid

    # -- metrik kustom: setiap Blueprint lolos validate_blueprint (Req 37.2) -----
    metrics, columns = result["vocabulary"]
    existing = {k: LayoutRect.model_validate(v) for k, v in result["before"]["layout"].items()}
    blueprints = _blueprints(result)
    for bp in blueprints:
        assert validate_blueprint(bp, existing, metrics, columns) == [], cid

    # -- metrik kustom: item hasil build tepat di layout slot (Req 37.7) ---------
    if "built_slots" in expected:
        record = result["blueprint"]
        assert record["status"] == "completed", cid
        done = {sid: s["item_id"] for sid, s in record["slot_status"].items() if s["status"] == "done"}
        assert set(done) == set(expected["built_slots"]), (cid, record["slot_status"])
        for slot in record["blueprint"]["slots"]:
            assert result["after"]["layout"][done[slot["slot_id"]]] == slot["layout"], (cid, slot["slot_id"])
        for sid in expected.get("kpi_slots", []):
            assert result["after"]["items"][done[sid]]["kind"] == "kpi", cid

    # -- harapan per kasus --------------------------------------------------------
    sql = result["last_query"].sql
    if "metric_expr_in_sql" in expected:
        assert "SUM(revenue)" in sql and expected["metric_expr_in_sql"] in sql, (cid, sql)
    for needle in expected.get("sql_contains", []):
        assert needle in sql, (cid, sql)
    if "last_query_value" in expected:
        assert result["last_query"].rows[0][0] == FACTS[expected["last_query_value"]], (cid, result["last_query"].rows)
    for agent, needles in expected.get("prompt_contains", {}).items():
        prompt = " ".join(result["prompts"][agent])
        for needle in needles:
            assert needle in prompt, (cid, agent, needle)
    if "blueprint_sections" in expected:
        assert blueprints, cid
        assert set(expected["blueprint_sections"]) <= {s.section for s in blueprints[-1].slots}, cid
    if "slot_metrics" in expected or "slot_above" in expected:
        slots = {s.slot_id: s for s in blueprints[-1].slots}
        for sid, wanted in expected.get("slot_metrics", {}).items():
            assert slots[sid].metrics == wanted, (cid, sid)
        for top, below in expected.get("slot_above", []):
            assert slots[top].layout.y + slots[top].layout.h <= slots[below].layout.y, (cid, top, below)
        for sid in expected.get("slot_left", []):
            assert slots[sid].layout.x == 0, (cid, sid)
    if "new_item_kinds" in expected:
        new = [item["kind"] for iid, item in result["after"]["items"].items() if iid not in result["before"]["items"]]
        assert new == expected["new_item_kinds"], (cid, new)
        assert "add_insight" not in _tool_calls(turns[-1]), cid


pytestmark = [pytest.mark.eval, pytest.mark.asyncio]


async def test_evalset_schema_valid(evalset: EvalSet) -> None:
    assert evalset.eval_set_id == "blueprint-evalset"
    ids = [c.eval_id for c in evalset.eval_cases]
    assert len(ids) == 6 and len(set(ids)) == len(ids)


@pytest.mark.parametrize(
    "eval_id",
    [c["eval_id"] for c in json.loads(EVALSET_PATH.read_text(encoding="utf-8"))["eval_cases"]],
)
async def test_blueprint_eval_case(eval_id: str, evalset: EvalSet, tmp_path: Path) -> None:
    case = next(c for c in evalset.eval_cases if c.eval_id == eval_id)
    check_case(case, await run_case(tmp_path, case))
