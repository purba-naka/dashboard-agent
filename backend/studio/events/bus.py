"""Event bus pub/sub in-process per Workspace (Req 16.3, 16.5, 18.3).

Model
-----
- Setiap Workspace punya *channel* dengan sequence counter monoton, ring
  buffer event terakhir (untuk replay ``Last-Event-ID``), dan himpunan
  subscriber.
- ``Event.id`` berbentuk ``"<epoch>-<seq>"``. ``epoch`` adalah token acak per
  instance bus (per proses), sehingga ``Last-Event-ID`` dari proses sebelumnya
  (setelah restart backend) dikenali sebagai tidak dikenal alih-alih
  disalahartikan sebagai posisi di sequence baru.
- Payload diserialisasi ke JSON **saat publish**: error serialisasi muncul di
  sisi publisher dan event di buffer imutabel.

Thread-safety
-------------
:meth:`EventBus.publish` aman dipanggil dari thread mana pun (mis. job ingest
di ``asyncio.to_thread``) maupun dari coroutine. Penetapan sequence, penulisan
buffer, dan penjadwalan pengiriman terjadi di bawah satu lock; pengiriman ke
``asyncio.Queue`` subscriber selalu dijalankan di event loop milik subscriber
(langsung bila pemanggil sudah berada di loop itu, selain itu lewat
``loop.call_soon_threadsafe``). Karena penjadwalan dilakukan di dalam lock dan
``call_soon_threadsafe`` FIFO, urutan event per subscriber = urutan sequence.

Subscriber lambat (pilihan desain)
----------------------------------
Queue subscriber dibatasi (``queue_size``). Publisher **tidak pernah
menunggu**. Bila queue subscriber penuh, subscriber tersebut **di-drop**:
queue-nya dikosongkan, subscription ditutup (``overflowed = True``), dan
stream SSE-nya berakhir. ``EventSource`` di browser otomatis reconnect dengan
``Last-Event-ID`` = event terakhir yang benar-benar diterimanya, lalu bus
me-replay sisa event dari ring buffer. Bila event yang dibutuhkan sudah keluar
dari buffer, ``Subscription.replay_complete`` bernilai False dan klien
mengandalkan resync ``GET /dashboards/{id}/patches?since_version=`` (Req 16.5)
yang memang selalu dilakukan saat reconnect.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

from studio.events.sse import to_json

__all__ = [
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_QUEUE_SIZE",
    "WORKSPACE_EVENT_TYPES",
    "Event",
    "EventBus",
    "Subscription",
    "get_event_bus",
]

# Tipe event stream workspace (sinkron dengan `WorkspaceSseEvent` di
# frontend/src/lib/types.ts).
WORKSPACE_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "patch.applied",
        "job.progress",
        "job.done",
        "job.failed",
        "dataset.profiled",
        "relation.updated",
        "semantic.updated",
        "semantic.warning",
    }
)

DEFAULT_BUFFER_SIZE: Final = 1000
DEFAULT_QUEUE_SIZE: Final = 256

_CLOSE: Final = object()  # sentinel penutup queue subscriber


@dataclass(frozen=True, slots=True)
class Event:
    """Satu event workspace yang sudah diberi sequence dan diserialisasi."""

    seq: int
    id: str
    workspace_id: str
    type: str
    data_json: str
    created_at: _dt.datetime

    @property
    def data(self) -> Any:
        """Payload sebagai struktur JSON-native (hasil parse ulang)."""
        return json.loads(self.data_json)


@dataclass(slots=True)
class _Channel:
    buffer: deque[Event]
    seq: int = 0
    # Sequence terbesar yang sudah tergusur dari ring buffer (0 = belum ada).
    evicted_seq: int = 0
    subscribers: set[Subscription] = field(default_factory=set)


class Subscription:
    """Langganan satu klien ke event sebuah Workspace.

    Dapat dipakai sebagai async iterator dan/atau async context manager::

        async with bus.subscribe(ws_id, last_event_id) as sub:
            async for event in sub:
                ...

    Harus dibuat (``EventBus.subscribe``) dari dalam event loop yang berjalan;
    konsumsi dilakukan di loop yang sama.
    """

    __slots__ = (
        "_bus",
        "_loop",
        "_queue",
        "_replay",
        "_last_enqueued",
        "_closed",
        "overflowed",
        "replay_complete",
        "workspace_id",
        "last_delivered_id",
    )

    def __init__(
        self,
        bus: EventBus,
        workspace_id: str,
        loop: asyncio.AbstractEventLoop,
        queue_size: int,
        replay: list[Event],
        last_enqueued: int,
        replay_complete: bool,
    ) -> None:
        self._bus = bus
        self._loop = loop
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_size)
        self._replay: deque[Event] = deque(replay)
        self._last_enqueued = last_enqueued
        self._closed = False
        self.workspace_id = workspace_id
        #: True bila subscription di-drop karena queue penuh (klien harus reconnect).
        self.overflowed = False
        #: False bila sebagian event setelah ``Last-Event-ID`` tidak lagi tersedia
        #: di ring buffer (klien perlu resync state via REST).
        self.replay_complete = replay_complete
        #: ``Event.id`` terakhir yang dikembalikan ke konsumen.
        self.last_delivered_id: str | None = None

    # -- konsumsi ---------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    async def next(self, timeout: float | None = None) -> Event | None:
        """Event berikutnya; ``None`` bila ``timeout`` habis tanpa event.

        Raise ``StopAsyncIteration`` bila subscription sudah ditutup.
        """
        if self._replay:
            return self._mark(self._replay.popleft())
        if self._closed and self._queue.empty():
            raise StopAsyncIteration
        try:
            if timeout is None:
                item = await self._queue.get()
            else:
                item = await asyncio.wait_for(self._queue.get(), timeout)
        except TimeoutError:
            return None
        if item is _CLOSE:
            raise StopAsyncIteration
        return self._mark(item)

    def _mark(self, event: Event) -> Event:
        self.last_delivered_id = event.id
        return event

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> Event:
        event = await self.next()
        assert event is not None  # tanpa timeout, next() tidak pernah None
        return event

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()

    async def aclose(self) -> None:
        self.close()

    # -- siklus hidup -----------------------------------------------------

    def close(self) -> None:
        """Lepas subscription dari bus. Idempoten; aman dari thread mana pun."""
        self._bus._unregister(self)
        if _running_loop() is self._loop:
            self._shutdown(overflow=False)
        elif not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._shutdown, False)
            except RuntimeError:  # loop ditutup di antara pengecekan
                self._closed = True

    def _shutdown(self, overflow: bool) -> None:
        """Tutup di thread loop: kosongkan queue lalu kirim sentinel."""
        if self._closed:
            return
        self._closed = True
        if overflow:
            self.overflowed = True
            self._replay.clear()
            while not self._queue.empty():
                self._queue.get_nowait()
        elif self._queue.full():
            # Close normal: pertahankan event yang sudah antre sebisa mungkin,
            # tapi sentinel harus masuk untuk membangunkan next() yang menunggu.
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSE)

    def _offer(self, event: Event) -> None:
        """Kirim event ke queue (dijalankan di thread loop subscriber)."""
        if self._closed or event.seq <= self._last_enqueued:
            return  # sudah ditutup, atau duplikat dari snapshot replay
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._bus._unregister(self)
            self._shutdown(overflow=True)
            return
        self._last_enqueued = event.seq


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class EventBus:
    """Pub/sub in-process per Workspace dengan replay ``Last-Event-ID``."""

    def __init__(
        self,
        *,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        event_types: frozenset[str] | None = WORKSPACE_EVENT_TYPES,
    ) -> None:
        if buffer_size < 1 or queue_size < 1:
            raise ValueError("buffer_size and queue_size must be >= 1")
        self._buffer_size = buffer_size
        self._queue_size = queue_size
        self._event_types = event_types  # None = tipe bebas
        self._epoch = secrets.token_hex(4)
        self._lock = threading.RLock()
        self._channels: dict[str, _Channel] = {}

    @property
    def epoch(self) -> str:
        return self._epoch

    def _channel(self, workspace_id: str) -> _Channel:
        ch = self._channels.get(workspace_id)
        if ch is None:
            ch = _Channel(buffer=deque(maxlen=self._buffer_size))
            self._channels[workspace_id] = ch
        return ch

    # -- publish ----------------------------------------------------------

    def publish(self, workspace_id: str, event_type: str, data: Any = None) -> Event:
        """Terbitkan event ke semua subscriber Workspace; tidak pernah blocking.

        Aman dipanggil dari coroutine maupun dari thread worker. Mengembalikan
        :class:`Event` yang sudah diberi sequence id.
        """
        if self._event_types is not None and event_type not in self._event_types:
            raise ValueError(f"Unknown workspace event type: {event_type!r}")
        data_json = to_json(data)  # di luar lock; error muncul di publisher
        current = _running_loop()
        with self._lock:
            ch = self._channel(workspace_id)
            ch.seq += 1
            event = Event(
                seq=ch.seq,
                id=f"{self._epoch}-{ch.seq}",
                workspace_id=workspace_id,
                type=event_type,
                data_json=data_json,
                created_at=_dt.datetime.now(_dt.UTC),
            )
            if len(ch.buffer) == ch.buffer.maxlen:
                ch.evicted_seq = ch.buffer[0].seq
            ch.buffer.append(event)
            dead: list[Subscription] = []
            for sub in tuple(ch.subscribers):
                loop = sub._loop
                if loop is current:
                    sub._offer(event)
                    continue
                try:
                    loop.call_soon_threadsafe(sub._offer, event)
                except RuntimeError:  # loop subscriber sudah ditutup
                    dead.append(sub)
            for sub in dead:
                ch.subscribers.discard(sub)
                sub._closed = True
        return event

    # Alias eksplisit untuk pemanggil sinkron di thread worker.
    publish_threadsafe = publish

    # -- subscribe --------------------------------------------------------

    def _parse_cursor(self, last_event_id: str | int | None) -> tuple[int | None, bool]:
        """→ (seq kursor atau None, kursor dikenali?)."""
        if last_event_id is None:
            return None, True
        if isinstance(last_event_id, int):
            return (last_event_id, True) if last_event_id >= 0 else (None, False)
        raw = last_event_id.strip()
        if not raw:
            return None, True
        epoch, sep, seq_s = raw.rpartition("-")
        if not sep:
            epoch, seq_s = self._epoch, raw
        if epoch != self._epoch or not seq_s.isdigit():
            return None, False
        return int(seq_s), True

    def subscribe(
        self, workspace_id: str, last_event_id: str | int | None = None
    ) -> Subscription:
        """Berlangganan event Workspace.

        - ``last_event_id=None`` (koneksi baru): hanya event setelah saat ini.
        - ``last_event_id`` valid: replay event di buffer dengan sequence lebih
          besar, lalu event live — tanpa celah dan tanpa duplikat.
        - ``last_event_id`` tidak dikenali (epoch lain / setelah restart,
          format rusak, atau di depan sequence saat ini): replay seluruh buffer
          dengan ``replay_complete=False``.

        Harus dipanggil dari dalam event loop yang berjalan.
        """
        loop = asyncio.get_running_loop()
        cursor, known = self._parse_cursor(last_event_id)
        with self._lock:
            ch = self._channel(workspace_id)
            if known and cursor is not None and cursor > ch.seq:
                known = False
            if not known:
                replay = list(ch.buffer)
                complete = False
            elif cursor is None:
                replay, complete = [], True
            else:
                replay = [e for e in ch.buffer if e.seq > cursor]
                complete = cursor >= ch.evicted_seq
            sub = Subscription(
                self,
                workspace_id,
                loop,
                self._queue_size,
                replay,
                last_enqueued=ch.seq,
                replay_complete=complete,
            )
            ch.subscribers.add(sub)
        return sub

    def _unregister(self, sub: Subscription) -> None:
        with self._lock:
            ch = self._channels.get(sub.workspace_id)
            if ch is not None:
                ch.subscribers.discard(sub)

    # -- utilitas ---------------------------------------------------------

    def last_event_id(self, workspace_id: str) -> str | None:
        with self._lock:
            ch = self._channels.get(workspace_id)
            return f"{self._epoch}-{ch.seq}" if ch and ch.seq else None

    def subscriber_count(self, workspace_id: str) -> int:
        with self._lock:
            ch = self._channels.get(workspace_id)
            return len(ch.subscribers) if ch else 0

    def close_workspace(self, workspace_id: str) -> None:
        """Tutup semua subscriber dan buang buffer Workspace (mis. saat dihapus)."""
        with self._lock:
            ch = self._channels.pop(workspace_id, None)
        if ch is None:
            return
        for sub in tuple(ch.subscribers):
            sub.close()


_default_bus: EventBus | None = None
_default_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Instance bus bersama untuk proses (dibuat saat pertama dipakai)."""
    global _default_bus
    with _default_lock:
        if _default_bus is None:
            _default_bus = EventBus()
        return _default_bus
