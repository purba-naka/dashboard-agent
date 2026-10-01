import asyncio
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types


class Mock(BaseLlm):
    script: list = []

    async def generate_content_async(self, llm_request, stream=False):
        step = self.script.pop(0)
        print("  [model", self.model, "stream", stream, "tools", list(llm_request.tools_dict), "]")
        if isinstance(step, Exception):
            raise step
        for r in step:
            yield r


def fc(name, **args):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args))]))


def txt(t, partial=False):
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]), partial=partial)


async def ping(x: str) -> dict:
    """ping"""
    return {"ok": True, "x": x, "chat_events": [{"event": "profile.summary", "data": {}}]}


async def main():
    shared = []
    root_m = Mock(model="root"); sub_m = Mock(model="sub")
    root_m.script = shared; sub_m.script = shared
    shared += [
        [fc("ping", x="a")],
        [fc("transfer_to_agent", agent_name="sub")],
        [txt("hal", True), txt("o", True), txt("halo")],
        [fc("transfer_to_agent", agent_name="root")],
        [txt("selesai")],
    ]
    sub = LlmAgent(name="sub", model=sub_m, instruction="s", tools=[ping])
    root = LlmAgent(name="root", model=root_m, instruction=lambda ctx: "r " + str(ctx.state.get("workspace_id")), tools=[ping], sub_agents=[sub])
    ss = InMemorySessionService()
    r = Runner(app_name="a", agent=root, session_service=ss)
    s = await ss.create_session(app_name="a", user_id="u", state={"workspace_id": "w"})
    async for ev in r.run_async(user_id="u", session_id=s.id, new_message=types.Content(role="user", parts=[types.Part(text="hi")]),
                                state_delta={"temp:x": 1}, run_config=RunConfig(streaming_mode=StreamingMode.SSE)):
        print(ev.author, "partial=", ev.partial, "fc=", [c.name for c in ev.get_function_calls()],
              "fr=", [(x.name, x.response) for x in ev.get_function_responses()],
              "text=", [p.text for p in (ev.content.parts if ev.content else []) if p.text], "err=", ev.error_code, ev.error_message,
              "actions.transfer=", ev.actions.transfer_to_agent, "delta=", ev.actions.state_delta)
    s = await ss.get_session(app_name="a", user_id="u", session_id=s.id)
    print(len(s.events), s.state)

asyncio.run(main())
