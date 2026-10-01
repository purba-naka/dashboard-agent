"""InstructionProvider membangun konteks sekali per (invocation, versi Dashboard, slot)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from studio.agents import context as ctx_mod
from studio.agents.tools.context import STATE_WORKSPACE_ID


async def test_cache_per_invocation_dan_versi(monkeypatch: Any) -> None:
    calls: list[str] = []
    version = {"v": 1}

    async def fake_build(services: Any, agent_key: str, state: Any, session_id: Any) -> str:
        calls.append(agent_key)
        return f"v{version['v']}"

    async def fake_version(services: Any, state: Any) -> int:
        return version["v"]

    monkeypatch.setattr(ctx_mod, "build_agent_context", fake_build)
    monkeypatch.setattr(ctx_mod, "_dashboard_version", fake_version)
    provider = ctx_mod.make_instruction("chart", services=None)  # type: ignore[arg-type]

    def ctx(inv: str) -> Any:
        return SimpleNamespace(state={STATE_WORKSPACE_ID: "ws"}, invocation_id=inv, session=SimpleNamespace(id="s"))

    first = await provider(ctx("i1"))
    assert await provider(ctx("i1")) == first and len(calls) == 1
    version["v"] = 2  # mutasi Dashboard \u2192 bangun ulang
    assert (await provider(ctx("i1"))).endswith("v2") and len(calls) == 2
    await provider(ctx("i2"))  # invocation baru \u2192 bangun ulang
    assert len(calls) == 3
