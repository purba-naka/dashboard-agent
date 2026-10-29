"""Harness ADK deterministik untuk tes guard orkestrasi (tanpa LLM).

Model diganti :class:`FakeModel` (subclass ``BaseLlm``) yang memainkan skrip
respons, sehingga sisa alur ADK — pemanggilan ``before_tool_callback``/
``after_tool_callback``, transfer antar-agent, dan eksekusi tool sungguhan —
berjalan seperti produksi. Dipakai tes regresi BUG A (transfer pada giliran
saran) dan BUG B (``NO_QUERY`` buntu).

Agent dirakit lewat :func:`studio.agents.definitions.build_agents` (wiring asli),
bukan duplikat — bila guard tidak dipasang di sana, tes ini merah.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from google.adk.models import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from studio.agents.definitions import build_agents
from studio.agents.tools.context import ToolServices
from studio.agents.turn_policy import begin_turn

TRANSFER = "transfer_to_agent"

MODEL_KEYS = ("root", "profiler", "query", "chart", "insight", "architect")


def call(name: str, args: dict[str, Any] | None = None) -> LlmResponse:
    """Satu function call model (mis. ``transfer_to_agent``)."""
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=types.FunctionCall(name=name, args=args or {}))],
        )
    )


def text(value: str) -> LlmResponse:
    """Satu respons teks model (jawaban akhir)."""
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

    @property
    def remaining(self) -> int:
        """Jumlah aksi yang belum dikonsumsi (0 = model ini tidak pernah dipanggil habis)."""
        return len(self._actions)

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if not self._actions:
            yield text("Selesai.")
            return
        yield self._actions.pop(0)


def fake_models(**scripts: list[Any]) -> dict[str, BaseLlm]:
    """Mapping ``models`` untuk ``build_agents``; agent tanpa skrip menjawab teks pendek."""
    fallback = FakeModel.of(text("Dilewati."))
    out: dict[str, BaseLlm] = {key: fallback for key in MODEL_KEYS}
    out.update({key: FakeModel.of(*value) for key, value in scripts.items()})
    return out


def dummy_services(*, dashboard_store: Any = None) -> ToolServices:
    """``ToolServices`` minimal: tool tidak dieksekusi tes ini kecuali disebut eksplisit."""
    return ToolServices(
        repos=SimpleNamespace(),
        engine=SimpleNamespace(),
        profiler=SimpleNamespace(),
        dashboard_store=dashboard_store,
    )


@dataclass
class Turn:
    """Ringkasan event satu giliran ADK."""

    events: list[Any] = field(default_factory=list)

    @property
    def authors(self) -> list[str]:
        """Agent yang menghasilkan event (urutan kemunculan pertama, termasuk Root)."""
        out: list[str] = []
        for event in self.events:
            if event.author and event.author not in out:
                out.append(event.author)
        return out

    @property
    def calls(self) -> list[str]:
        """Nama tool yang dipanggil model, berurutan."""
        return [c.name for e in self.events for c in (e.get_function_calls() or [])]

    @property
    def results(self) -> list[dict[str, Any]]:
        """Function response ``(name, response)`` berurutan."""
        return [
            (r.name, r.response)  # type: ignore[misc]
            for e in self.events
            for r in (e.get_function_responses() or [])
        ]

    @property
    def transfers(self) -> list[str]:
        """Target transfer yang benar-benar dieksekusi ADK (dari ``event.actions``)."""
        return [e.actions.transfer_to_agent for e in self.events if e.actions and e.actions.transfer_to_agent]

    @property
    def answer(self) -> str:
        """Gabungan teks jawaban semua agent."""
        return "".join(
            part.text
            for e in self.events
            if e.content and e.content.parts
            for part in e.content.parts
            if isinstance(getattr(part, "text", None), str)
        )

    def response_of(self, tool: str) -> dict[str, Any] | None:
        """Response tool pertama bernama ``tool``."""
        for name, response in self.results:
            if name == tool:
                return response if isinstance(response, dict) else None
        return None


async def run_turn(
    message: str,
    *,
    services: ToolServices | None = None,
    state_delta: dict[str, Any] | None = None,
    approved_proposal_id: str | None = None,
    **scripts: list[Any],
) -> Turn:
    """Jalankan satu giliran chat lewat wiring ``build_agents`` dengan model skrip."""
    root = build_agents(fake_models(**scripts), services or dummy_services())
    sessions = InMemorySessionService()
    runner = Runner(app_name="harness", agent=root, session_service=sessions)
    await sessions.create_session(app_name="harness", user_id="u", session_id="s")
    delta = dict(
        begin_turn(user_message=message, approved_proposal_id=approved_proposal_id)
    )
    if state_delta:
        delta.update(state_delta)
    turn = Turn()
    async for event in runner.run_async(
        user_id="u",
        session_id="s",
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        state_delta=delta,
    ):
        turn.events.append(event)
    return turn


__all__ = [
    "FakeModel",
    "MODEL_KEYS",
    "TRANSFER",
    "Turn",
    "call",
    "dummy_services",
    "fake_models",
    "run_turn",
    "text",
]
