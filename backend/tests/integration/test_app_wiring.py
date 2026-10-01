"""Wiring ``create_app``: lifespan mengisi ``app.state`` dan semua router terpasang."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from studio.app import create_app
from studio.config import MIN_MAX_UPLOAD_BYTES, Settings
from studio.data.worker import InlineQueryRunner, QueryWorkerPool
from studio.store.db import DB_FILENAME

CSV = b"id,name,amount\n1,alpha,10.5\n2,beta,20\n3,gamma,30.25\n"


@asynccontextmanager
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _wait_job(client: httpx.AsyncClient, job_id: str, timeout: float = 30.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("done", "failed"):
            return body
        assert asyncio.get_running_loop().time() < deadline, body
        await asyncio.sleep(0.05)


async def test_inline_app_end_to_end(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"  # belum ada: dibuat oleh lifespan
    hook_calls: list[str] = []

    @asynccontextmanager
    async def agent_hook(app: FastAPI) -> AsyncIterator[None]:
        # Service sudah tersedia saat hook dijalankan (titik ekstensi task 18.11).
        assert app.state.dashboard_store is not None
        hook_calls.append("enter")
        yield
        hook_calls.append("exit")

    app = create_app(
        Settings(data_dir=data_dir, query_runner_mode="inline"),
        lifespan_hooks=[agent_hook],
    )

    async with running(app) as client:
        state = app.state
        assert (data_dir / "uploads").is_dir()
        assert (data_dir / DB_FILENAME).is_file()
        assert isinstance(state.query_runner, InlineQueryRunner) and state.query_runner.started
        assert state.workspace_cleanup_hooks == [] and state.insight_rewriter is None
        assert state.ingestion.max_upload_bytes == MIN_MAX_UPLOAD_BYTES
        for name in ("db", "repos", "bus", "engine", "profiler", "dashboard_store"):
            assert getattr(state, name) is not None, name
        assert hook_calls == ["enter"]

        assert (await client.get("/api/health")).json() == {"status": "ok"}

        resp = await client.post("/api/workspaces", json={"name": "Sales"})
        assert resp.status_code == 201, resp.text
        ws = resp.json()["id"]

        resp = await client.post(
            f"/api/workspaces/{ws}/uploads",
            files={"file": ("orders.csv", CSV, "text/csv")},
        )
        assert resp.status_code == 202, resp.text
        job = await _wait_job(client, resp.json()["job_id"])
        assert job["status"] == "done", job

        resp = await client.get(f"/api/workspaces/{ws}/datasets")
        assert resp.status_code == 200, resp.text
        assert [d["id"] for d in resp.json()] == [job["dataset_id"]]

        resp = await client.post(f"/api/workspaces/{ws}/dashboards", json={"title": "Overview"})
        assert resp.status_code == 201, resp.text
        dashboard_id = resp.json()["id"]

        resp = await client.get(f"/api/dashboards/{dashboard_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == dashboard_id

        resp = await client.get("/api/workspaces/does-not-exist/events")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"]

    assert hook_calls == ["enter", "exit"]
    assert not app.state.query_runner.started


async def test_process_mode_starts_and_stops_pool(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", query_workers=1))

    async with running(app) as client:
        pool = app.state.query_runner
        assert isinstance(pool, QueryWorkerPool)
        assert pool.started and len(pool.worker_pids) == 1
        assert (await client.get("/api/health")).status_code == 200

    assert not pool.started and pool.worker_pids == []


def test_settings_runtime_defaults_and_min_upload() -> None:
    s = Settings()
    assert s.query_timeout_s == 60.0 and s.query_workers is None
    assert s.query_runner_mode == "process" and s.sse_heartbeat_seconds == 15.0
    assert s.max_upload_bytes == MIN_MAX_UPLOAD_BYTES
    assert Settings(max_upload_bytes=1024).max_upload_bytes == MIN_MAX_UPLOAD_BYTES
    assert Settings(max_upload_bytes=2 * MIN_MAX_UPLOAD_BYTES).max_upload_bytes == 2 * MIN_MAX_UPLOAD_BYTES
