"""Regresi BUG B: ``NO_QUERY`` tidak boleh membuat giliran buntu.

Gejala di trace nyata: Chart_Designer_Agent memanggil ``get_query_schema`` yang
mengembalikan ``NO_QUERY`` (``studio/agents/tools/data_tools.py``) atau tool
mutasi yang memanggil ``load_query`` (``studio/agents/tools/dashboard_tools.py``).
Hasil itu hanya berisi "Belum ada query. Jalankan run_sql terlebih dahulu" —
langkah yang TIDAK bisa dilakukan Chart_Designer_Agent — sehingga giliran
berakhir tanpa apa pun.

Perbaikan: jaring pemulihan deterministik di ``after_tool_callback``. Hasil tool
``NO_QUERY`` diganti versi yang memuat langkah berikutnya secara eksplisit
(tool ``Query_Agent`` untuk Chart, ``run_sql`` untuk Slot_Builder). Pemulihan
tidak memaksa ``transfer_to_agent``: pada arsitektur agent-as-tool, Query_Agent
adalah TOOL milik Chart_Designer_Agent dan ``disallow_transfer_to_peers=True``,
sehingga transfer ke Query_Agent justru ditolak ADK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from google.adk.agents import LlmAgent

from studio.agents.definitions import (
    CHART_NAME,
    QUERY_NAME,
    SLOT_BUILDER_NAME,
    build_agents,
)
from studio.agents.tools.context import ToolServices
from studio.agents.tools.guard import RESULT_TOO_LARGE, after_tool_callback as row_guard
from studio.agents.tools.transfer_guard import make_no_query_recovery
from studio.agents.turn_policy import NO_QUERY_RECOVERY_KEY, begin_turn
from studio.store.db import Database
from studio.store.repos import Repositories

from tests.unit.agent_harness import call, dummy_services, fake_models, run_turn, text

NO_QUERY_RESPONSE = {
    "ok": False,
    "error": {
        "code": "NO_QUERY",
        "message": "Belum ada query. Jalankan run_sql terlebih dahulu atau berikan query_id.",
        "details": {},
    },
}


class FakeActions:
    """``tool_context.actions`` ADK (duck-typed)."""

    def __init__(self) -> None:
        self.transfer_to_agent: str | None = None


def fake_tool_context(
    message: str = "Tambahkan bar chart revenue", invocation_id: str = "inv-1"
) -> Any:
    state: dict[str, Any] = dict(begin_turn(user_message=message, invocation_id=invocation_id))
    return SimpleNamespace(
        state=state,
        actions=FakeActions(),
        invocation_id=invocation_id,
        user_content=None,
    )


def tool_named(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


@pytest.fixture
async def live_services(tmp_path: Path) -> AsyncIterator[tuple[ToolServices, str]]:
    """``ToolServices`` dengan Repositories sungguhan: cukup agar
    ``get_query_schema`` benar-benar mengembalikan ``NO_QUERY`` (tanpa query)."""
    db = await Database(tmp_path / "studio.db").open()
    try:
        repos = Repositories(db)
        workspace = await repos.workspaces.create("WS")
        services = ToolServices(
            repos=repos, engine=SimpleNamespace(), profiler=SimpleNamespace()
        )
        yield services, workspace.id
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Alur ADK nyata lewat build_agents: hasil tool NO_QUERY memuat langkah lanjut
# ---------------------------------------------------------------------------


async def test_no_query_dari_get_query_schema_memberi_langkah_berikutnya(
    live_services: tuple[ToolServices, str],
) -> None:
    """Chart menerima NO_QUERY → hasil tool harus menyebut tool ``Query_Agent``."""
    services, ws_id = live_services
    turn = await run_turn(
        "Tambahkan bar chart total revenue per channel ke dashboard sekarang.",
        services=services,
        state_delta={"workspace_id": ws_id},
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME})],
        chart=[call("get_query_schema", {}), text("Chart buntu: tidak ada query.")],
        query=[text("Query menjalankan SQL.")],
    )
    replaced = turn.response_of("get_query_schema")
    assert replaced is not None, turn.results
    assert replaced.get("recovered") is True, replaced
    next_action = replaced.get("next_action") or {}
    assert next_action.get("tool") == QUERY_NAME, replaced
    # Kompatibilitas: kode error asli tetap ada, pesannya bertambah instruksi.
    assert replaced["error"]["code"] == "NO_QUERY"
    assert QUERY_NAME in replaced["error"]["message"]


async def test_setelah_pemulihan_chart_memanggil_tool_query_agent(
    live_services: tuple[ToolServices, str],
) -> None:
    """Model yang mengikuti langkah lanjut memanggil tool ``Query_Agent`` (agent-as-tool)."""
    services, ws_id = live_services
    turn = await run_turn(
        "Tambahkan bar chart total revenue per channel ke dashboard sekarang.",
        services=services,
        state_delta={"workspace_id": ws_id},
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME})],
        chart=[
            call("get_query_schema", {}),
            call(QUERY_NAME, {"request": "total revenue per channel"}),
            text("Chart dibuat."),
        ],
        query=[text("Query selesai.")],
    )
    assert QUERY_NAME in turn.authors, turn.authors
    assert CHART_NAME in turn.authors


# ---------------------------------------------------------------------------
# Jaring pemulihan di level callback (tanpa ADK)
# ---------------------------------------------------------------------------


def test_chart_mendapat_instruksi_tool_query_agent() -> None:
    recover = make_no_query_recovery(retry_tool=QUERY_NAME, inner=row_guard)
    ctx = fake_tool_context()
    out = recover(tool_named("get_query_schema"), {}, ctx, NO_QUERY_RESPONSE)
    assert out is not None and out["recovered"] is True
    assert out["next_action"]["tool"] == QUERY_NAME
    assert "panggil tool `Query_Agent`" in out["error"]["message"]
    # Tidak memaksa transfer: Query_Agent adalah tool, bukan target transfer.
    assert ctx.actions.transfer_to_agent is None


def test_slot_builder_mendapat_instruksi_run_sql() -> None:
    """Slot_Builder tidak punya tool Query_Agent: ia harus menjalankan SQL sendiri."""
    recover = make_no_query_recovery(retry_tool=None, inner=row_guard)
    ctx = fake_tool_context()
    out = recover(tool_named("add_chart"), {}, ctx, NO_QUERY_RESPONSE)
    assert out is not None and out["recovered"] is True
    assert out["next_action"]["tool"] == "run_sql"
    assert "run_sql" in out["error"]["message"]
    assert ctx.actions.transfer_to_agent is None


def test_pemulihan_hanya_sekali_per_giliran() -> None:
    """Jaring pemulihan tidak boleh memicu loop pemulihan tanpa henti."""
    recover = make_no_query_recovery(retry_tool=QUERY_NAME, inner=row_guard)
    ctx = fake_tool_context()
    pertama = recover(tool_named("get_query_schema"), {}, ctx, NO_QUERY_RESPONSE)
    assert pertama is not None and pertama["recovered"] is True
    assert ctx.state[NO_QUERY_RECOVERY_KEY] == "get_query_schema"

    kedua = recover(tool_named("get_query_schema"), {}, ctx, NO_QUERY_RESPONSE)
    assert kedua is not None
    assert "recovered" not in kedua
    assert kedua["error"]["code"] == "NO_QUERY"
    assert "sampaikan kepada pengguna" in kedua["error"]["message"]


def test_hasil_normal_dilewatkan() -> None:
    recover = make_no_query_recovery(retry_tool=QUERY_NAME, inner=row_guard)
    ctx = fake_tool_context()
    assert recover(tool_named("get_query_schema"), {}, ctx, {"ok": True, "columns": []}) is None


def test_jaring_batas_baris_tetap_berlaku() -> None:
    """Guard lama (Privacy_Guard > 200 baris) tidak boleh hilang karena guard baru."""
    recover = make_no_query_recovery(retry_tool=QUERY_NAME, inner=row_guard)
    ctx = fake_tool_context()
    besar = {"ok": True, "rows": [["x"]] * 201}
    out = recover(tool_named("run_sql"), {}, ctx, besar)
    assert out is not None and out["error"]["code"] == RESULT_TOO_LARGE


def test_wiring_chart_dan_slot_builder_memakai_jaring_no_query() -> None:
    root = build_agents(fake_models(), dummy_services())
    agents = {agent.name: agent for agent in root.sub_agents}
    assert agents[CHART_NAME].after_tool_callback is not None
    assert agents[CHART_NAME].after_tool_callback is not row_guard
    slot_builder = _find(agents, SLOT_BUILDER_NAME)
    assert slot_builder is not None and slot_builder.after_tool_callback is not row_guard


def _find(agents: dict[str, LlmAgent], name: str) -> LlmAgent | None:
    """Slot_Builder adalah sub-agent dari Blueprint_Builder (bukan sub-agent Root)."""
    for agent in agents.values():
        if agent.name == name:
            return agent
        for child in getattr(agent, "sub_agents", []) or []:
            if child.name == name:
                return child
    return None
