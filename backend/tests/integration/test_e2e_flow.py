"""Integration test end-to-end alur lengkap dengan model mock (task 23.2).

Satu alur utuh melewati seluruh lapisan: upload dua CSV → profiling otomatis →
konfirmasi relasi → chat dengan model skrip (``run_sql`` + ``add_chart`` +
``add_insight`` lewat ADK sungguhan) → edit manual via REST → konflik versi
agent (``VERSION_CONFLICT`` mengembalikan state terbaru lalu dicoba ulang) →
undo/redo → Global_Filter dengan propagasi ke chart lain → re-upload yang
menandai Insight_Card ``stale``.

Model mengikuti pola :class:`tests.integration.test_chat_stream.FakeModel`;
aksi bisa berupa callable yang menerima ``llm_request`` agar aksi berikutnya
dapat membaca hasil tool sebelumnya (mis. versi terbaru dari error
``VERSION_CONFLICT``).

_Requirements: 7.4, 17.5, 18.5, 19.1, 20.1, 20.4, 23.1, 26.2_
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
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
from tests.integration.helpers import (
    EXPECTED_FACTS,
    SAMPLES,
    SQL,
    SseReader,
    StreamingASGITransport,
    Studio,
    dataset_rows,
)

# Nama function-call transfer antar-agent ADK.
TRANSFER = "transfer_to_agent"

REGION_BAR_OPTION = {
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "encode": {"x": "region", "y": "revenue"}}],
}
CHANNEL_BAR_OPTION = {
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value"},
    "series": [{"type": "bar", "encode": {"x": "channel", "y": "revenue"}}],
}
WEBSITE = {"kind": "in", "table": "transactions", "column": "channel", "values": ["Website"]}


def call(name: str, args: dict[str, Any]) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]
        )
    )


def text(value: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=value)]))


class ScriptModel(BaseLlm):
    """Model skrip; aksi callable menerima ``llm_request`` (bisa baca hasil tool)."""

    model: str = "script-model"

    _actions: list[Any] = PrivateAttr(default_factory=list)

    @classmethod
    def of(cls, *actions: Any) -> ScriptModel:
        instance = cls()
        instance._actions = list(actions)
        return instance

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if not self._actions:
            yield text("Selesai.")
            return
        action = self._actions.pop(0)
        if callable(action):
            action = action(llm_request)
        yield action


def _tool_payloads(request: Any, tool: str) -> list[dict[str, Any]]:
    """Semali payload ``function_response`` bertanda ``tool`` dari riwayat giliran.

    Menangani dua bentuk pembungkusan ADK: dict langsung atau ``{"result": {...}}``.
    """
    payloads: list[dict[str, Any]] = []
    for content in getattr(request, "contents", []) or []:
        for part in getattr(content, "parts", None) or []:
            resp = getattr(part, "function_response", None)
            if resp is None or resp.name != tool:
                continue
            payload = resp.response
            if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
                payload = payload["result"]
            if isinstance(payload, dict):
                payloads.append(payload)
    return payloads


def retry_after_conflict(tool: str, args_fn: Callable[[int], dict[str, Any]]) -> Any:
    """Aksi callable: ulangi tool ``tool`` memakai versi terbaru dari error
    ``VERSION_CONFLICT`` hasil panggilan sebelumnya (Req 18.5)."""

    def action(request: Any) -> LlmResponse:
        version = None
        for payload in _tool_payloads(request, tool):
            error = payload.get("error") or {}
            if error.get("code") == "VERSION_CONFLICT":
                version = error.get("details", {}).get("current_version")
        assert version is not None, "panggilan sebelumnya tidak menghasilkan VERSION_CONFLICT"
        return call(tool, args_fn(int(version)))

    return action


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
async def chat_turn(
    app: FastAPI, ws: str, message: str
) -> AsyncIterator[tuple[list[Any], list[Any]]]:
    """Satu giliran chat; yield (frame SSE, error frame) setelah ``run.done``."""
    transport = StreamingASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST", f"/api/workspaces/{ws}/chat", json={"message": message}
        ) as resp:
            assert resp.status_code == 200, resp.text
            reader = SseReader(resp)
            frames = await reader.until(lambda f: f.event in ("run.done", "run.stopped"))
    yield frames, [f for f in frames if f.event == "error"]


# ---------------------------------------------------------------------------
# Alur end-to-end
# ---------------------------------------------------------------------------


async def test_alur_lengkap_upload_chat_edit_konflik_undo_filter_reupload(
    tmp_path: Path,
) -> None:
    top_region = EXPECTED_FACTS["top_region"]
    website_rev = next(
        r["revenue"] for r in EXPECTED_FACTS["revenue_by_channel"] if r["channel"] == "Website"
    )

    # Skrip model: tiga giliran chat.
    # Giliran 1 — chart revenue per region (dashboard masih v0 → langsung jadi).
    # Giliran 2 — insight kontributor region teratas.
    # Giliran 3 — chart revenue per channel dengan base_version basi (v1; sudah
    #   tertinggal oleh patch agent + edit manual) → VERSION_CONFLICT → ulangi.
    root = ScriptModel.of(
        call(
            "classify_turn",
            {"intent": "dashboard_modification", "explicit_change_request": True, "evidence": "tambahkan chart"},
        ),
        call(TRANSFER, {"agent_name": QUERY_NAME}),
        call(TRANSFER, {"agent_name": CHART_NAME}),
        text("Chart revenue per region sudah ditambahkan."),
        # Giliran 2.
        call(
            "classify_turn",
            {"intent": "dashboard_modification", "explicit_change_request": True, "evidence": "Buat insight"},
        ),
        call(TRANSFER, {"agent_name": INSIGHT_NAME}),
        text("Insight region teratas sudah ditambahkan."),
        # Giliran 3.
        call(
            "classify_turn",
            {"intent": "dashboard_modification", "explicit_change_request": True, "evidence": "Tambahkan juga chart"},
        ),
        call(TRANSFER, {"agent_name": QUERY_NAME}),
        call(TRANSFER, {"agent_name": CHART_NAME}),
        text("Chart revenue per channel sudah ditambahkan setelah konflik versi diulang."),
    )
    query = ScriptModel.of(
        call("run_sql", {"sql": SQL["revenue_by_region"]}),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
        # Giliran 3.
        call("run_sql", {"sql": SQL["revenue_by_channel"]}),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
    )
    chart = ScriptModel.of(
        # Giliran 1: dashboard baru (v0).
        call(
            "add_chart",
            {
                "base_version": 0,
                "title": "Revenue per Region",
                "chart_type": "bar",
                "option": REGION_BAR_OPTION,
            },
        ),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
        # Giliran 3: base_version basi (v1) → konflik → ulangi dengan versi terbaru.
        call(
            "add_chart",
            {
                "base_version": 1,
                "title": "Revenue per Channel",
                "chart_type": "bar",
                "option": CHANNEL_BAR_OPTION,
            },
        ),
        retry_after_conflict(
            "add_chart",
            lambda version: {
                "base_version": version,
                "title": "Revenue per Channel",
                "chart_type": "bar",
                "option": CHANNEL_BAR_OPTION,
            },
        ),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
    )
    insight = ScriptModel.of(
        call("run_sql", {"sql": SQL["revenue_by_region"]}),
        call(
            "add_insight",
            {
                "base_version": 1,
                "insight_type": "top_bottom_contributors",
                "title": "Region teratas",
                "text": (
                    f"{top_region['region']} memimpin dengan pendapatan "
                    f"{top_region['revenue']:,} dari {top_region['transactions']} transaksi."
                ).replace(",", "."),
            },
        ),
        call(TRANSFER, {"agent_name": ROOT_NAME}),
    )
    models: dict[str, BaseLlm] = {
        "root": root,
        "profiler": ScriptModel.of(),
        "query": query,
        "chart": chart,
        "insight": insight,
    }

    app = make_app(tmp_path, models)
    async with running(app) as client:
        studio = Studio(app, client)

        # --- 1) Upload dua CSV → profil otomatis (Req 7.4, 20.1) --------------
        ws = await studio.create_workspace()
        customers_ds = await studio.upload_sample(ws, "customers.csv")
        await studio.upload_sample(ws, "transactions.csv")

        # Profil tersimpan untuk kedua dataset (hook job profiling).
        detail = (
            await client.get(f"/api/workspaces/{ws}/datasets/{customers_ds}")
        ).json()
        assert detail["dataset"]["row_count"] == EXPECTED_FACTS["tables"]["customers"]["rows"]
        assert detail["column_profiles"], "profil kolom belum tersimpan"
        assert detail["dataset"]["table_name"] == "customers"

        # --- 2) Konfirmasi relasi customers.customer_id ↔ transactions --------
        rel = await studio.relation_between(ws, "customers", "customer_id", "transactions", status="candidate")
        await studio.confirm(ws, rel["id"])
        confirmed = await studio.relations(ws, status="confirmed")
        assert len(confirmed) == 1

        dash = await studio.create_dashboard(ws)
        dash_id = dash["id"]

        # --- 3) Giliran chat 1: run_sql + add_chart (Req 20.1, 20.4) ----------
        async with chat_turn(app, ws, "Tolong tambahkan chart revenue per region") as (frames, errors):
            assert not errors, [f.data for f in errors]
            tools = [
                f.data["tool"]
                for f in frames
                if f.event == "tool.call" and f.data["tool"] != TRANSFER
            ]
            assert tools == ["classify_turn", "run_sql", "add_chart"]
            (patch,) = [f for f in frames if f.event == "patch.applied"]
            assert patch.data["dashboard_id"] == dash_id
            assert patch.data["version"] == 1 and patch.data["source"] == "agent"
            texts = [f.data["text"] for f in frames if f.event == "text.delta"]
            assert any("revenue per region sudah ditambahkan" in t.lower() for t in texts)

        snap = await studio.get_dashboard(dash_id)
        items = snap["content"]["items"]
        assert len(items) == 1
        region_chart = next(iter(items))
        assert items[region_chart]["title"] == "Revenue per Region"
        assert snap["version"] == 1

        # --- 4) Giliran chat 2: run_sql + add_insight (verifier angka) --------
        async with chat_turn(app, ws, "Buat insight region penyumbang teratas") as (frames, errors):
            assert not errors, [f.data for f in errors]
            results = {f.data["tool"]: f.data for f in frames if f.event == "tool.result"}
            assert results["add_insight"]["ok"] is True
            (patch,) = [f for f in frames if f.event == "patch.applied"]
            assert patch.data["version"] == 2

        snap = await studio.get_dashboard(dash_id)
        items = snap["content"]["items"]
        assert len(items) == 2
        insight_id = next(i for i, item in items.items() if item["kind"] == "insight")
        assert items[insight_id]["text"].startswith(top_region["region"])

        # --- 5) Edit manual via REST (Req 17.5): pindahkan chart --------------
        layout = {"x": 2, "y": 3, "w": 6, "h": 5}
        event = await studio.apply_ok(
            dash_id, {"type": "set_layout", "changes": {region_chart: layout}}, 2
        )
        assert event["version"] == 3 and event["source"] == "user"
        snap = await studio.get_dashboard(dash_id)
        assert snap["content"]["layout"][region_chart] == layout

        # --- 6) Giliran chat 3: konflik versi → state terbaru → ulangi -------
        # Model memakai base_version=1 (basi; sudah v3 karena patch agent + edit
        # manual). Tool mengembalikan error VERSION_CONFLICT + versi terbaru,
        # model mengulang dengan versi benar (Req 18.5, 19.1).
        async with chat_turn(app, ws, "Tambahkan juga chart revenue per channel") as (frames, errors):
            # Tool gagal satu kali lalu berhasil — error pertama adalah konflik.
            assert [(f.data["code"]) for f in errors] == ["VERSION_CONFLICT"], [
                f.data for f in errors
            ]
            conflicts = [
                f for f in frames if f.event == "tool.result" and f.data["tool"] == "add_chart"
            ]
            assert [f.data["ok"] for f in conflicts] == [False, True]
            (patch,) = [f for f in frames if f.event == "patch.applied"]
            assert patch.data["version"] == 4 and patch.data["source"] == "agent"

        snap = await studio.get_dashboard(dash_id)
        assert snap["version"] == 4 and len(snap["content"]["items"]) == 3

        # --- 7) Undo lalu redo edit terakhir (Req 19.1) ------------------------
        channel_chart = next(
            i
            for i, item in snap["content"]["items"].items()
            if item["title"] == "Revenue per Channel"
        )
        undo_event = (
            await client.post(f"/api/dashboards/{dash_id}/undo", json={"base_version": 4})
        ).json()
        assert undo_event["version"] == 5
        snap = await studio.get_dashboard(dash_id)
        assert channel_chart not in snap["content"]["items"]
        assert len(snap["content"]["items"]) == 2

        redo_event = (
            await client.post(f"/api/dashboards/{dash_id}/redo", json={"base_version": 5})
        ).json()
        assert redo_event["version"] == 6
        snap = await studio.get_dashboard(dash_id)
        assert channel_chart in snap["content"]["items"]

        # --- 8) Global_Filter + propagasi ke semua chart (Req 23.1) ------------
        event = await studio.apply_ok(
            dash_id, {"type": "set_global_filters", "filters": [WEBSITE]}, 6
        )
        assert event["version"] == 7
        rendered = await studio.render(dash_id)
        # Chart region (JOIN customers↔transactions) ikut terfilter.
        region_rows = dataset_rows(rendered["items"][region_chart]["option"])
        assert sum(row["revenue"] for row in region_rows) == website_rev
        # Chart channel juga terfilter → tinggal baris Website.
        channel_rows = dataset_rows(rendered["items"][channel_chart]["option"])
        assert channel_rows == [
            {"channel": "Website", "revenue": website_rev, "transactions": 618}
        ]

        # --- 9) Re-upload skema sama → data_version naik → insight stale -----
        lines = (SAMPLES / "customers.csv").read_bytes().splitlines(keepends=True)
        resp = await client.post(
            f"/api/workspaces/{ws}/datasets/{customers_ds}/reupload",
            files={"file": ("customers.csv", b"".join(lines[:151]), "text/csv")},
        )
        assert resp.status_code == 202, resp.text
        job = await studio.wait_job(resp.json()["job_id"])
        assert job["status"] == "done", job

        snap = await studio.get_dashboard(dash_id)
        assert snap["item_status"][insight_id] == {"invalid": False, "stale": True}
        assert snap["item_status"][region_chart] == {"invalid": False, "stale": False}
