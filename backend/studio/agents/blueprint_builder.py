"""Blueprint_Builder_Agent: loop slot Blueprint deterministik (Req 37.7–37.12, 39.1).

Root cukup satu ``transfer_to_agent``. Loop slot, filter bawaan, review, dan
ringkasan dikerjakan kode. LLM (``Slot_Builder_Agent``) hanya dipanggil untuk
menulis SQL dan item satu slot. Event chat (``blueprint.progress``,
``patch.applied``, ``review.findings``) dititipkan di ``custom_metadata`` dan
disiramkan runner ke stream chat.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent, LlmAgent
from google.adk.events import Event, EventActions
from google.adk.sessions.state import State
from google.genai import types
from pydantic import ConfigDict

from studio.agents.tools.architect_tools import advance_blueprint, review_findings
from studio.agents.tools.context import CHAT_EVENTS_KEY, STATE_WORKSPACE_ID, ToolServices
from studio.api.errors import StudioError
from studio.events.sse import to_jsonable

BUILDER_NAME = "Blueprint_Builder_Agent"
SLOT_BUILDER_NAME = "Slot_Builder_Agent"


class BlueprintBuilderAgent(BaseAgent):
    """Bangun semua slot Blueprint aktif dalam satu invocation (state ``temp:`` tetap hidup)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    services: Any
    slot_builder: LlmAgent

    def __init__(self, *, services: ToolServices, slot_builder: LlmAgent) -> None:
        super().__init__(
            name=BUILDER_NAME,
            description="Membangun slot Blueprint yang sudah disetujui, lalu review dan ringkasan.",
            services=services,
            slot_builder=slot_builder,
            sub_agents=[slot_builder],
        )

    def _event(self, ctx: Any, delta: dict[str, Any], payload: dict[str, Any], text: str | None = None) -> Event:
        events = payload.get(CHAT_EVENTS_KEY) or []
        return Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(state_delta=dict(delta)),
            content=types.Content(role="model", parts=[types.Part(text=text)]) if text else None,
            custom_metadata={CHAT_EVENTS_KEY: to_jsonable(events)} if events else None,
        )

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Event, None]:
        while True:
            delta: dict[str, Any] = {}
            state = State(value=ctx.session.state, delta=delta)
            try:
                step = await advance_blueprint(self.services, state, ctx.invocation_id)
            except StudioError as exc:
                yield self._event(ctx, delta, {}, exc.message)
                return
            if step["done"]:
                break
            slot = step["slot"]
            # Teks event jadi jangkar konteks Slot_Builder (include_contents="none"):
            # tiap slot mulai bersih, tidak membawa riwayat slot sebelumnya.
            yield self._event(ctx, delta, step, f"Membangun slot `{slot['slot_id']}`: {slot.get('purpose', '')}")
            async for event in self.slot_builder.run_async(ctx):
                yield event
            # Slot yang masih `building` ditandai gagal oleh advance_blueprint berikutnya.

        reviewed = await review_findings(self.services, str(state.get(STATE_WORKSPACE_ID)), state)
        payload = {CHAT_EVENTS_KEY: [*(step.get(CHAT_EVENTS_KEY) or []), *reviewed.get(CHAT_EVENTS_KEY, [])]}
        yield self._event(ctx, delta, payload, _summary(step, reviewed))


def _summary(done: dict[str, Any], reviewed: dict[str, Any]) -> str:
    """Ringkasan deterministik (tanpa panggilan LLM)."""
    lines = ["Pembangunan Blueprint selesai."]
    failed = done.get("failed_slots") or []
    if failed:
        lines.append("Slot gagal:")
        lines += [f"- `{f['slot_id']}`: {(f.get('error') or {}).get('message', 'tidak diketahui')}" for f in failed]
    if done.get("default_filters_error"):
        lines.append(f"Filter bawaan gagal diterapkan: {done['default_filters_error']['message']}")
    findings = reviewed.get("findings") or []
    if findings:
        lines.append("Temuan review:")
        lines += [f"- {f.get('message')}" for f in findings[:5]]
    return "\n".join(lines)


__all__ = ["BUILDER_NAME", "SLOT_BUILDER_NAME", "BlueprintBuilderAgent"]
