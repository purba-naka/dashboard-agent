"""Format wire Server-Sent Events (``id:`` / ``event:`` / ``data:``).

Dipakai oleh dua stream (Req 16.1, 16.3, 18.3):

- stream workspace ``GET /workspaces/{ws}/events`` — event dari
  :mod:`studio.events.bus` yang membawa sequence id untuk ``Last-Event-ID``;
- stream chat ``POST /workspaces/{ws}/chat`` — event dari runner agent yang
  diformat langsung tanpa bus (tanpa ``id:``).

Serialisasi JSON mendukung model Pydantic, dataclass, ``datetime``/``date``/
``time`` (ISO 8601), ``Enum``, ``UUID``, ``Decimal``, ``Path``, set/tuple,
serta objek yang memiliki ``model_dump()``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import Enum
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

if TYPE_CHECKING:  # pragma: no cover
    from studio.events.bus import Event, Subscription

__all__ = [
    "CHAT_EVENT_TYPES",
    "DEFAULT_HEARTBEAT_SECONDS",
    "SSE_HEADERS",
    "SSE_MEDIA_TYPE",
    "format_comment",
    "format_event",
    "format_retry",
    "format_sse",
    "heartbeat",
    "stream_subscription",
    "to_json",
    "to_jsonable",
]

SSE_MEDIA_TYPE: Final = "text/event-stream"

# Header respons yang disarankan untuk StreamingResponse SSE (menonaktifkan
# buffering proxy/nginx dan cache).
SSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

DEFAULT_HEARTBEAT_SECONDS: Final = 15.0

# Pemisah baris menurut spesifikasi SSE (CRLF, LF, CR) — tidak termasuk
# pemisah Unicode lain yang dikenali ``str.splitlines()``.
_SSE_LINE_BREAK: Final = re.compile(r"\r\n|\r|\n")

# Tipe event stream chat (sinkron dengan `ChatSseEvent` di frontend/src/lib/types.ts).
CHAT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "run.started",
        "agent.active",
        "text.delta",
        "thought.delta",
        "tool.call",
        "tool.result",
        "patch.applied",
        "approval.request",
        "relation.candidates",
        "profile.summary",
        "semantic.draft",
        "blueprint.progress",
        "review.findings",
        "error",
        "run.stopped",
        "run.done",
    }
)


# ---------------------------------------------------------------------------
# Serialisasi JSON
# ---------------------------------------------------------------------------


def _default(obj: Any) -> Any:
    """Hook ``json.dumps(default=...)`` untuk tipe non-JSON bawaan."""
    # Pydantic v2 (dan objek serupa): mode="json" sudah menangani datetime dll.
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}
    # datetime adalah subclass date, keduanya punya isoformat().
    if isinstance(obj, (_dt.datetime, _dt.date, _dt.time)):
        return obj.isoformat()
    if isinstance(obj, _dt.timedelta):
        return obj.total_seconds()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (UUID, PurePath)):
        return str(obj)
    if isinstance(obj, Decimal):
        # Integral → int agar tidak kehilangan presisi bilangan bulat besar.
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, (set, frozenset, tuple)):
        return list(obj)
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def to_json(data: Any) -> str:
    """Serialisasi ``data`` ke JSON satu baris (UTF-8 apa adanya, tanpa spasi).

    ``NaN``/``Infinity`` ditolak (``ValueError``) karena bukan JSON valid
    bagi ``JSON.parse`` di browser.
    """
    return json.dumps(
        data,
        default=_default,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def to_jsonable(data: Any) -> Any:
    """Konversi ``data`` ke struktur JSON-native (dict/list/str/number/bool/None)."""
    return json.loads(to_json(data))


# ---------------------------------------------------------------------------
# Formatter wire
# ---------------------------------------------------------------------------


def _check_field(name: str, value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError(f"SSE field {name!r} must not contain line breaks")
    return value


def format_sse(
    event: str | None,
    data: Any = None,
    *,
    id: str | int | None = None,
    retry_ms: int | None = None,
    data_is_json: bool = False,
) -> str:
    """Bangun satu frame SSE.

    Urutan field: ``id:``, ``event:``, ``retry:``, ``data:``, diakhiri baris
    kosong. ``data`` diserialisasi ke JSON kecuali ``data_is_json=True``
    (string sudah berupa JSON). Payload multi-baris dipecah menjadi beberapa
    baris ``data:`` sesuai spesifikasi SSE.
    """
    lines: list[str] = []
    if id is not None:
        lines.append(f"id: {_check_field('id', str(id))}")
    if event is not None:
        lines.append(f"event: {_check_field('event', event)}")
    if retry_ms is not None:
        lines.append(f"retry: {int(retry_ms)}")
    payload = data if data_is_json else to_json(data)
    if not isinstance(payload, str):  # pragma: no cover - defensif
        raise TypeError("data_is_json=True requires a str payload")
    # Hanya CRLF/LF/CR yang merupakan pemisah baris SSE; ``str.splitlines()``
    # juga memecah U+2028/U+2029/U+0085/\x0b/\x0c/\x1c-\x1e yang bisa muncul
    # di dalam string JSON (ensure_ascii=False) dan merusak payload.
    for line in _SSE_LINE_BREAK.split(payload):
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def format_event(event: Event) -> str:
    """Format :class:`studio.events.bus.Event` (membawa ``id`` untuk ``Last-Event-ID``)."""
    return format_sse(event.type, event.data_json, id=event.id, data_is_json=True)


def format_comment(text: str = "") -> str:
    """Frame komentar SSE (``: text``) — diabaikan klien, berguna sebagai keep-alive."""
    body = text.splitlines() or [""]
    return "".join(f": {line}\n" if line else ":\n" for line in body) + "\n"


def heartbeat() -> str:
    """Frame keep-alive standar."""
    return format_comment("ping")


def format_retry(retry_ms: int) -> str:
    """Frame yang hanya mengatur interval reconnect ``EventSource`` klien."""
    return f"retry: {int(retry_ms)}\n\n"


async def stream_subscription(
    subscription: Subscription,
    *,
    heartbeat_seconds: float | None = DEFAULT_HEARTBEAT_SECONDS,
    retry_ms: int | None = None,
) -> AsyncIterator[str]:
    """Ubah :class:`~studio.events.bus.Subscription` menjadi frame SSE siap kirim.

    Replay event setelah ``Last-Event-ID`` dikirim lebih dulu (ditangani
    subscription), lalu event live. Bila tidak ada event selama
    ``heartbeat_seconds``, kirim komentar keep-alive. Generator selesai bila
    subscription ditutup (mis. overflow → klien reconnect lalu replay); pada
    disconnect/cancel subscription selalu dilepas.

    Contoh (FastAPI)::

        sub = bus.subscribe(ws_id, request.headers.get("last-event-id"))
        return StreamingResponse(stream_subscription(sub),
                                 media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS)
    """
    try:
        if retry_ms is not None:
            yield format_retry(retry_ms)
        else:
            # Kirim sesuatu segera agar header respons ter-flush ke klien.
            yield format_comment("connected")
        while True:
            try:
                event = await subscription.next(timeout=heartbeat_seconds)
            except StopAsyncIteration:
                return
            if event is None:
                yield heartbeat()
                continue
            yield format_event(event)
    finally:
        subscription.close()
