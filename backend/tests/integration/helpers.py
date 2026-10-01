"""Helper integration test REST API: app berjalan, upload sampel, dan klien SSE streaming.

``httpx.ASGITransport`` menunggu aplikasi ASGI selesai sebelum mengembalikan
respons (body di-buffer penuh), sehingga tidak dapat dipakai untuk stream SSE
yang tidak pernah berakhir. :class:`StreamingASGITransport` menjalankan app di
task terpisah dan mengalirkan chunk body lewat queue; menutup respons mengirim
``http.disconnect`` ke app lalu membatalkan task-nya.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
from fastapi import FastAPI

SAMPLES = Path(__file__).resolve().parent.parent / "eval" / "samples"
EXPECTED_FACTS: dict[str, Any] = json.loads(
    (SAMPLES / "expected_facts.json").read_text(encoding="utf-8")
)
SQL: dict[str, str] = EXPECTED_FACTS["sql"]

JOB_TIMEOUT_S = 60.0

T = TypeVar("T")


# ---------------------------------------------------------------------------
# App berjalan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Jalankan lifespan app lalu yield klien httpx yang terhubung via ASGI."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def err(resp: httpx.Response) -> dict[str, Any]:
    """Isi ``error`` envelope respons gagal."""
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


async def wait_until(
    probe: Callable[[], Awaitable[T]],
    predicate: Callable[[T], bool],
    *,
    timeout: float = JOB_TIMEOUT_S,
    interval: float = 0.05,
) -> T:
    """Panggil ``probe`` berulang sampai ``predicate(hasil)`` benar (gagal bila timeout)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = await probe()
        if predicate(value):
            return value
        assert loop.time() < deadline, f"timeout menunggu kondisi; nilai terakhir: {value!r}"
        await asyncio.sleep(interval)


@dataclass
class Studio:
    """App + klien + helper alur REST yang sering dipakai."""

    app: FastAPI
    client: httpx.AsyncClient
    #: Nilai progres yang teramati per job saat polling ``GET /jobs/{id}``.
    progress_seen: dict[str, list[float]] = field(default_factory=dict)

    # -- workspace ----------------------------------------------------------

    async def create_workspace(self, name: str = "Sales") -> str:
        resp = await self.client.post("/api/workspaces", json={"name": name})
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    # -- upload & job ---------------------------------------------------------

    async def upload(
        self, ws: str, filename: str, content: bytes, content_type: str = "text/csv"
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/workspaces/{ws}/uploads",
            files={"file": (filename, content, content_type)},
        )

    async def wait_job(self, job_id: str) -> dict[str, Any]:
        """Poll ``GET /jobs/{id}`` sampai done/failed, lalu tunggu hook profiling selesai."""
        seen = self.progress_seen.setdefault(job_id, [])

        async def probe() -> dict[str, Any]:
            resp = await self.client.get(f"/api/jobs/{job_id}")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            seen.append(body["progress"])
            return body

        body = await wait_until(probe, lambda b: b["status"] in ("done", "failed"))
        # Job ditandai done sebelum profiling + deteksi relasi (hook) selesai;
        # tunggu task job agar profil & Relation_Candidate sudah tersimpan.
        await self.app.state.ingestion.wait_job(job_id, JOB_TIMEOUT_S)
        return body

    async def upload_csv(self, ws: str, filename: str, content: bytes) -> str:
        """Upload CSV sampai Dataset terdaftar dan terprofil; kembalikan ``dataset_id``."""
        resp = await self.upload(ws, filename, content)
        assert resp.status_code == 202, resp.text
        job = await self.wait_job(resp.json()["job_id"])
        assert job["status"] == "done", job
        return job["dataset_id"]

    async def upload_sample(self, ws: str, name: str) -> str:
        return await self.upload_csv(ws, name, (SAMPLES / name).read_bytes())

    async def upload_xlsx_sheets(self, ws: str, filename: str, content: bytes) -> dict[str, Any]:
        resp = await self.upload(
            ws,
            filename,
            content,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def upload_products(self, ws: str) -> str:
        """``products.xlsx`` (satu sheet ``Products``) → ``dataset_id``."""
        body = await self.upload_xlsx_sheets(
            ws, "products.xlsx", (SAMPLES / "products.xlsx").read_bytes()
        )
        resp = await self.client.post(
            f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets",
            json={"sheets": [body["sheets"][0]["name"]]},
        )
        assert resp.status_code == 202, resp.text
        job = await self.wait_job(resp.json()["jobs"][0]["job_id"])
        assert job["status"] == "done", job
        return job["dataset_id"]

    async def sales_workspace(self, *, products: bool = True) -> dict[str, str]:
        """Workspace berisi customers, transactions (dan products) dari sampel eval."""
        ws = await self.create_workspace()
        ids = {"ws": ws}
        ids["customers"] = await self.upload_sample(ws, "customers.csv")
        ids["transactions"] = await self.upload_sample(ws, "transactions.csv")
        if products:
            ids["products"] = await self.upload_products(ws)
        return ids

    # -- relasi ---------------------------------------------------------------

    async def relations(self, ws: str, status: str | None = None) -> list[dict[str, Any]]:
        params = {"status": status} if status else None
        resp = await self.client.get(f"/api/workspaces/{ws}/relations", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def relation_between(
        self, ws: str, table_a: str, column: str, table_b: str, status: str | None = None
    ) -> dict[str, Any]:
        """Relasi ``table_a.column`` ↔ ``table_b.column`` (arah bebas)."""
        want = {(table_a, column), (table_b, column)}
        for rel in await self.relations(ws, status):
            ends = {(rel["from_table"], rel["from_column"]), (rel["to_table"], rel["to_column"])}
            if ends == want:
                return rel
        raise AssertionError(f"relasi {table_a}.{column} ↔ {table_b}.{column} tidak ditemukan")

    async def confirm(self, ws: str, relation_id: str) -> dict[str, Any]:
        resp = await self.client.post(f"/api/workspaces/{ws}/relations/{relation_id}/confirm")
        assert resp.status_code == 200, resp.text
        return resp.json()

    # -- query & dashboard ------------------------------------------------------

    async def execute(self, ws: str, sql: str) -> Any:
        """Eksekusi & simpan query langsung via Data_Engine (``QueryResult``)."""
        return await self.app.state.engine.execute(ws, sql, created_by="user")

    async def create_dashboard(self, ws: str, title: str = "Overview") -> dict[str, Any]:
        resp = await self.client.post(f"/api/workspaces/{ws}/dashboards", json={"title": title})
        assert resp.status_code == 201, resp.text
        return resp.json()

    async def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        resp = await self.client.get(f"/api/dashboards/{dashboard_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def apply(
        self, dashboard_id: str, command: dict[str, Any], base_version: int
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/dashboards/{dashboard_id}/patches",
            json={"base_version": base_version, "command": command},
        )

    async def apply_ok(
        self, dashboard_id: str, command: dict[str, Any], base_version: int
    ) -> dict[str, Any]:
        resp = await self.apply(dashboard_id, command, base_version)
        assert resp.status_code == 200, resp.text
        return resp.json()

    async def add_chart(
        self, dashboard_id: str, base_version: int, title: str, spec: dict[str, Any]
    ) -> tuple[str, int]:
        """``add_chart`` → ``(item_id, versi baru)``."""
        event = await self.apply_ok(
            dashboard_id, {"type": "add_chart", "title": title, "spec": spec}, base_version
        )
        (op,) = event["ops"]
        assert op["op"] == "add_item"
        return op["item"]["id"], event["version"]

    async def add_insight(
        self, dashboard_id: str, base_version: int, draft: dict[str, Any]
    ) -> tuple[str, int]:
        event = await self.apply_ok(
            dashboard_id, {"type": "add_insight", "insight": draft}, base_version
        )
        (op,) = event["ops"]
        return op["item"]["id"], event["version"]

    async def render(self, dashboard_id: str, **body: Any) -> dict[str, Any]:
        resp = await self.client.post(f"/api/dashboards/{dashboard_id}/render", json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


# ---------------------------------------------------------------------------
# Builder Chart_Spec / insight
# ---------------------------------------------------------------------------


def bar_spec(query_id: str, x: str, y: str, *, cross_filter_column: str | None = None) -> dict:
    spec: dict[str, Any] = {
        "query_id": query_id,
        "chart_type": "bar",
        "option": {
            "xAxis": {"type": "category"},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "encode": {"x": x, "y": y}}],
        },
    }
    if cross_filter_column is not None:
        spec["cross_filter_column"] = cross_filter_column
    return spec


def pie_spec(query_id: str, name: str, value: str) -> dict:
    return {
        "query_id": query_id,
        "chart_type": "pie",
        "option": {"series": [{"type": "pie", "encode": {"itemName": name, "value": value}}]},
    }


def insight_draft(
    result: Any,
    sql: str,
    text: str,
    dataset_ids: list[str],
    dataset_versions: dict[str, int],
    *,
    title: str = "Total pendapatan",
    insight_type: str = "comparison",
) -> dict[str, Any]:
    """Draft ``add_insight`` dari ``QueryResult`` (evidence = hasil query)."""
    return {
        "insight_type": insight_type,
        "title": title,
        "text": text,
        "query_id": result.query_id,
        "sql": sql,
        "evidence": {
            "columns": [c.model_dump(mode="json") for c in result.columns],
            "rows": result.rows,
            "row_count": result.row_count,
        },
        "filters_snapshot": [],
        "dataset_ids": dataset_ids,
        "computed_at": datetime.now(UTC).isoformat(),
        "dataset_versions": dataset_versions,
    }


def dataset_rows(option: dict[str, Any]) -> list[dict[str, Any]]:
    """``option.dataset`` hasil render → daftar dict per baris."""
    dims = option["dataset"]["dimensions"]
    return [dict(zip(dims, row)) for row in option["dataset"]["source"]]


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


class _QueueStream(httpx.AsyncByteStream):
    def __init__(
        self, queue: asyncio.Queue[bytes | None], task: asyncio.Task[None], disconnected: asyncio.Event
    ) -> None:
        self._queue = queue
        self._task = task
        self._disconnected = disconnected

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk

    async def aclose(self) -> None:
        self._disconnected.set()
        if not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 2.0)
            except (TimeoutError, asyncio.TimeoutError):
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            except Exception:  # noqa: BLE001 - error app setelah disconnect diabaikan
                pass


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """Transport ASGI yang mengalirkan body respons (untuk ``text/event-stream``)."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 123),
            "root_path": "",
        }
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        started = asyncio.Event()
        disconnected = asyncio.Event()
        request_sent = False
        head: dict[str, Any] = {}

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                head["status"] = message["status"]
                head["headers"] = message.get("headers", [])
                started.set()
            elif message["type"] == "http.response.body":
                if disconnected.is_set():
                    raise OSError("client disconnected")
                chunk = message.get("body", b"")
                if chunk:
                    queue.put_nowait(chunk)
                if not message.get("more_body", False):
                    queue.put_nowait(None)

        async def run_app() -> None:
            try:
                await self.app(scope, receive, send)
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run_app())
        waiter = asyncio.create_task(started.wait())
        await asyncio.wait({task, waiter}, timeout=10.0, return_when=asyncio.FIRST_COMPLETED)
        if not started.is_set():
            waiter.cancel()
            if task.done():
                task.result()  # lempar error app
            task.cancel()
            raise AssertionError("app tidak memulai respons dalam 10 detik")
        return httpx.Response(
            head["status"],
            headers=head["headers"],
            stream=_QueueStream(queue, task, disconnected),
            request=request,
        )


@dataclass
class SseFrame:
    event: str | None
    data: Any
    id: str | None


class SseReader:
    """Parser frame SSE dari respons streaming httpx."""

    def __init__(self, response: httpx.Response) -> None:
        self._chunks = response.aiter_text()
        self._buffer = ""

    async def next_frame(self, timeout: float = 10.0) -> SseFrame | None:
        """Frame berikutnya (komentar/heartbeat dilewati); ``None`` bila stream berakhir."""
        async with asyncio.timeout(timeout):
            while True:
                while "\n\n" in self._buffer:
                    raw, self._buffer = self._buffer.split("\n\n", 1)
                    frame = self._parse(raw)
                    if frame is not None:
                        return frame
                try:
                    self._buffer += await anext(self._chunks)
                except StopAsyncIteration:
                    return None

    @staticmethod
    def _parse(raw: str) -> SseFrame | None:
        event: str | None = None
        frame_id: str | None = None
        data: list[str] = []
        for line in raw.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "id":
                frame_id = value
            elif name == "data":
                data.append(value)
        if event is None and not data:
            return None  # hanya komentar / retry
        return SseFrame(event=event, data=json.loads("\n".join(data)) if data else None, id=frame_id)

    async def until(
        self, predicate: Callable[[SseFrame], bool], timeout: float = 30.0
    ) -> list[SseFrame]:
        """Baca frame sampai ``predicate`` benar; kembalikan semua frame yang dibaca."""
        frames: list[SseFrame] = []
        async with asyncio.timeout(timeout):
            while True:
                frame = await self.next_frame(timeout)
                assert frame is not None, f"stream berakhir; frame: {frames}"
                frames.append(frame)
                if predicate(frame):
                    return frames


@asynccontextmanager
async def sse_stream(
    app: FastAPI,
    ws: str,
    headers: dict[str, str] | None = None,
    *,
    params: dict[str, str] | None = None,
) -> AsyncIterator[tuple[httpx.Response, SseReader]]:
    """Buka ``GET /api/workspaces/{ws}/events`` sebagai stream (ditutup saat keluar)."""
    transport = StreamingASGITransport(app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "GET", f"/api/workspaces/{ws}/events", headers=headers or {}, params=params
        ) as resp:
            yield resp, SseReader(resp)
