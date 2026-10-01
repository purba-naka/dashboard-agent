"""Unit test event bus workspace dan formatter SSE (Req 16.3, 16.5)."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import re

import pytest

from studio.events.bus import Event, EventBus, get_event_bus
from studio.events.sse import (
    CHAT_EVENT_TYPES,
    SSE_HEADERS,
    format_comment,
    format_event,
    format_retry,
    format_sse,
    heartbeat,
    stream_subscription,
    to_json,
)

WS = "ws-1"
PATCH = "patch.applied"


async def drain(sub, n: int, timeout: float = 0.5) -> list[Event]:
    """Ambil tepat ``n`` event dari subscription (gagal bila timeout)."""
    out: list[Event] = []
    for _ in range(n):
        ev = await sub.next(timeout=timeout)
        assert ev is not None, f"timeout setelah {len(out)} event"
        out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Sequence & id
# ---------------------------------------------------------------------------


def test_sequence_monotonic_and_id_format() -> None:
    bus = EventBus()
    events = [bus.publish(WS, PATCH, {"i": i}) for i in range(5)]

    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    for e in events:
        assert re.fullmatch(rf"{bus.epoch}-{e.seq}", e.id)
        assert e.workspace_id == WS and e.type == PATCH
        assert e.created_at.tzinfo is not None
    assert events[2].data == {"i": 2}
    assert bus.last_event_id(WS) == events[-1].id


def test_sequence_is_per_workspace() -> None:
    bus = EventBus()
    a1 = bus.publish("a", PATCH)
    b1 = bus.publish("b", PATCH)
    a2 = bus.publish("a", PATCH)
    assert (a1.seq, b1.seq, a2.seq) == (1, 1, 2)
    assert bus.last_event_id("unknown") is None


def test_unknown_event_type_rejected() -> None:
    bus = EventBus()
    with pytest.raises(ValueError, match="Unknown workspace event type"):
        bus.publish(WS, "not.a.type", {})
    # Tidak ada sequence yang terpakai oleh publish yang gagal.
    assert bus.last_event_id(WS) is None
    assert bus.publish(WS, PATCH).seq == 1


def test_free_event_types_when_disabled() -> None:
    bus = EventBus(event_types=None)
    assert bus.publish(WS, "anything.goes").type == "anything.goes"


def test_invalid_sizes_rejected() -> None:
    with pytest.raises(ValueError):
        EventBus(buffer_size=0)
    with pytest.raises(ValueError):
        EventBus(queue_size=0)


def test_unserializable_payload_raises_at_publisher() -> None:
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.publish(WS, PATCH, {"x": float("nan")})
    assert bus.last_event_id(WS) is None


def test_get_event_bus_is_singleton() -> None:
    assert get_event_bus() is get_event_bus()


# ---------------------------------------------------------------------------
# Subscribe, live delivery & replay Last-Event-ID
# ---------------------------------------------------------------------------


async def test_live_delivery_in_order() -> None:
    bus = EventBus()
    bus.publish(WS, PATCH, {"before": True})  # sebelum subscribe → tidak dikirim
    async with bus.subscribe(WS) as sub:
        assert sub.replay_complete is True
        for i in range(10):
            bus.publish(WS, PATCH, {"i": i})
        got = await drain(sub, 10)
        assert [e.data["i"] for e in got] == list(range(10))
        assert [e.seq for e in got] == list(range(2, 12))
        assert sub.last_delivered_id == got[-1].id
        assert await sub.next(timeout=0.01) is None
    assert bus.subscriber_count(WS) == 0


async def test_workspace_isolation() -> None:
    bus = EventBus()
    async with bus.subscribe("a") as sub:
        bus.publish("b", PATCH, {"ws": "b"})
        bus.publish("a", PATCH, {"ws": "a"})
        (ev,) = await drain(sub, 1)
        assert ev.data == {"ws": "a"}
        assert await sub.next(timeout=0.01) is None


async def test_replay_since_last_event_id_then_live_without_gap_or_dup() -> None:
    bus = EventBus()
    published = [bus.publish(WS, PATCH, {"i": i}) for i in range(5)]
    async with bus.subscribe(WS, published[1].id) as sub:
        assert sub.replay_complete is True
        bus.publish(WS, PATCH, {"i": 5})
        got = await drain(sub, 4)
        assert [e.seq for e in got] == [3, 4, 5, 6]
        assert await sub.next(timeout=0.01) is None


async def test_replay_accepts_int_and_bare_seq_cursor() -> None:
    bus = EventBus()
    for i in range(3):
        bus.publish(WS, PATCH, {"i": i})
    for cursor in (1, "1"):
        async with bus.subscribe(WS, cursor) as sub:
            assert [e.seq for e in await drain(sub, 2)] == [2, 3]


async def test_replay_from_latest_id_yields_nothing() -> None:
    bus = EventBus()
    last = [bus.publish(WS, PATCH) for _ in range(3)][-1]
    async with bus.subscribe(WS, last.id) as sub:
        assert sub.replay_complete is True
        assert await sub.next(timeout=0.01) is None


async def test_replay_incomplete_when_events_evicted() -> None:
    bus = EventBus(buffer_size=3)
    ids = [bus.publish(WS, PATCH, {"i": i}).id for i in range(5)]  # buffer: 3,4,5

    async with bus.subscribe(WS, ids[0]) as sub:  # butuh seq 2 → sudah tergusur
        assert sub.replay_complete is False
        assert [e.seq for e in await drain(sub, 3)] == [3, 4, 5]

    async with bus.subscribe(WS, ids[1]) as sub:  # seq 3.. masih lengkap
        assert sub.replay_complete is True
        assert [e.seq for e in await drain(sub, 3)] == [3, 4, 5]


@pytest.mark.parametrize("cursor", ["deadbeef-2", "garbage-x", "-5", 99, -1])
async def test_unknown_cursor_replays_whole_buffer_as_incomplete(cursor) -> None:
    bus = EventBus()
    for _ in range(3):
        bus.publish(WS, PATCH)
    async with bus.subscribe(WS, cursor) as sub:
        assert sub.replay_complete is False
        assert [e.seq for e in await drain(sub, 3)] == [1, 2, 3]


async def test_empty_cursor_is_new_connection() -> None:
    bus = EventBus()
    bus.publish(WS, PATCH)
    async with bus.subscribe(WS, "  ") as sub:
        assert sub.replay_complete is True
        assert await sub.next(timeout=0.01) is None


# ---------------------------------------------------------------------------
# Thread-safety
# ---------------------------------------------------------------------------


async def test_publish_from_worker_thread_preserves_order() -> None:
    bus = EventBus(queue_size=1000)
    n = 200

    def worker() -> None:
        for i in range(n):
            bus.publish(WS, "job.progress", {"i": i})

    async with bus.subscribe(WS) as sub:
        await asyncio.to_thread(worker)
        got = await drain(sub, n)
        assert [e.data["i"] for e in got] == list(range(n))
        assert [e.seq for e in got] == list(range(1, n + 1))


async def test_concurrent_threads_get_unique_increasing_seq() -> None:
    bus = EventBus(queue_size=1000)

    def worker(tag: int) -> None:
        for i in range(50):
            bus.publish(WS, "job.progress", {"t": tag, "i": i})

    async with bus.subscribe(WS) as sub:
        await asyncio.gather(*(asyncio.to_thread(worker, t) for t in range(4)))
        got = await drain(sub, 200)
        seqs = [e.seq for e in got]
        assert seqs == sorted(seqs) == list(range(1, 201))
        # Urutan per publisher (thread) juga terjaga.
        for t in range(4):
            assert [e.data["i"] for e in got if e.data["t"] == t] == list(range(50))


# ---------------------------------------------------------------------------
# Subscriber lambat (overflow) & penutupan
# ---------------------------------------------------------------------------


async def test_overflow_drops_slow_subscriber_and_reconnect_replays() -> None:
    bus = EventBus(queue_size=2)
    sub = bus.subscribe(WS)
    fast = bus.subscribe(WS)
    for i in range(3):  # event ke-3 memenuhi queue sub → di-drop
        bus.publish(WS, PATCH, {"i": i})
    # `fast` juga queue_size=2 → ikut overflow; publisher tidak pernah blok.
    assert sub.overflowed is True and sub.closed is True
    with pytest.raises(StopAsyncIteration):
        await sub.next(timeout=0.1)
    assert bus.subscriber_count(WS) == 0
    fast.close()

    # Reconnect dengan Last-Event-ID terakhir yang diterima (tidak ada → seq 0).
    async with bus.subscribe(WS, f"{bus.epoch}-0") as again:
        assert again.replay_complete is True
        assert [e.data["i"] for e in await drain(again, 3)] == [0, 1, 2]


async def test_publish_after_overflow_does_not_reach_dropped_subscriber() -> None:
    bus = EventBus(queue_size=1)
    sub = bus.subscribe(WS)
    bus.publish(WS, PATCH)
    bus.publish(WS, PATCH)  # overflow
    bus.publish(WS, PATCH)
    assert sub.overflowed
    with pytest.raises(StopAsyncIteration):
        await sub.next(timeout=0.1)


async def test_close_is_idempotent_and_ends_iteration() -> None:
    bus = EventBus()
    sub = bus.subscribe(WS)
    sub.close()
    sub.close()
    assert sub.closed and not sub.overflowed
    with pytest.raises(StopAsyncIteration):
        await sub.next(timeout=0.1)
    assert [e async for e in sub] == []


async def test_close_wakes_waiting_consumer() -> None:
    bus = EventBus()
    sub = bus.subscribe(WS)

    async def consume() -> list[Event]:
        return [e async for e in sub]

    task = asyncio.create_task(consume())
    await asyncio.sleep(0)
    bus.publish(WS, PATCH, {"x": 1})
    await asyncio.sleep(0.01)
    bus.close_workspace(WS)
    got = await asyncio.wait_for(task, 0.5)
    assert [e.data for e in got] == [{"x": 1}]
    assert bus.subscriber_count(WS) == 0
    # Buffer ikut dibuang → sequence mulai ulang.
    assert bus.last_event_id(WS) is None


# ---------------------------------------------------------------------------
# Formatter SSE
# ---------------------------------------------------------------------------


def test_format_sse_event_and_data() -> None:
    assert format_sse(PATCH, {"a": 1}) == 'event: patch.applied\ndata: {"a":1}\n\n'


def test_format_sse_field_order_with_id_and_retry() -> None:
    frame = format_sse("job.done", None, id="e-7", retry_ms=3000)
    assert frame == "id: e-7\nevent: job.done\nretry: 3000\ndata: null\n\n"


def test_format_sse_multiline_payload_split_into_data_lines() -> None:
    frame = format_sse("x", '{\n"a": 1\n}', data_is_json=True)
    assert frame == 'event: x\ndata: {\ndata: "a": 1\ndata: }\n\n'


def test_format_sse_rejects_line_breaks_in_fields() -> None:
    with pytest.raises(ValueError):
        format_sse("bad\nevent", {})
    with pytest.raises(ValueError):
        format_sse("ok", {}, id="1\r2")


def test_format_sse_json_escapes_newlines_in_strings() -> None:
    frame = format_sse("text.delta", {"t": "a\nb", "u": "é"})
    assert frame == 'event: text.delta\ndata: {"t":"a\\nb","u":"é"}\n\n'
    assert json.loads(frame.split("data: ", 1)[1]) == {"t": "a\nb", "u": "é"}


def test_to_json_handles_common_types() -> None:
    dt = _dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=_dt.UTC)
    assert json.loads(to_json({"d": dt, "s": {1}, "t": (1, 2)})) == {
        "d": "2024-01-02T03:04:05+00:00",
        "s": [1],
        "t": [1, 2],
    }
    with pytest.raises(TypeError):
        to_json(object())


def test_format_event_includes_id() -> None:
    bus = EventBus()
    ev = bus.publish(WS, PATCH, {"version": 3})
    assert format_event(ev) == f'id: {ev.id}\nevent: patch.applied\ndata: {{"version":3}}\n\n'


def test_comment_heartbeat_retry_frames() -> None:
    assert format_comment("hi") == ": hi\n\n"
    assert format_comment() == ":\n\n"
    assert heartbeat() == ": ping\n\n"
    assert format_retry(1500) == "retry: 1500\n\n"


def test_constants() -> None:
    assert {"run.started", "text.delta", "run.done", "error"} <= CHAT_EVENT_TYPES
    assert SSE_HEADERS["Cache-Control"] == "no-cache"


# ---------------------------------------------------------------------------
# stream_subscription
# ---------------------------------------------------------------------------


async def test_stream_subscription_heartbeat_events_and_close() -> None:
    bus = EventBus()
    sub = bus.subscribe(WS)
    gen = stream_subscription(sub, heartbeat_seconds=0.02)

    assert await anext(gen) == ": connected\n\n"
    assert await asyncio.wait_for(anext(gen), 0.5) == heartbeat()

    ev = bus.publish(WS, "job.done", {"ok": True})
    assert await asyncio.wait_for(anext(gen), 0.5) == format_event(ev)

    sub.close()
    # Setelah close, generator berakhir (mungkin didahului satu heartbeat).
    rest = [frame async for frame in gen]
    assert all(f == heartbeat() for f in rest)
    assert bus.subscriber_count(WS) == 0


async def test_stream_subscription_replays_first_and_sends_retry() -> None:
    bus = EventBus()
    first = bus.publish(WS, PATCH, {"v": 1})
    second = bus.publish(WS, PATCH, {"v": 2})
    gen = stream_subscription(bus.subscribe(WS, first.id), heartbeat_seconds=0.02, retry_ms=2000)

    assert await anext(gen) == "retry: 2000\n\n"
    assert await anext(gen) == format_event(second)
    await gen.aclose()
    assert bus.subscriber_count(WS) == 0


async def test_stream_subscription_ends_on_overflow() -> None:
    bus = EventBus(queue_size=1)
    gen = stream_subscription(bus.subscribe(WS), heartbeat_seconds=None)
    assert await anext(gen) == ": connected\n\n"
    bus.publish(WS, PATCH)
    bus.publish(WS, PATCH)  # overflow → subscription di-drop
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(gen), 0.5)


def test_format_sse_unicode_line_separators_stay_in_single_data_line() -> None:
    # `str.splitlines()` juga memecah U+2028/U+2029/U+0085/\x0b/\x0c/\x1c-\x1e;
    # SSE hanya mengenal CRLF/LF/CR sehingga payload harus tetap satu baris `data:`.
    value = "a\u2028b\u2029c\u0085d\x0be\x0cf\x1cg\x1dh\x1ei"
    frame = format_sse(PATCH, {"t": value})
    assert frame.endswith("\n\n")
    data_lines = [ln for ln in frame[:-2].split("\n") if ln.startswith("data:")]
    assert len(data_lines) == 1
    assert json.loads(data_lines[0][len("data: ") :]) == {"t": value}
