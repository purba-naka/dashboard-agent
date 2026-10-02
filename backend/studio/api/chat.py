"""Endpoint chat agent (Req 16.1, 16.2, 20.1).

- ``POST /workspaces/{ws}/chat`` — stream ``text/event-stream``. ``run.started``
  dikirim segera (< 2 dtk) sebelum agent berjalan, lalu event giliran dari
  :class:`studio.agents.runner.ChatRunner`.
- ``POST /workspaces/{ws}/chat/runs/{run_id}/stop`` — batalkan giliran → ``202``.
- ``GET /workspaces/{ws}/chat/sessions/{sid}/messages`` — riwayat pesan sesi.

Router hanya aktif bila ``app.state.chat_runner`` terpasang (lihat
``wiring.py``); tanpa model dikonfigurasi, aplikasi tetap berjalan tanpa chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse

from studio.agents.runner import APP_NAME, USER_ID
from studio.agents.tools.architect_tools import activate_blueprint
from studio.agents.tools.context import STATE_DASHBOARD_ID
from studio.agents.turn_policy import resolve_approval
from studio.api.errors import StudioError
from studio.api.schemas import ChatRequest
from studio.events.sse import SSE_HEADERS, SSE_MEDIA_TYPE, format_sse

if TYPE_CHECKING:  # pragma: no cover
    from studio.agents.runner import ChatRunner
    from studio.store.repos import Repositories

router = APIRouter(prefix="/api", tags=["chat"])

_DEFAULT_TITLE = "Percakapan"


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


def _runner(request: Request) -> ChatRunner:
    runner = getattr(request.app.state, "chat_runner", None)
    if runner is None:
        raise StudioError(
            "CHAT_UNAVAILABLE",
            "Fitur chat agent tidak aktif (model belum dikonfigurasi).",
            http_status=503,
        )
    return runner


async def _active_dashboard_id(repos: Repositories, workspace_id: str) -> str | None:
    dashboards = await repos.dashboards.list_by_workspace(workspace_id)
    if not dashboards:
        return None
    return max(dashboards, key=lambda d: (d.updated_at, d.created_at, d.id)).id


@router.post("/workspaces/{workspace_id}/chat")
async def chat(workspace_id: str, body: ChatRequest, request: Request) -> StreamingResponse:
    repos = _repos(request)
    runner = _runner(request)
    await repos.workspaces.get(workspace_id)  # 404 bila Workspace tidak ada

    if body.session_id:
        session = await repos.chat_sessions.get(body.session_id, workspace_id=workspace_id)
        session_id = session.id
    else:
        session = await repos.chat_sessions.create(workspace_id, _DEFAULT_TITLE)
        session_id = session.id

    if body.dashboard_id:
        record = await repos.dashboards.get_or_none(body.dashboard_id)
        if record is None or record.workspace_id != workspace_id:
            raise StudioError(
                "NOT_FOUND",
                f"Dashboard '{body.dashboard_id}' tidak ada di Workspace ini.",
                {"entity": "Dashboard", "id": body.dashboard_id},
                http_status=404,
            )
        dashboard_id: str | None = body.dashboard_id
    else:
        dashboard_id = await _active_dashboard_id(repos, workspace_id)
    await runner.ensure_session(
        session_id, workspace_id=workspace_id, dashboard_id=dashboard_id
    )

    approved = await resolve_approval(
        repos.proposals,
        body.approval.proposal_id if body.approval else None,
        session_id,
    )
    # Persetujuan Blueprint → simpan Blueprint aktif berisi slot terpilih (Req 37.6).
    extra_state: dict[str, Any] = {}
    if body.dashboard_id:
        # Sesi lama hanya mengisi dashboard_id saat dibuat; tulis ulang tiap giliran.
        extra_state[STATE_DASHBOARD_ID] = body.dashboard_id
    if approved:
        proposal = await repos.proposals.get_or_none(approved)
        if proposal is not None and proposal.kind == "blueprint":
            await activate_blueprint(
                runner.services,
                workspace_id=workspace_id,
                proposal=proposal,
                selected_slot_ids=body.approval.selected_slot_ids if body.approval else None,
                state=extra_state,
            )
    run_id = uuid.uuid4().hex

    async def frames() -> AsyncIterator[str]:
        # run.started lebih dulu agar Frontend punya run_id/session_id < 2 dtk.
        yield format_sse("run.started", {"run_id": run_id, "session_id": session_id})

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for event in runner.stream(
                    run_id=run_id,
                    session_id=session_id,
                    message=body.message,
                    approved_proposal_id=approved,
                    **({"extra_state": extra_state} if extra_state else {}),
                ):
                    await queue.put(event)
            finally:
                await queue.put(None)

        task = asyncio.ensure_future(pump())
        runner.registry.register(run_id, task)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield format_sse(event["event"], event.get("data"))
        finally:
            runner.registry.discard(run_id)
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(frames(), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)


@router.post(
    "/workspaces/{workspace_id}/chat/runs/{run_id}/stop",
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_run(workspace_id: str, run_id: str, request: Request) -> Response:
    runner = _runner(request)
    runner.registry.cancel(run_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.get("/workspaces/{workspace_id}/chat/sessions/{session_id}/messages")
async def session_messages(
    workspace_id: str, session_id: str, request: Request
) -> dict[str, Any]:
    repos = _repos(request)
    runner = _runner(request)
    await repos.chat_sessions.get(session_id, workspace_id=workspace_id)
    session = await runner.session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    messages: list[dict[str, Any]] = []
    if session is not None:
        for event in session.events or []:
            text = _event_text(event)
            if text:
                messages.append({"author": event.author or "", "text": text})
    return {"session_id": session_id, "messages": messages}


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return ""
    return "".join(
        p.text
        for p in parts
        if isinstance(getattr(p, "text", None), str) and not getattr(p, "thought", None)
    )


__all__ = ["router"]
