"""Runner ADK → stream event SSE chat (Req 16.1, 16.3, 20.1, 28.2).

``ChatRunner`` membungkus ``google.adk.runners.Runner`` dengan
``DatabaseSessionService`` (SQLite, path absolut). Untuk tiap giliran ia:

1. memastikan sesi ADK ada (dengan ``workspace_id``/``dashboard_id`` di state);
2. terapkan approval + reset state giliran via ``turn_policy.begin_turn``;
3. jalankan ``run_async`` dan terjemahkan setiap ADK ``Event`` ke event SSE chat
   (``agent.active``, ``text.delta``, ``tool.call``, ``tool.result``,
   ``patch.applied``, event chat khusus dari ``chat_events``, ``error``);
4. keluarkan ``run.done`` di akhir (atau ``run.stopped`` bila dibatalkan).

``run.started`` dikirim oleh endpoint chat (task 18.11) sebelum ``stream``
berjalan agar tiba < 2 dtk. ``RunRegistry`` memetakan ``run_id`` → task asyncio
untuk stop kooperatif.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    STATE_DASHBOARD_ID,
    STATE_WORKSPACE_ID,
)
from studio.agents.turn_policy import begin_turn, resolve_approval
from studio.events.sse import to_jsonable

if TYPE_CHECKING:
    from google.adk.agents import LlmAgent
    from google.adk.events import Event

    from studio.agents.tools.context import ToolServices
    from studio.store.dashboard_store import PatchEvent

logger = logging.getLogger(__name__)

APP_NAME = "dashboard_studio"
USER_ID = "local"
_SESSIONS_DB = "adk_sessions.db"


def sessions_db_url(data_dir: Path, database_url: str | None = None) -> str:
    """URL Session_Service ADK: PostgreSQL yang sama dengan Metadata_Store bila
    ``database_url`` PostgreSQL (tabel ADK dibuat ADK sendiri), selain itu SQLite lokal."""
    scheme = (database_url or "").split("://", 1)[0].split("+", 1)[0]
    if scheme in ("postgresql", "postgres"):
        return "postgresql+asyncpg://" + database_url.split("://", 1)[1]  # type: ignore[union-attr]
    path = (Path(data_dir) / "data" / _SESSIONS_DB).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{path.as_posix()}"


class RunRegistry:
    """``run_id`` → task giliran yang berjalan (untuk stop)."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    def register(self, run_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[run_id] = task

    def discard(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True


class ChatRunner:
    """Satu instance per aplikasi; membungkus Runner ADK + Session_Service."""

    def __init__(
        self,
        root_agent: LlmAgent,
        services: ToolServices,
        *,
        data_dir: Path,
        session_service: Any | None = None,
        database_url: str | None = None,
    ) -> None:
        self._services = services
        self._session_service = session_service or DatabaseSessionService(
            db_url=sessions_db_url(data_dir, database_url)
        )
        self._runner = Runner(
            app_name=APP_NAME,
            agent=root_agent,
            session_service=self._session_service,
        )
        self.registry = RunRegistry()

    @property
    def services(self) -> ToolServices:
        return self._services

    async def _skip_blueprint_slots(self, session_id: str) -> None:
        """Run dihentikan saat membangun Blueprint → slot sisa ``skipped`` (Req 37.11)."""
        from studio.agents.tools.architect_tools import skip_remaining_slots
        from studio.agents.turn_policy import BLUEPRINT_ACTIVE_KEY

        try:
            session = await self._session_service.get_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id
            )
            bp_id = (session.state or {}).get(BLUEPRINT_ACTIVE_KEY) if session else None
            if isinstance(bp_id, str):
                await skip_remaining_slots(self._services, bp_id)
        except Exception:  # noqa: BLE001 — pembatalan tidak boleh gagal karena ini
            logger.exception("Gagal menandai slot Blueprint skipped (sesi %s)", session_id)

    @property
    def session_service(self) -> Any:
        return self._session_service

    async def ensure_session(
        self, session_id: str, *, workspace_id: str, dashboard_id: str | None
    ) -> None:
        """Buat sesi ADK bila belum ada; simpan ``workspace_id``/``dashboard_id`` di state."""
        existing = await self._session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        if existing is not None:
            return
        state: dict[str, Any] = {STATE_WORKSPACE_ID: workspace_id}
        if dashboard_id:
            state[STATE_DASHBOARD_ID] = dashboard_id
        await self._session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id, state=state
        )

    async def delete_session(self, session_id: str) -> None:
        """Hapus sesi ADK (dipanggil saat sesi/Workspace dihapus)."""
        try:
            await self._session_service.delete_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id
            )
        except Exception:  # sesi mungkin belum pernah dibuat
            logger.debug("delete_session ADK gagal untuk %s", session_id, exc_info=True)

    async def _dashboard_version(self, session_id: str) -> int | None:
        """Versi Dashboard sesi saat ini (penanda edit manual yang sudah dilihat agent)."""
        session = await self._session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )
        dashboard_id = (session.state or {}).get(STATE_DASHBOARD_ID) if session else None
        if not dashboard_id:
            return None
        record = await self._services.repos.dashboards.get_or_none(dashboard_id)
        return record.version if record is not None else None

    async def _mark_edits_seen(self, session_id: str, version: int | None) -> None:
        """Edit manual s.d. ``version`` sudah masuk konteks agent (Req 20.1)."""
        if version is None:
            return
        try:
            await self._services.repos.chat_sessions.set_last_agent_version(session_id, version)
        except Exception:  # noqa: BLE001 — penanda gagal hanya membuat edit tampil ulang
            logger.exception("Gagal menyimpan last_agent_version (sesi %s)", session_id)

    async def stream(
        self,
        *,
        run_id: str,
        session_id: str,
        message: str,
        approved_proposal_id: str | None,
        extra_state: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Hasilkan event SSE chat untuk satu giliran. ``run.started`` dikirim pemanggil.

        ``extra_state``: state sesi tambahan (mis. Blueprint aktif yang baru disetujui).
        """
        state_delta = begin_turn(
            user_message=message, approved_proposal_id=approved_proposal_id
        )
        if extra_state:
            state_delta.update(extra_state)
        content = types.Content(role="user", parts=[types.Part(text=message)])
        run_config = RunConfig(streaming_mode=StreamingMode.SSE)

        # Versi diambil SEBELUM giliran: edit manual selama run tetap muncul giliran berikutnya.
        seen_version = await self._dashboard_version(session_id)
        last_agent: str | None = None
        stopped = False
        stream_state = self.new_stream_state()
        try:
            async for event in self._runner.run_async(
                user_id=USER_ID,
                session_id=session_id,
                new_message=content,
                state_delta=state_delta,
                run_config=run_config,
            ):
                author = event.author or ""
                usage = event.usage_metadata
                if usage is not None and not event.partial:
                    # Ukuran konteks per agent: dasar mengukur efisiensi konteks.
                    logger.info(
                        "llm_usage run=%s agent=%s prompt_tokens=%s output_tokens=%s",
                        run_id, author, usage.prompt_token_count, usage.candidates_token_count,
                    )
                if author and author != "user" and author != last_agent:
                    last_agent = author
                    yield {"event": "agent.active", "data": {"agent": author}}
                for out in self.translate(event, author, stream_state):
                    yield out
        except asyncio.CancelledError:
            stopped = True
            await self._skip_blueprint_slots(session_id)
            yield {"event": "run.stopped", "data": {"run_id": run_id}}
            raise
        except Exception as exc:  # kegagalan model/runtime → error terstruktur
            logger.exception("Giliran chat gagal (run_id=%s)", run_id)
            yield {
                "event": "error",
                "data": {"code": "AGENT_ERROR", "message": str(exc)},
            }
        finally:
            await self._mark_edits_seen(session_id, seen_version)
            if not stopped:
                yield {"event": "run.done", "data": {"run_id": run_id}}

    @staticmethod
    def new_stream_state() -> dict[str, bool]:
        """State per giliran: apakah delta parsial sudah dikirim."""
        return {"streamed": False}

    @staticmethod
    def translate(
        event: Event, author: str, stream_state: dict[str, bool]
    ) -> list[dict[str, Any]]:
        """Terjemahkan satu ADK ``Event`` ke daftar event SSE chat.

        Part ``thought`` (reasoning model) dikirim sebagai ``thought.delta``,
        terpisah dari jawaban. Pada streaming SSE, ADK mengirim delta parsial
        lalu satu event final berisi teks gabungan; event final itu dilewati
        agar teks tidak muncul dua kali.
        """
        out: list[dict[str, Any]] = []

        if event.partial:
            stream_state["streamed"] = True
        skip_text = not event.partial and stream_state["streamed"]
        if not event.partial:
            stream_state["streamed"] = False

        if not skip_text:
            thought, text = ChatRunner._texts_of(event)
            if thought:
                out.append(
                    {"event": "thought.delta", "data": {"agent": author, "text": thought}}
                )
            if text:
                out.append(
                    {"event": "text.delta", "data": {"agent": author, "text": text}}
                )

        # Potongan function_call parsial (streaming LiteLLM) tanpa argumen lengkap
        # dilewati; panggilan final dikirim sekali oleh event non-parsial.
        for call in [] if event.partial else event.get_function_calls():
            out.append(
                {
                    "event": "tool.call",
                    "data": {
                        "agent": author,
                        "tool": call.name,
                        "args_summary": _args_summary(call.args),
                    },
                }
            )

        for resp in event.get_function_responses():
            payload = resp.response if isinstance(resp.response, dict) else {}
            ok = bool(payload.get("ok", True)) if payload else True
            out.append(
                {
                    "event": "tool.result",
                    "data": {
                        "agent": author,
                        "tool": resp.name,
                        "ok": ok,
                        "summary": _result_summary(payload),
                    },
                }
            )
            if not ok:
                err = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(err, dict):
                    out.append(
                        {
                            "event": "error",
                            "data": {
                                "code": err.get("code", "TOOL_ERROR"),
                                "message": err.get("message", ""),
                                "agent": author,
                            },
                        }
                    )
            # Event chat khusus yang dititipkan tool (approval.request, dll).
            for chat_evt in payload.get(CHAT_EVENTS_KEY, []) if isinstance(payload, dict) else []:
                if isinstance(chat_evt, dict) and "event" in chat_evt:
                    out.append(
                        {"event": chat_evt["event"], "data": to_jsonable(chat_evt.get("data"))}
                    )

        # Agent non-LLM (Blueprint_Builder) menitipkan event chat di custom_metadata.
        meta = event.custom_metadata or {}
        for chat_evt in meta.get(CHAT_EVENTS_KEY, []) if isinstance(meta, dict) else []:
            if isinstance(chat_evt, dict) and "event" in chat_evt:
                out.append({"event": chat_evt["event"], "data": to_jsonable(chat_evt.get("data"))})

        if event.error_message:
            out.append(
                {
                    "event": "error",
                    "data": {
                        "code": event.error_code or "MODEL_ERROR",
                        "message": event.error_message,
                        "agent": author,
                    },
                }
            )
        return out

    @staticmethod
    def _texts_of(event: Event) -> tuple[str, str]:
        """``(thought, text)`` dari part teks event."""
        content = event.content
        if content is None or not content.parts:
            return "", ""
        thought: list[str] = []
        text: list[str] = []
        for p in content.parts:
            if not isinstance(getattr(p, "text", None), str):
                continue
            if getattr(p, "function_call", None) is not None:
                continue
            (thought if getattr(p, "thought", None) else text).append(p.text)
        return "".join(thought), "".join(text)

    def emit_patch_event(self, patch: PatchEvent) -> dict[str, Any]:
        """Bentuk event ``patch.applied`` (dipublikasikan lewat EventBus, bukan stream ini)."""
        return {"event": "patch.applied", "data": to_jsonable(patch)}


def _args_summary(args: Any, limit: int = 160) -> str:
    if not args:
        return ""
    try:
        text = ", ".join(f"{k}={_short(v)}" for k, v in dict(args).items())
    except Exception:
        text = str(args)
    return text[:limit]


def _result_summary(payload: Any, limit: int = 160) -> str:
    if not isinstance(payload, dict):
        return _short(payload, limit)
    for key in ("message", "summary", "note"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value[:limit]
    keys = [k for k in payload if k not in {"ok", CHAT_EVENTS_KEY}]
    return f"ok ({', '.join(keys[:6])})" if keys else "ok"


def _short(value: Any, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "APP_NAME",
    "USER_ID",
    "ChatRunner",
    "RunRegistry",
    "sessions_db_url",
]
