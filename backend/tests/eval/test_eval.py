"""Evaluasi agent (ADK eval) dengan model mock (task 22.2).

Tidak berjalan default — marker ``eval`` (``-m eval``). Evalset
``studio.evalset.json`` dimuat sebagai :class:`google.adk.evaluation.EvalSet`
(validasi skema AgentEvaluator); tiap kasus dijalankan lewat harness chat
stream dengan model mock yang mengikuti ``x_script`` kasus, lalu dihitung:

* ``tool_trajectory_avg_score`` — rata-rata kecocokan urutan tool yang
  dipanggil terhadap ``intermediate_data.tool_uses``;
* ``response_match_score`` — rata-rata kata kunci jawaban yang muncul di teks
  final agent;
* metrik kustom: setiap SQL di evalset lolos SQL_Validator, Insight_Card lolos
  verifier angka (tool nyata yang menolak ketidakcocokan), dan tidak ada tool
  mutasi yang mengubah Dashboard tanpa persetujuan (Req 8.2, 12.6, 30.2, 30.4).
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

pytest.importorskip("google.adk.evaluation", reason="ADK eval tidak tersedia")
from google.adk.evaluation.eval_set import EvalSet  # noqa: E402

from studio.agents.tools.context import STATE_DASHBOARD_ID  # noqa: E402
from studio.agents.wiring import configure_agents  # noqa: E402
from studio.api import chat  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import Settings  # noqa: E402
from studio.core.sql_rules import ConfirmedRelation, analyze_sql  # noqa: E402

from tests.integration.helpers import (
    SAMPLES,
    SseReader,
    StreamingASGITransport,
    Studio,
    bar_spec,
)

EVALSET_PATH = Path(__file__).resolve().parent / "studio.evalset.json"
MUTATION_TOOLS = {"add_chart", "update_chart", "remove_chart", "update_layout",
                  "add_insight", "update_insight", "undo_last"}
TRANSFER = "transfer_to_agent"
TRAJECTORY_THRESHOLD = 0.9
RESPONSE_THRESHOLD = 0.9


# ---------------------------------------------------------------------------
# Model mock berskrip
# ---------------------------------------------------------------------------


def _call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))],
        )
    )


def _text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


class ScriptedModel(BaseLlm):
    """Model yang memainkan aksi skrip (tool call / transfer / teks)."""

    model: str = "scripted-model"

    _actions: list[dict[str, Any]] = PrivateAttr(default_factory=list)
    _resolver: Any = PrivateAttr(default=None)

    @classmethod
    def of(cls, actions: list[dict[str, Any]], resolver: Any = None) -> ScriptedModel:
        instance = cls()
        instance._actions = list(actions)
        instance._resolver = resolver
        return instance

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if not self._actions:
            yield _text("Selesai.")
            return
        action = self._actions.pop(0)
        if "transfer" in action:
            yield _call(TRANSFER, {"agent_name": action["transfer"]})
        elif "text" in action:
            yield _text(action["text"])
        else:
            args = dict(action.get("args", {}))
            if self._resolver is not None:
                args = await self._resolver(args)
            yield _call(action["tool"], args)


def scripted_models(script: dict[str, Any], resolver: Any = None) -> dict[str, BaseLlm]:
    """Satu model skrip per agent; urutan root = root + root_after_transfer + root_final."""
    root_actions = (
        script.get("root", [])
        + script.get("root_after_transfer", [])
        + script.get("root_final", [])
    )
    return {
        "root": ScriptedModel.of(root_actions, resolver),
        "profiler": ScriptedModel.of(script.get("profiler", []), resolver),
        "query": ScriptedModel.of(script.get("query", []), resolver),
        "chart": ScriptedModel.of(script.get("chart", []), resolver),
        "insight": ScriptedModel.of(script.get("insight", []), resolver),
    }


def make_eval_app(tmp_path: Path, script: dict[str, Any], resolver: Any = None) -> FastAPI:
    settings = Settings(
        data_dir=tmp_path / "data",
        query_runner_mode="inline",
        sse_heartbeat_seconds=0.2,
    )
    app = create_app(
        settings,
        extra_routers=[chat.router],
        lifespan_hooks=[configure_agents],
    )
    app.state.models = scripted_models(script, resolver)
    return app


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@asynccontextmanager
async def chat_stream(
    app: FastAPI, ws: str, body: dict[str, Any]
) -> AsyncIterator[tuple[httpx.Response, SseReader]]:
    transport = StreamingASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", f"/api/workspaces/{ws}/chat", json=body
        ) as resp:
            yield resp, SseReader(resp)


# ---------------------------------------------------------------------------
# Fixture: satu app + data untuk seluruh evalset
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def evalset() -> EvalSet:
    return EvalSet.model_validate(json.loads(EVALSET_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def tmp_path_eval(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
async def eval_setup(tmp_path_eval: Path) -> AsyncIterator[dict[str, Any]]:
    """App + data lengkap (customers/transactions/products, relasi confirmed) +
    Dashboard berisi satu chart awal (kasus "ganti-tipe-chart" mengubahnya).

    Function-scope: tiap test membangun ulang data agar urutan versi Dashboard
    deterministik dan tidak berbagi event loop antar test.
    """
    app = make_eval_app(tmp_path_eval, {})
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace()
        await studio.upload_sample(ws, "customers.csv")
        await studio.upload_sample(ws, "transactions.csv")
        await studio.upload_products(ws)
        # Konfirmasi semua relasi terdeteksi (JOIN lintas dataset diizinkan).
        for rel in await studio.relations(ws, status="candidate"):
            await studio.confirm(ws, rel["id"])
        dash = await studio.create_dashboard(ws)
        # Chart awal (v1) untuk kasus ganti tipe.
        query = await studio.execute(
            ws,
            "SELECT STRFTIME(transaction_date, '%Y-%m') AS month, COUNT(*) AS transactions "
            "FROM transactions GROUP BY STRFTIME(transaction_date, '%Y-%m')",
        )
        await studio.add_chart(
            dash["id"], 0, "Transaksi per bulan", bar_spec(query.query_id, "month", "transactions")
        )

        def app_factory(script: dict[str, Any]) -> FastAPI:
            """App kasus dengan resolver placeholder skrip (dipasang lazily)."""
            holder: dict[str, Any] = {"dash": dash["id"]}

            async def resolve(args: dict[str, Any]) -> dict[str, Any]:
                out = dict(args)
                needs = any(
                    v in ("$dashboard_version", "$last_chart_id") for v in out.values()
                )
                if not needs:
                    return out
                snapshot = await app.state.dashboard_store.get(holder["dash"])
                if out.get("base_version") == "$dashboard_version":
                    out["base_version"] = snapshot.version
                if out.get("chart_id") == "$last_chart_id":
                    charts = [
                        item_id
                        for item_id, item in snapshot.content.items.items()
                        if getattr(item, "kind", "") == "chart"
                    ]
                    out["chart_id"] = charts[-1] if charts else ""
                return out

            return make_eval_app(tmp_path_eval, script, resolve)

        yield {
            "ws": ws,
            "dash": dash["id"],
            "app_factory": app_factory,
            "repos": app.state.repos,
        }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


async def run_case(
    setup: dict[str, Any], case: Any
) -> dict[str, Any]:
    """Jalankan satu kasus eval; kembalikan event stream + hasil pengukuran."""
    script: dict[str, Any] = case.model_extra.get("x_script", {}) if case.model_extra else {}
    expected: dict[str, Any] = case.model_extra.get("x_expected", {}) if case.model_extra else {}
    invocation = case.conversation[0]
    user_text = invocation.user_content.parts[0].text
    uses = invocation.intermediate_data.tool_uses if invocation.intermediate_data else []
    expected_tools = [u.name for u in uses]

    app = setup["app_factory"](script)
    async with running(app) as client:
        async with chat_stream(app, setup["ws"], {"message": user_text}) as (_, reader):
            started = await reader.until(lambda f: f.event == "run.started")
            frames = started + await reader.until(lambda f: f.event in ("run.done", "run.stopped"))

    # Skrip yang memicu error tool (mis. intent tak dikenal) = kasus eval tidak sah.
    errors = [f.data for f in frames if f.event == "error"]
    assert not errors, (case.eval_id, errors)

    tool_calls = [f.data["tool"] for f in frames if f.event == "tool.call" and f.data["tool"] != TRANSFER]
    tool_results = {
        f.data["tool"]: f for f in frames if f.event == "tool.result"
    }
    final_text = " ".join(
        f.data["text"] for f in frames if f.event == "text.delta" and f.data["agent"] == "Root_Agent"
    )

    # Skor trajectory: expected (multiset) ⊆ actual (urutan tool tetap dihormati
    # lewat penghapusan berurutan).
    remaining = list(tool_calls)
    matched = 0
    for name in expected_tools:
        if name in remaining:
            remaining.remove(name)
            matched += 1
    trajectory_score = matched / len(expected_tools) if expected_tools else 1.0

    keywords = expected.get("response_keywords", [])
    hits = sum(1 for kw in keywords if kw in final_text)
    response_score = hits / len(keywords) if keywords else 1.0

    return {
        "frames": frames,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "final_text": final_text,
        "trajectory_score": trajectory_score,
        "response_score": response_score,
        "expected": expected,
    }


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


pytestmark = [pytest.mark.eval, pytest.mark.asyncio]


async def test_evalset_schema_valid(evalset: EvalSet) -> None:
    assert evalset.eval_set_id == "studio-evalset"
    assert len(evalset.eval_cases) == 8
    ids = [c.eval_id for c in evalset.eval_cases]
    assert len(set(ids)) == len(ids)


async def test_tool_trajectory_avg_score(evalset: EvalSet, eval_setup: dict[str, Any]) -> None:
    scores: dict[str, float] = {}
    for case in evalset.eval_cases:
        result = await run_case(eval_setup, case)
        scores[case.eval_id] = result["trajectory_score"]
        assert result["trajectory_score"] > 0.99, (case.eval_id, result["tool_calls"])
    avg = sum(scores.values()) / len(scores)
    assert avg >= TRAJECTORY_THRESHOLD
    print(f"tool_trajectory_avg_score = {avg:.3f}")


async def test_response_match_score(evalset: EvalSet, eval_setup: dict[str, Any]) -> None:
    scores: dict[str, float] = {}
    for case in evalset.eval_cases:
        result = await run_case(eval_setup, case)
        scores[case.eval_id] = result["response_score"]
        assert result["response_score"] > 0.99, (case.eval_id, result["final_text"])
    avg = sum(scores.values()) / len(scores)
    assert avg >= RESPONSE_THRESHOLD
    print(f"response_match_score = {avg:.3f}")


async def test_setiap_sql_evalset_lolos_sql_validator(evalset: EvalSet, eval_setup: dict[str, Any]) -> None:
    """Metrik kustom: semua SQL di trajectory lolos SQL_Validator (Req 10.x, 30.2)."""
    repos = eval_setup["repos"]
    ws = eval_setup["ws"]
    datasets = await repos.datasets.list_by_workspace(ws)
    tables = {d.table_name: [c.name for c in d.schema] for d in datasets}
    confirmed = [
        ConfirmedRelation(
            id=r.id,
            table_a=r.from_table,
            column_a=r.from_column,
            table_b=r.to_table,
            column_b=r.to_column,
        )
        for r in await repos.relations.list_confirmed(ws)
    ]

    sqls = []
    for case in evalset.eval_cases:
        invocation = case.conversation[0]
        if invocation.intermediate_data:
            for use in invocation.intermediate_data.tool_uses:
                if use.name == "run_sql":
                    sqls.append(use.args["sql"])
    assert len(sqls) >= 3
    for sql in sqls:
        analysis = analyze_sql(sql, tables, confirmed)  # raise StudioError bila gagal
        assert analysis.tables_used


async def test_insight_lolos_verifier_angka_dan_tanpa_mutasi_tanpa_approval(
    evalset: EvalSet, eval_setup: dict[str, Any]
) -> None:
    """Metrik kustom: Insight lolos verifier angka (tool nyata) & tidak ada tool
    mutasi yang mengubah Dashboard tanpa persetujuan (Req 14.4, 21.4, 30.2)."""
    mutation_cases = {"chart-per-bulan", "ganti-tipe-chart", "insight-kontributor-teratas",
                      "insight-lintas-dataset"}
    no_mutation_cases = {"tanpa-persetujuan"}

    for case in evalset.eval_cases:
        result = await run_case(eval_setup, case)
        tool_calls = result["tool_calls"]

        if case.eval_id in no_mutation_cases:
            # Tidak ada tool mutasi yang dipanggil sama sekali (Req 21.4).
            assert not (set(tool_calls) & MUTATION_TOOLS), (case.eval_id, tool_calls)
            # Tidak ada patch yang diterapkan.
            assert not [f for f in result["frames"] if f.event == "patch.applied"]
            # Jawaban menyebut perlunya persetujuan.
            assert "persetujuan" in result["final_text"].lower()

        if case.eval_id in mutation_cases:
            # Tool mutasi berhasil (patch terjadi) → approval gate terpenuhi
            # lewat classify_turn(explicit_change_request=True).
            patches = [f for f in result["frames"] if f.event == "patch.applied"]
            assert patches, (case.eval_id, tool_calls)

        if case.eval_id.startswith("insight-"):
            # add_insight lolos verifier angka (ok=True; tool menolak mismatch).
            res = result["tool_results"].get("add_insight")
            assert res is not None and res.data["ok"] is True, (case.eval_id, res)


async def test_kasus_agregasi_menyebut_angka_ground_truth(
    evalset: EvalSet, eval_setup: dict[str, Any]
) -> None:
    """Jawaban agregasi memuat angka ground truth (1.802.384.000 / 2.000)."""
    case = next(c for c in evalset.eval_cases if c.eval_id == "pertanyaan-agregasi")
    result = await run_case(eval_setup, case)
    assert "1.802.384.000" in result["final_text"]
    assert "2.000" in result["final_text"]
