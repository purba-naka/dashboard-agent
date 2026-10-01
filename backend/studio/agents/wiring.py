"""Wiring agent/chat ke aplikasi (task 18.11).

``configure_agents`` adalah ``LifespanHook`` (lihat ``studio.app``). Ia hanya
aktif bila ``app.state.models`` terisi (``{agent: LiteLlm}`` dari Model_Gateway);
tanpa model, aplikasi berjalan penuh tanpa fitur chat sehingga test/dev yang
tidak mengonfigurasi model tetap jalan.

Saat aktif ia memasang:

* ``app.state.chat_runner`` — :class:`~studio.agents.runner.ChatRunner`;

* hook penghapusan sesi ADK saat Workspace dihapus (``workspace_cleanup_hooks``);
* ``app.state.insight_rewriter`` — penyusun ulang teks Insight_Card saat hasil
  numerik berubah (Req 15.2).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.agents.definitions import build_agents
from studio.agents.runner import ChatRunner
from studio.agents.tools.context import ToolServices

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def configure_agents(app: FastAPI) -> AsyncIterator[None]:
    """Pasang runner chat + hook bila ``app.state.models`` tersedia."""
    models = getattr(app.state, "models", None)
    if not models:
        yield
        return

    services = ToolServices.from_app_state(app.state)
    root_agent = build_agents(models, services)
    data_dir = Path(app.state.settings.data_dir)
    runner = ChatRunner(
        root_agent, services, data_dir=data_dir, database_url=app.state.settings.database_url
    )
    app.state.chat_runner = runner

    # Edit manual (Req 20.1) masuk otomatis lewat konteks Root
    # (``context.build_turn_context`` → ``manual_edits_since_last_turn``), bukan riwayat.
    repos = app.state.repos

    async def cleanup_sessions(workspace_id: str) -> None:
        for session in await repos.chat_sessions.list_by_workspace(workspace_id):
            await runner.delete_session(session.id)

    app.state.workspace_cleanup_hooks.append(cleanup_sessions)

    # Penyusun ulang teks insight: pakai model insight untuk menulis ulang teks
    # agar cocok dengan bukti baru. Fallback None → refresh tetap jalan tanpa
    # menulis ulang teks (endpoint mengembalikan INSIGHT_TEXT_OUTDATED bila perlu).
    app.state.insight_rewriter = None

    # Semantic_Drafter: pengayaan LLM dengan output terstruktur (Req 32.3).
    drafter = getattr(app.state, "semantic_drafter", None)
    if drafter is not None and "semantic" in models:
        from studio.agents.context import load_prompt
        from studio.data.semantic_drafter import AdkSemanticEnricher

        drafter.enricher = AdkSemanticEnricher(models["semantic"], load_prompt("semantic_drafter"))

    try:
        yield
    finally:
        app.state.chat_runner = None
        if drafter is not None:
            drafter.enricher = None


__all__ = ["configure_agents"]
