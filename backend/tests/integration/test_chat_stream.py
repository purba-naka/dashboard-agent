"""Integration test stream SSE chat agent dengan model ADK di-mock (task 18.12).

Model diganti :class:`FakeModel` (subclass ``BaseLlm``) yang memainkan skrip
respons — sisa alur ADK (transfer antar-agent, eksekusi tool sungguhan atas
Data_Engine/DashboardStore, penyimpanan sesi) berjalan seperti produksi.

_Requirements: 8.5, 16.2, 16.3, 16.6, 20.1, 28.4_
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import FastAPI
from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from studio.agents.definitions import CHART_NAME, QUERY_NAME, ROOT_NAME
from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings
from tests.integration.helpers import SseReader, StreamingASGITransport, Studio

MESSAGE = "Tolong tambahkan chart revenue per region ke dashboard"

BAR_OPTION = {
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
}

SALES_CSV = b"region,revenue\nJawa,1500\nBali,700\n"

# Nama function-call transfer antar-agent ADK.
TRANSFER = "transfer_to_agent"


def call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        )
    )


def text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


class FakeModel(BaseLlm):
    """Model skrip: tiap panggilan mengonsumsi satu aksi (function call/teks)."""

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
        action = self._actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            action = action(llm_request)
        yield action


def fake_models(
    root: list[Any], *, query: list[Any] | None = None, chart: list[Any] | None = None
) -> dict[str, BaseLlm]:
    """Mapping model untuk ``app.state.models`` (agent lain memakai default)."""
    default = FakeModel.of()
    return {
        "root": FakeModel.of(*root),
        "profiler": default,
        "query": FakeModel.of(*(query if query is not None else [text("Dilewati.")])),
        "chart": FakeModel.of(*(chart if chart is not None else [text("Dilewati.")])),
        "insight": default,
    }


def make_app(tmp_path: Path, models: dict[str, BaseLlm]) -> FastAPI:
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
    app.state.models = models
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
    """``POST /workspaces/{ws}/chat`` sebagai stream SSE (body tidak di-buffer)."""
    transport = StreamingASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", f"/api/workspaces/{ws}/chat", headers={}, json=body
        ) as resp:
            yield resp, SseReader(resp)


async def setup_sales(studio: Studio) -> dict[str, str]:
    ws = await studio.create_workspace()
    await studio.upload_csv(ws, "sales.csv", SALES_CSV)
    dash = await studio.create_dashboard(ws)
    return {"ws": ws, "dash": dash["id"]}


# ---------------------------------------------------------------------------
# Alur utama: run.started → agent/tool → patch → run.done
# ---------------------------------------------------------------------------


async def test_chat_stream_urutan_event_teks_tool_patch(tmp_path: Path) -> None:
    root_script = [
        # 1) Klasifikasi giliran: permintaan perubahan eksplisit (evidence
        #    substring pesan pengguna) → tool mutasi diizinkan.
        call(
            "classify_turn",
            {"intent": "dashboard_modification", "explicit_change_request": True, "evidence": "tambahkan chart"},
        ),
        # 2) Delegasikan SQL ke Query_Agent.
        call("transfer_to_agent", {"agent_name": QUERY_NAME}),
        # 3) Delegasikan chart ke Chart_Designer_Agent.
        call("transfer_to_agent", {"agent_name": CHART_NAME}),
        # 4) Ringkas.
        text("Selesai — chart revenue per region sudah ditambahkan."),
    ]
    query_script = [
        call("run_sql", {"sql": "SELECT region, revenue FROM sales"}),
        call("transfer_to_agent", {"agent_name": ROOT_NAME}),
    ]
    chart_script = [
        call(
            "add_chart",
            {
                "base_version": 0,
                "title": "Revenue per Region",
                "chart_type": "bar",
                "option": BAR_OPTION,
            },
        ),
        call("transfer_to_agent", {"agent_name": ROOT_NAME}),
    ]
    app = make_app(tmp_path, fake_models(root_script, query=query_script, chart=chart_script))
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await setup_sales(studio)

        started = asyncio.get_running_loop().time()
        async with chat_stream(
            app, ids["ws"], {"message": MESSAGE}
        ) as (resp, reader):
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            # run.started lebih dulu, < 2 dtk (Req 16.1).
            first = await reader.next_frame(timeout=2.0)
            elapsed = asyncio.get_running_loop().time() - started
            assert first is not None and first.event == "run.started"
            assert first.data["run_id"] and first.data["session_id"]
            assert elapsed < 2.0, elapsed

            frames = [first] + await reader.until(lambda f: f.event == "run.done")

        events = [f.event for f in frames]
        assert events[-1] == "run.done" and events.count("run.started") == 1
        # Agent aktif dilaporkan untuk tiap agent yang berbicara.
        agents = [f.data["agent"] for f in frames if f.event == "agent.active"]
        assert agents[0] == ROOT_NAME and QUERY_NAME in agents and CHART_NAME in agents
        # Tool call/result berpasangan dan terpisah dari teks.
        tools = [f.data["tool"] for f in frames if f.event == "tool.call"]
        assert "classify_turn" in tools and "run_sql" in tools and "add_chart" in tools
        assert all(f.event == "tool.result" for f in frames if f.event == "tool.result")
        # Patch dari tool mutasi mengalir ke stream (Req 18.3).
        (patch,) = [f for f in frames if f.event == "patch.applied"]
        assert patch.data["dashboard_id"] == ids["dash"]
        assert patch.data["version"] == 1 and patch.data["source"] == "agent"
        # Teks model (Root) hadir dan urut setelah patch.
        texts = [f.data["text"] for f in frames if f.event == "text.delta" and f.data["agent"] == ROOT_NAME]
        assert any("Selesai" in t for t in texts)
        assert frames.index(patch) < len(frames) - 1

        # Dashboard benar-benar berubah.
        snap = await studio.get_dashboard(ids["dash"])
        items = snap["content"]["items"]
        assert len(items) == 1 and next(iter(items.values()))["title"] == "Revenue per Region"


async def test_chat_stream_stop_menghasilkan_run_stopped(tmp_path: Path) -> None:
    # Root mentransfer ke Query_Agent yang modelnya menggantung → Stop.
    hang = asyncio.Event()

    async def hang_action(_request: Any) -> LlmResponse:
        await hang.wait()
        return text("tidak akan pernah")

    app = make_app(
        tmp_path,
        fake_models(
            [call("transfer_to_agent", {"agent_name": QUERY_NAME})],
            query=[hang_action],
        ),
    )
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await setup_sales(studio)

        async with chat_stream(app, ids["ws"], {"message": MESSAGE}) as (resp, reader):
            assert resp.status_code == 200
            first = await reader.next_frame()
            assert first is not None and first.event == "run.started"
            run_id = first.data["run_id"]
            # Tunggu agent aktif (transfer sudah diproses) lalu stop (Req 16.6).
            await reader.until(lambda f: f.event == "agent.active")
            stop = await client.post(f"/api/workspaces/{ids['ws']}/chat/runs/{run_id}/stop")
            assert stop.status_code == 202
            frames = await reader.until(lambda f: f.event in ("run.stopped", "run.done"))

        assert [f.event for f in frames][-1] == "run.stopped"
        assert frames[-1].data["run_id"] == run_id
        hang.set()


async def test_chat_stream_kegagalan_sub_agent_menghasilkan_error(tmp_path: Path) -> None:
    app = make_app(
        tmp_path,
        fake_models(
            [call("transfer_to_agent", {"agent_name": QUERY_NAME})],
            query=[RuntimeError("model query meledak")],
        ),
    )
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await setup_sales(studio)

        async with chat_stream(app, ids["ws"], {"message": MESSAGE}) as (resp, reader):
            assert resp.status_code == 200
            frames = await reader.until(lambda f: f.event == "run.done")

        events = [f.event for f in frames]
        assert "error" in events and events[-1] == "run.done"
        err = next(f for f in frames if f.event == "error")
        assert "meledak" in err.data["message"] or err.data["code"] == "AGENT_ERROR"


async def test_edit_manual_masuk_konteks_root_sekali_dan_riwayat_pulih(tmp_path: Path) -> None:
    from studio.agents.context import build_turn_context

    app = make_app(
        tmp_path,
        fake_models([text("Halo! Dashboard siap dianalisis."), text("Oke.")]),
    )
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await setup_sales(studio)

        async with chat_stream(app, ids["ws"], {"message": "Halo"}) as (_, reader):
            started = await reader.until(lambda f: f.event == "run.started")
            session_id = started[0].data["session_id"]
            await reader.until(lambda f: f.event == "run.done")

        # Edit manual (set_layout via REST) → otomatis di konteks Root giliran berikutnya (Req 20.1).
        await studio.apply_ok(
            ids["dash"],
            {"type": "set_layout", "changes": {}},
            (await studio.get_dashboard(ids["dash"]))["version"],
        )
        services = app.state.chat_runner.services

        async def root_context() -> str:
            return await build_turn_context(
                services, workspace_id=ids["ws"], dashboard_id=ids["dash"], session_id=session_id
            )

        assert "set_layout" in await root_context()

        # Riwayat ADK tetap bersih: edit tidak menyamar sebagai pesan pengguna.
        messages = (
            await client.get(f"/api/workspaces/{ids['ws']}/chat/sessions/{session_id}/messages")
        ).json()["messages"]
        assert any("Halo! Dashboard siap" in m["text"] for m in messages)
        assert not any("[Edit manual pengguna]" in m["text"] for m in messages)

        # Setelah agent melihatnya satu giliran, edit tidak diulang.
        body = {"message": "Lanjut", "session_id": session_id}
        async with chat_stream(app, ids["ws"], body) as (_, reader):
            await reader.until(lambda f: f.event == "run.done")
        assert "set_layout" not in await root_context()

    # "Restart": app baru di atas data_dir sama → riwayat pulih (Req 28.4).
    app2 = make_app(tmp_path, fake_models([text("restart")]))
    async with running(app2) as client2:
        resp = await client2.get(f"/api/workspaces/{ids['ws']}/chat/sessions/{session_id}/messages")
        assert resp.status_code == 200, resp.text
        restored = resp.json()["messages"]
        assert any("Halo! Dashboard siap" in m["text"] for m in restored)


async def test_chat_tanpa_runner_mengembalikan_503(tmp_path: Path) -> None:
    """App tanpa model (248 test lain) tetap hidup; chat menolak rapi."""
    settings = Settings(data_dir=tmp_path / "data", query_runner_mode="inline")
    app = create_app(settings, extra_routers=[chat.router], lifespan_hooks=[configure_agents])
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace()
        resp = await client.post(f"/api/workspaces/{ws}/chat", json={"message": "halo"})
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "CHAT_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Util kecil agar JSON frame mudah diperiksa manual bila test gagal
# ---------------------------------------------------------------------------


def dump(frames: list[Any]) -> str:  # pragma: no cover - hanya debugging
    return json.dumps([{ "event": f.event, "data": f.data} for f in frames], indent=2, default=str)


def unique_id() -> str:  # pragma: no cover
    return uuid4().hex


async def test_chat_dashboard_id_workspace_lain_ditolak(tmp_path: Path) -> None:
    app = make_app(tmp_path, fake_models([text("Halo.")]))
    async with running(app) as client:
        studio = Studio(app, client)
        ids = await setup_sales(studio)
        other = await studio.create_workspace("Lain")
        foreign = (await studio.create_dashboard(other))["id"]
        resp = await client.post(
            f"/api/workspaces/{ids['ws']}/chat",
            json={"message": MESSAGE, "dashboard_id": foreign},
        )
        assert resp.status_code == 404
