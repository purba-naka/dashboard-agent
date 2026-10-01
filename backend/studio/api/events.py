"""Stream SSE event Workspace: ``GET /api/workspaces/{ws}/events`` (Req 5.3, 16.5, 18.3).

Event yang dikirim (dari :class:`studio.events.bus.EventBus`): ``patch.applied``,
``job.progress``, ``job.done``, ``job.failed``, ``dataset.profiled``,
``relation.updated``. Setiap frame membawa ``id:`` (sequence per Workspace)
sehingga ``EventSource`` otomatis mengirim ``Last-Event-ID`` saat reconnect
dan bus me-replay event yang terlewat dari ring buffer.

``Last-Event-ID`` dibaca dari header (reconnect otomatis ``EventSource``) atau
query ``?last_event_id=`` (koneksi pertama, karena ``EventSource`` tidak bisa
menyetel header). Bila replay tidak lengkap (kursor tidak dikenal — mis.
setelah restart backend — atau event sudah tergusur dari buffer), frame
``event: resync`` tanpa ``id:`` dikirim lebih dulu agar Frontend me-refetch
state lewat REST (``GET /dashboards/{id}/patches?since_version=``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from studio.events.sse import (
    DEFAULT_HEARTBEAT_SECONDS,
    SSE_HEADERS,
    SSE_MEDIA_TYPE,
    format_sse,
    stream_subscription,
)

if TYPE_CHECKING:  # pragma: no cover
    from studio.events.bus import EventBus, Subscription
    from studio.store.repos import Repositories

router = APIRouter(prefix="/api", tags=["events"])

# Interval reconnect yang disarankan ke EventSource (ms).
RECONNECT_RETRY_MS = 3000
RESYNC_EVENT = "resync"


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


def _bus(request: Request) -> EventBus:
    return request.app.state.bus


def _heartbeat_seconds(request: Request) -> float:
    settings = getattr(request.app.state, "settings", None)
    value = getattr(settings, "sse_heartbeat_seconds", None)
    return float(value) if value else DEFAULT_HEARTBEAT_SECONDS


async def _event_stream(
    request: Request,
    sub: Subscription,
    last_event_id: str | None,
    heartbeat_seconds: float,
) -> AsyncIterator[str]:
    """Frame SSE untuk satu klien; subscription selalu dilepas saat selesai/disconnect."""
    frames = stream_subscription(
        sub, heartbeat_seconds=heartbeat_seconds, retry_ms=RECONNECT_RETRY_MS
    )
    try:
        # Frame pertama (retry:) tidak mengonsumsi event; kirim segera agar
        # header respons ter-flush, lalu sinyal resync bila perlu.
        yield await anext(frames)
        if not sub.replay_complete:
            data: dict[str, Any] = {"reason": "replay_incomplete", "last_event_id": last_event_id}
            yield format_sse(RESYNC_EVENT, data)
        async for frame in frames:
            # Deteksi disconnect juga di jalur ASGI >= 2.4 (Starlette tidak
            # memantau http.disconnect di sana); heartbeat membatasi latensinya.
            if await request.is_disconnected():
                break
            yield frame
    finally:
        await frames.aclose()  # menutup subscription (finally di stream_subscription)
        sub.close()  # idempoten


@router.get("/workspaces/{workspace_id}/events")
async def workspace_events(
    workspace_id: str,
    request: Request,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    """Stream event Workspace; dukung ``Last-Event-ID`` (header atau query)."""
    await _repos(request).workspaces.get(workspace_id)  # 404 envelope bila tidak ada

    # Header (reconnect otomatis) lebih baru daripada query URL awal.
    last_event_id = (last_event_id_header or "").strip() or (last_event_id_query or "").strip() or None
    sub = _bus(request).subscribe(workspace_id, last_event_id)
    return StreamingResponse(
        _event_stream(request, sub, last_event_id, _heartbeat_seconds(request)),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
