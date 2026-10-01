"""Penerjemahan event ADK → SSE chat: thought terpisah dari teks, tanpa duplikasi final."""

from __future__ import annotations

from google.adk.events import Event
from google.genai import types

from studio.agents.runner import ChatRunner


def ev(*parts: types.Part, partial: bool | None = None) -> Event:
    return Event(author="root", content=types.Content(role="model", parts=list(parts)), partial=partial)


def thought(text: str) -> types.Part:
    return types.Part(text=text, thought=True)


def texts(frames: list[dict], kind: str) -> list[str]:
    return [f["data"]["text"] for f in frames if f["event"] == kind]


def run(events: list[Event]) -> list[dict]:
    state = ChatRunner.new_stream_state()
    out: list[dict] = []
    for e in events:
        out += ChatRunner.translate(e, "root", state)
    return out


def test_thought_dipisah_dari_teks() -> None:
    frames = run([ev(thought("mikir"), types.Part(text="Halo"))])
    assert texts(frames, "thought.delta") == ["mikir"]
    assert texts(frames, "text.delta") == ["Halo"]


def test_event_final_setelah_streaming_tidak_diulang() -> None:
    frames = run(
        [
            ev(thought("mi"), partial=True),
            ev(thought("kir"), partial=True),
            ev(types.Part(text="Ha"), partial=True),
            ev(types.Part(text="lo"), partial=True),
            ev(thought("mikir"), types.Part(text="Halo"), partial=False),
        ]
    )
    assert "".join(texts(frames, "thought.delta")) == "mikir"
    assert "".join(texts(frames, "text.delta")) == "Halo"


def test_function_call_parsial_tidak_digandakan() -> None:
    """Streaming LiteLLM memecah satu tool call jadi banyak potongan; kirim sekali saja."""
    chunk = types.Part(function_call=types.FunctionCall(name="run_sql", will_continue=True))
    final = types.Part(function_call=types.FunctionCall(name="run_sql", args={"sql": "SELECT 1"}))
    frames = run([ev(chunk, partial=True), ev(chunk, partial=True), ev(final, partial=False)])
    calls = [f["data"] for f in frames if f["event"] == "tool.call"]
    assert calls == [{"agent": "root", "tool": "run_sql", "args_summary": "sql=SELECT 1"}]


def test_model_non_streaming_tetap_dipancarkan() -> None:
    frames = run([ev(types.Part(text="A")), ev(types.Part(text="B"))])
    assert texts(frames, "text.delta") == ["A", "B"]
