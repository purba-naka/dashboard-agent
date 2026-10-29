"""Eval orkestrasi multi-agent dengan model LLM nyata (marker ``live``, berbiaya).

    backend\\.venv\\Scripts\\python.exe -m pytest -m live tests/eval/test_orchestration_live.py -s

Berbeda dari ``test_eval.py`` (model skrip), di sini Root_Agent benar-benar memilih
sub-agent. Per kasus ``orchestration.evalset.json`` dihitung:

* ``routing`` — agent yang diharapkan aktif berurutan (subsequence ``agent.active``),
  agent terlarang tidak aktif;
* ``trajectory`` — ``tool_uses`` muncul IN_ORDER di tool yang dipanggil;
* ``approval_gate`` — patch Dashboard terjadi persis bila ``mutation`` true;
* ``grounding`` — angka/teks ground truth muncul di jawaban (teks semua agent).

Laporan JSON ditulis ke ``tests/eval/results/orchestration-latest.json``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings
from tests.integration.helpers import Studio
from tests.integration.test_chat_stream import chat_stream, running
from tests.live.test_ntp_live import TURN_TIMEOUT_S, _live_models, _mentions

EVALSET = Path(__file__).resolve().parent / "orchestration.evalset.json"
REPORT = Path(__file__).resolve().parent / "results" / "orchestration-latest.json"
TRANSFER = "transfer_to_agent"
ROOT = "Root_Agent"

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


def step_metrics(frames: list[Any]) -> dict[str, Any]:
    """Metrik langkah dari frame SSE satu giliran (bukan gate; latensi LLM nondeterministik)."""
    calls = [f for f in frames if f.event == "tool.call"]
    args_chars: dict[str, int] = {}
    for f in calls:
        args_chars[f.data["tool"]] = args_chars.get(f.data["tool"], 0) + len(f.data.get("args_summary") or "")
    by_tool: dict[str, int] = {}
    for f in calls:
        by_tool[f.data["tool"]] = by_tool.get(f.data["tool"], 0) + 1
    return {
        "transfer_count": by_tool.get(TRANSFER, 0),
        "classify_turn_count": by_tool.get("classify_turn", 0),
        "tool_calls": len(calls),
        "agent_switches": sum(1 for f in frames if f.event == "agent.active"),
        "tool_counts": by_tool,
        # args_summary dipotong runner; cukup untuk membandingkan tren, bukan ukuran mutlak.
        "args_summary_chars": args_chars,
    }

def in_order(expected: list[str], actual: list[str]) -> float:
    """Porsi ``expected`` yang muncul berurutan di ``actual`` (IN_ORDER).

    Entri ``"a|b"`` berarti salah satu dari keduanya (tool setara), mis.
    ``add_chart|add_chart_from_template``. Setiap entri mengonsumsi satu kemunculan.
    """
    if not expected:
        return 1.0
    it = iter(actual)
    hits = 0
    for name in expected:
        alts = set(name.split("|"))
        if any(item in alts for item in it):
            hits += 1
    return hits / len(expected)


async def test_step_metrics_selfcheck() -> None:
    from types import SimpleNamespace as NS

    def call(tool: str, summary: str = "") -> NS:
        return NS(event="tool.call", data={"tool": tool, "args_summary": summary})

    frames = [NS(event="agent.active", data={"agent": ROOT}), call(TRANSFER), call("classify_turn"),
              NS(event="agent.active", data={"agent": "Query_Agent"}), call("run_sql", "abc"), call("run_sql", "de")]
    m = step_metrics(frames)
    assert (m["transfer_count"], m["classify_turn_count"], m["tool_calls"], m["agent_switches"]) == (1, 1, 4, 2)
    assert m["args_summary_chars"]["run_sql"] == 5

async def test_in_order_selfcheck() -> None:
    assert in_order(["a", "c"], ["a", "b", "c"]) == 1.0
    assert in_order(["c", "a"], ["a", "b", "c"]) == 0.5
    assert in_order([], ["x"]) == 1.0
    # Alternatif setara: salah satu dihitung, tapi tidak melonggarkan urutan/hitungan.
    assert in_order(["a|b"], ["b"]) == 1.0
    assert in_order(["a|b"], ["z"]) == 0.0
    assert in_order(["run_sql", "a|b"], ["run_sql", "b"]) == 1.0
    assert in_order(["a|b", "run_sql"], ["b", "run_sql", "a"]) == 1.0
    assert in_order(["a|b", "c"], ["b"]) == 0.5
    assert in_order(["a"], ["a", "a"]) == 1.0


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[dict[str, Any]]:
    settings = Settings(data_dir=tmp_path_factory.mktemp("orch") / "data", query_runner_mode="inline",
                        sse_heartbeat_seconds=5, database_url=None)
    app = create_app(settings, extra_routers=[chat.router], lifespan_hooks=[configure_agents])
    app.state.models = _live_models()
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await studio.sales_workspace()
        for rel in await studio.relations(ids["ws"], status="candidate"):
            await studio.confirm(ids["ws"], rel["id"])
        await app.state.semantic_drafter.wait_idle()
        yield {"app": app, "studio": studio, "ws": ids["ws"]}


async def run_case(live: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    exp = case["x_expected"]
    inv = case["conversation"][0]
    dash = await live["studio"].create_dashboard(live["ws"], case["eval_id"])
    body = {"message": inv["user_content"]["parts"][0]["text"], "dashboard_id": dash["id"]}
    t0 = time.monotonic()
    async with chat_stream(live["app"], live["ws"], body) as (resp, reader):
        assert resp.status_code == 200
        frames = await reader.until(lambda f: f.event in ("run.done", "run.stopped"), timeout=TURN_TIMEOUT_S)

    agents = [f.data["agent"] for f in frames if f.event == "agent.active" and f.data["agent"] != ROOT]
    tools = [f.data["tool"] for f in frames if f.event == "tool.call" and f.data["tool"] != TRANSFER]
    # Chat UI menampilkan teks semua agent; grounding dinilai atas teks yang dilihat pengguna.
    text = "".join(f.data["text"] for f in frames if f.event == "text.delta")
    patched = any(f.event == "patch.applied" for f in frames)
    cards = [f for f in frames if f.event == "approval.request" and f.data.get("kind") == "blueprint"]
    tool_errors = [f.data["tool"] for f in frames if f.event == "tool.result" and not f.data.get("ok", True)]

    forbidden = set(exp.get("forbid_agents", [])) & set(agents)
    checks = {
        "routing": in_order(exp["agents"], agents) if not forbidden else 0.0,
        "trajectory": in_order([u["name"] for u in inv["intermediate_data"]["tool_uses"]], tools),
        "approval_gate": 1.0 if patched == exp["mutation"] and (not exp.get("blueprint_card") or cards) else 0.0,
    }
    needles = [n for n in exp.get("numbers", [])] + exp.get("texts", [])
    if needles:
        hits = [_mentions(text, n) if isinstance(n, (int, float)) else n.lower() in text.lower() for n in needles]
        checks["grounding"] = sum(hits) / len(hits)
    return {
        "eval_id": case["eval_id"],
        "passed": all(v >= 1.0 for v in checks.values()),  # error tool yang pulih = self_corrections
        "scores": checks,
        "seconds": round(time.monotonic() - t0, 1),
        "metrics": step_metrics(frames),
        "agents": agents,
        "tools": tools,
        "tool_errors": tool_errors,
        "self_corrections": len([f for f in frames if f.event == "error"]),
        "errors": [f.data for f in frames if f.event == "error"],
        "answer": text[:600],
        "trace": [f"{f.event}:{f.data.get('agent', '')}:{f.data.get('tool', '')}" for f in frames
                  if f.event not in ("text.delta", "thought.delta")],
    }


async def test_orchestration_evalset(live: dict[str, Any]) -> None:
    cases = json.loads(EVALSET.read_text(encoding="utf-8"))["eval_cases"]
    if only := os.environ.get("ORCH_CASES"):
        cases = [c for c in cases if c["eval_id"] in only.split(",")]
    results = []
    for case in cases:
        r = await run_case(live, case)
        results.append(r)
        print(f"\n[{'PASS' if r['passed'] else 'FAIL'}] {r['eval_id']} {r['scores']} {r['seconds']}s"
              f"\n  agents={r['agents']}\n  tools={r['tools']}\n  answer={ascii(r['answer'][:200])}")

    metrics = sorted({m for r in results for m in r["scores"]})
    summary = {m: round(sum(r["scores"][m] for r in results if m in r["scores"])
                        / sum(1 for r in results if m in r["scores"]), 3) for m in metrics}
    n = max(len(results), 1)
    summary["avg_seconds"] = round(sum(r["seconds"] for r in results) / n, 1)
    for key in ("transfer_count", "classify_turn_count", "tool_calls", "agent_switches"):
        summary[f"avg_{key}"] = round(sum(r["metrics"][key] for r in results) / n, 2)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps({"summary": summary, "passed": sum(r["passed"] for r in results),
                                  "total": len(results), "cases": results}, indent=2, ensure_ascii=False),
                      encoding="utf-8")
    print(f"\nsummary={summary} report={REPORT}")
    assert all(r["passed"] for r in results), [r["eval_id"] for r in results if not r["passed"]]
