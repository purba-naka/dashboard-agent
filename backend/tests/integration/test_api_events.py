"""Integration test stream SSE ``GET /api/workspaces/{ws}/events`` (task 16.8).

Stream dibaca lewat :class:`~tests.integration.helpers.StreamingASGITransport`
(``httpx.ASGITransport`` mem-buffer body sampai app selesai). Setiap pembacaan
dibatasi timeout sehingga test tidak pernah menggantung.

_Requirements: 5.3, 16.5, 18.3_
"""

from __future__ import annotations

from tests.integration.helpers import SAMPLES, SseFrame, Studio, sse_stream

WORKSPACE_EVENTS = {
    "patch.applied",
    "job.progress",
    "job.done",
    "job.failed",
    "dataset.profiled",
    "relation.updated",
}


def _seq(frame: SseFrame) -> int:
    assert frame.id is not None, frame
    return int(frame.id.rsplit("-", 1)[1])


async def test_sse_stream_delivers_events_and_replays_after_last_event_id(
    studio: Studio,
) -> None:
    ws = await studio.create_workspace()

    async with sse_stream(studio.app, ws) as (resp, reader):
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-cache"

        upload = await studio.upload(ws, "customers.csv", (SAMPLES / "customers.csv").read_bytes())
        assert upload.status_code == 202, upload.text
        job_id = upload.json()["job_id"]
        job_frames = await reader.until(
            lambda f: f.event == "job.done" and f.data["job_id"] == job_id
        )
        dataset_id = job_frames[-1].data["dataset_id"]
        # Profiling berjalan setelah job.done.
        job_frames += await reader.until(
            lambda f: f.event == "dataset.profiled" and f.data["dataset_id"] == dataset_id
        )

        dash = await studio.create_dashboard(ws)
        event = await studio.apply_ok(dash["id"], {"type": "set_title", "title": "Baru"}, 0)
        patch_frames = await reader.until(lambda f: f.event == "patch.applied")

    frames = job_frames + patch_frames
    assert {f.event for f in frames} <= WORKSPACE_EVENTS
    assert all(f.id for f in frames)
    seqs = [_seq(f) for f in frames]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    progress = [f.data["progress"] for f in frames if f.event == "job.progress"]
    assert progress and progress == sorted(progress) and progress[-1] == 100.0
    done = next(f for f in frames if f.event == "job.done")
    patch = patch_frames[-1]
    assert patch.data["id"] == event["id"] and patch.data["version"] == 1
    assert patch.data["dashboard_id"] == dash["id"] and patch.data["source"] == "user"

    # --- reconnect dengan Last-Event-ID: replay event setelah kursor ----------
    async with sse_stream(studio.app, ws, {"Last-Event-ID": done.id}) as (_, reader):
        replay = await reader.until(lambda f: f.event == "patch.applied")
    assert all(f.event != "resync" for f in replay)
    after_done = [f for f in frames if _seq(f) > _seq(done)]
    assert [(f.id, f.event) for f in replay] == [(f.id, f.event) for f in after_done]
    assert replay[-1].data == patch.data


async def test_sse_query_cursor_and_unknown_cursor_resync(studio: Studio) -> None:
    ws = await studio.create_workspace()
    dash = await studio.create_dashboard(ws)
    first = await studio.apply_ok(dash["id"], {"type": "set_title", "title": "Satu"}, 0)
    second = await studio.apply_ok(dash["id"], {"type": "set_title", "title": "Dua"}, 1)

    # Stream baru tanpa kursor: tidak me-replay event lama; event live tetap diterima.
    async with sse_stream(studio.app, ws) as (_, reader):
        third = await studio.apply_ok(dash["id"], {"type": "set_title", "title": "Tiga"}, 2)
        (frame,) = await reader.until(lambda f: f.event == "patch.applied")
        assert frame.data["id"] == third["id"]
    last_id = frame.id

    # Kursor lewat query ``?last_event_id=`` (EventSource tidak bisa set header).
    epoch = last_id.rsplit("-", 1)[0]
    cursor = {"last_event_id": f"{epoch}-1"}  # setelah patch pertama
    async with sse_stream(studio.app, ws, params=cursor) as (_, reader):
        replay = await reader.until(lambda f: f.data.get("id") == third["id"])
    assert [f.data["id"] for f in replay] == [second["id"], third["id"]]
    assert first["id"] not in {f.data["id"] for f in replay}

    # Kursor tidak dikenal (mis. setelah restart backend) → resync lalu replay buffer.
    async with sse_stream(studio.app, ws, {"Last-Event-ID": "bogus-99"}) as (_, reader):
        resync = await reader.next_frame()
        assert resync is not None and resync.event == "resync" and resync.id is None
        assert resync.data == {"reason": "replay_incomplete", "last_event_id": "bogus-99"}
        replay = await reader.until(lambda f: f.data.get("id") == third["id"])
    assert [f.data["id"] for f in replay] == [first["id"], second["id"], third["id"]]
