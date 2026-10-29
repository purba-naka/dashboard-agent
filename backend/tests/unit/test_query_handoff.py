"""Regresi deterministik tanpa LLM: kendali Query_Agent kembali ke pemanggil.

Bug (eval ``route-chart-eksplisit``): Chart_Designer meminta SQL ke Query_Agent,
lalu Query_Agent mengakhiri gilirannya — baik dengan transfer ke agent yang
SALAH (mis. Dashboard_Architect_Agent) maupun tanpa transfer sama sekali — dan
giliran berakhir tanpa chart. Sebabnya: pengembalian ke pemanggil adalah
KEPUTUSAN LLM (``transfer_to_agent``), padahal LLM tidak tahu siapa pemanggilnya.

Perbaikan yang diuji di sini: Query_Agent dipanggil Chart_Designer_Agent sebagai
*tool* (agent-as-tool, ``mode="single_turn"``), sehingga kendali kembali ke
pemanggil secara struktural — apa pun yang dilakukan Query_Agent di akhir
gilirannya.

Model diganti model skrip (tanpa LLM); sisa alur ADK (transfer, eksekusi tool
sungguhan, query Polars, patch Dashboard, gerbang persetujuan) berjalan seperti
produksi.
"""

from __future__ import annotations

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

from studio.agents.definitions import CHART_NAME, INSIGHT_NAME, QUERY_NAME, ROOT_NAME
from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings
from tests.integration.helpers import SseReader, StreamingASGITransport, Studio
from tests.unit.agent_harness import call as h_call
from tests.unit.agent_harness import run_turn, text as h_text

SALES_CSV = b"region,revenue\nJawa,1500\nBali,700\n"
TRANSFER = "transfer_to_agent"
TIMEOUT_S = 60

BAR_OPTION = {
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
}


def call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        )
    )


def text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


class FakeModel(BaseLlm):
    """Model skrip: tiap panggilan mengonsumsi satu aksi (function call / teks)."""

    model: str = "fake-model"

    _actions: list[Any] = PrivateAttr(default_factory=list)

    @classmethod
    def of(cls, *actions: Any) -> FakeModel:
        instance = cls()
        instance._actions = list(actions)
        return instance

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if not self._actions:
            yield text("Selesai.")
            return
        yield self._actions.pop(0)


def fake_models(*, root: list[Any], query: list[Any], chart: list[Any]) -> dict[str, BaseLlm]:
    default = FakeModel.of()
    return {
        "root": FakeModel.of(*root),
        "profiler": default,
        "query": FakeModel.of(*query),
        "chart": FakeModel.of(*chart),
        "insight": default,
    }


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
        async with client.stream("POST", f"/api/workspaces/{ws}/chat", json=body) as resp:
            yield resp, SseReader(resp)


async def _frames_for_chart_turn(tmp_path: Path) -> list[Any]:
    """Jalankan satu giliran chart: Root → Chart → tool Query_Agent → add_chart."""
    root_script = [
        call(
            "classify_turn",
            {
                "intent": "dashboard_modification",
                "explicit_change_request": True,
                "evidence": "tambahkan bar chart",
            },
        ),
        call(TRANSFER, {"agent_name": CHART_NAME}),
        text("Chart revenue per region sudah ditambahkan."),
    ]
    # Query_Agent menjalankan SQL lalu MENGAKHIRI gilirannya tanpa transfer apa
    # pun — persis mode gagal yang teramati di eval live (``run.done`` tepat
    # setelah ``run_sql``).
    query_script = [
        call("run_sql", {"sql": "SELECT region, revenue FROM sales"}),
        text("Query siap."),
    ]
    chart_script = [
        call(QUERY_NAME, {"request": "total revenue per region"}),
        call("get_query_schema", {}),
        call(
            "add_chart",
            {
                "base_version": 0,
                "title": "Revenue per Region",
                "chart_type": "bar",
                "option": BAR_OPTION,
            },
        ),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
    ]
    settings = Settings(
        data_dir=tmp_path / "data", query_runner_mode="inline", sse_heartbeat_seconds=0.2
    )
    app = create_app(settings, extra_routers=[chat.router], lifespan_hooks=[configure_agents])
    app.state.models = fake_models(root=root_script, query=query_script, chart=chart_script)
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace()
        await studio.upload_csv(ws, "sales.csv", SALES_CSV)
        dash = await studio.create_dashboard(ws)
        body = {"message": "Tambahkan bar chart total revenue per region.", "dashboard_id": dash["id"]}
        async with chat_stream(app, ws, body) as (resp, reader):
            assert resp.status_code == 200
            return await reader.until(
                lambda f: f.event in ("run.done", "run.stopped"), timeout=TIMEOUT_S
            )


def _agents(frames: list[Any]) -> list[str]:
    return [
        f.data["agent"]
        for f in frames
        if f.event == "agent.active" and f.data["agent"] != ROOT_NAME
    ]


def _tools(frames: list[Any]) -> list[str]:
    return [f.data["tool"] for f in frames if f.event == "tool.call"]


async def test_kendali_kembali_ke_chart_setelah_query_selesai(tmp_path: Path) -> None:
    """Query_Agent berhenti tanpa transfer → Chart_Designer_Agent tetap membuat chart."""
    frames = await _frames_for_chart_turn(tmp_path)
    agents = _agents(frames)
    tools = _tools(frames)

    assert QUERY_NAME in agents, f"Query_Agent tidak pernah aktif: agents={agents}"
    # Kendali kembali ke pemanggil (Chart) SETELAH Query, lalu chart dibuat.
    assert agents.count(CHART_NAME) >= 2, f"Chart tidak dilanjutkan setelah Query: agents={agents}"
    assert "run_sql" in tools, f"SQL tidak berjalan: tools={tools}"
    assert "add_chart" in tools, f"chart tidak dibuat: tools={tools}"
    assert any(f.event == "patch.applied" for f in frames), "tidak ada patch Dashboard"
    # Pemanggil tidak boleh terseret ke agent lain di tengah giliran chart.
    assert "Dashboard_Architect_Agent" not in agents, f"agent salah aktif: agents={agents}"
    assert "propose_changes" not in tools, f"kartu usulan tak perlu muncul: tools={tools}"


async def test_query_sebagai_tool_tidak_punya_jalur_transfer(tmp_path: Path) -> None:
    """Saat menjadi tool, Query_Agent tidak punya ``transfer_to_agent`` sama sekali.

    Itu yang membuat pengembalian ke pemanggil deterministik: tidak ada target
    agent yang bisa salah dipilih LLM. Root_Agent tetap punya Query_Agent versi
    ``chat`` sebagai target transfer untuk pertanyaan data murni.
    """
    from google.adk.flows.llm_flows.extensions._agent_transfer import _get_transfer_targets

    app = create_app(
        Settings(data_dir=tmp_path / "data", query_runner_mode="inline"),
        extra_routers=[chat.router],
        lifespan_hooks=[configure_agents],
    )
    app.state.models = fake_models(root=[], query=[], chart=[])
    async with running(app):
        root = app.state.chat_runner._runner.agent
        queries = [a for a in _walk(root) if a.name == QUERY_NAME]
        inline = [a for a in queries if getattr(a, "mode", None) == "single_turn"]

        # Satu instance inline per agent pemakai (Chart dan Insight): masing-masing
        # memanggil Query sebagai TOOL agar kendali kembali otomatis.
        assert len(inline) == 2, f"butuh Query inline untuk Chart dan Insight: {[a.mode for a in queries]}"
        parents = sorted(a.parent_agent.name for a in inline if a.parent_agent is not None)
        assert parents == sorted([CHART_NAME, INSIGHT_NAME]), parents
        # Agent single_turn tidak mendapat tool transfer sama sekali (ADK).
        assert all(_get_transfer_targets(a) == [] for a in inline)
        assert _get_transfer_targets(inline[0]) == []

        chart = next(a for a in _walk(root) if a.name == CHART_NAME)
        assert _get_transfer_targets(chart) != [], "Chart harus tetap bisa kembali ke Root_Agent"
        # Pertanyaan data murni: Root_Agent tetap bisa mentransfer ke Query_Agent.
        assert QUERY_NAME in [t.name for t in _get_transfer_targets(root)]


def _walk(agent: Any) -> list[Any]:
    out = [agent]
    for sub in getattr(agent, "sub_agents", []) or []:
        out.extend(_walk(sub))
    return out


async def test_tanpa_transfer_query_tetap_kembali_ke_chart() -> None:
    """Jalur ADK nyata (tanpa DB): Query_Agent berhenti → Chart_Designer_Agent lanjut.

    Model Query_Agent hanya berkata "Query siap." (tanpa ``transfer_to_agent``).
    Karena Chart memanggilnya sebagai TOOL, giliran tetap kembali ke Chart —
    tidak ada keputusan agent yang bisa salah.
    """
    turn = await run_turn(
        "Tambahkan bar chart total revenue per region.",
        state_delta={"workspace_id": "ws-1"},
        root=[h_call(TRANSFER, {"agent_name": CHART_NAME})],
        chart=[
            h_call(QUERY_NAME, {"request": "total revenue per region"}),
            h_text("Chart selesai."),
        ],
        query=[h_text("Query siap.")],
    )
    assert turn.authors == [ROOT_NAME, CHART_NAME, QUERY_NAME], turn.authors
    # Agent tool dipanggil Chart dan hasilnya kembali ke Chart yang sama.
    assert QUERY_NAME in turn.calls, turn.calls
    tool_results = [name for name, _ in turn.results]
    assert tool_results.count(QUERY_NAME) == 1, tool_results
    assert "Chart selesai." in turn.answer, turn.answer
    # Satu-satunya transfer di giliran ini: Root → Chart. Pengembalian Query →
    # Chart TIDAK memakai transfer sama sekali.
    assert turn.transfers == [CHART_NAME], turn.transfers


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
