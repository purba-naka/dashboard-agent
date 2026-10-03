"""Endpoint Workspace (Req 1.1–1.5).

- ``POST /api/workspaces``            → ``201 Workspace``
- ``GET /api/workspaces``             → ``Workspace[]``
- ``GET /api/workspaces/{ws}``        → ``{workspace, datasets, dashboards, chat_sessions}``
- ``PATCH /api/workspaces/{ws}``      → ``Workspace`` (ganti nama)
- ``DELETE /api/workspaces/{ws}``     → ``204``; body ``{confirm_name}`` harus sama
  persis dengan nama Workspace.

Urutan penghapusan (Req 1.4):

1. batalkan job ingestion Workspace yang masih berjalan (agar tidak menulis ulang
   file ke folder yang akan dihapus);
2. jalankan ``app.state.workspace_cleanup_hooks`` (mis. hapus sesi ADK, task 18.11).
   Hook dipanggil *sebelum* metadata dihapus sehingga masih dapat membaca
   ``chat_sessions`` Workspace; kegagalan hook dicatat dan tidak menggagalkan
   penghapusan;
3. ``DELETE FROM workspaces`` (cascade seluruh metadata);
4. tutup kanal SSE Workspace;
5. ``shutil.rmtree(DATA_DIR/uploads/{ws})`` — file upload asli dan Parquet.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status

from studio.api.errors import StudioError
from studio.api.schemas import (
    CreateWorkspaceRequest,
    DeleteWorkspaceRequest,
    RenameWorkspaceRequest,
    WorkspaceDetail,
    WorkspaceOut,
    WorkspaceSummaryOut,
)
from studio.core.identifiers import safe_join
from studio.store.repos import Repositories

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["workspaces"])

#: Hook pembersihan tambahan saat Workspace dihapus: ``async hook(workspace_id)``.
WorkspaceCleanupHook = Callable[[str], Awaitable[Any]]

_JOB_CANCEL_TIMEOUT_S = 10.0
_RMTREE_ATTEMPTS = 5
_RMTREE_DELAY_S = 0.2


# ---------------------------------------------------------------------------
# Dependencies (dibaca dari app.state; di-wire oleh lifespan app)
# ---------------------------------------------------------------------------


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


def _cleanup_hooks(request: Request) -> list[WorkspaceCleanupHook]:
    return list(getattr(request.app.state, "workspace_cleanup_hooks", None) or [])


def _uploads_dir(request: Request, workspace_id: str) -> Path:
    """``DATA_DIR/uploads/{workspace_id}`` (diverifikasi tetap di bawah ``uploads``)."""
    ingestion = getattr(request.app.state, "ingestion", None)
    if ingestion is not None:
        return ingestion.workspace_dir(workspace_id)
    settings = request.app.state.settings
    return safe_join(Path(settings.data_dir) / "uploads", workspace_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/workspaces", status_code=status.HTTP_201_CREATED, response_model=WorkspaceOut)
async def create_workspace(
    body: CreateWorkspaceRequest, repos: Repositories = Depends(_repos)
) -> WorkspaceOut:
    record = await repos.workspaces.create(body.name)
    return WorkspaceOut.from_record(record)


@router.get("/workspaces", response_model=list[WorkspaceSummaryOut])
async def list_workspaces(repos: Repositories = Depends(_repos)) -> list[WorkspaceSummaryOut]:
    return [WorkspaceSummaryOut.from_summary(s) for s in await repos.workspaces.list_summaries()]


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceDetail)
async def get_workspace(
    workspace_id: str, repos: Repositories = Depends(_repos)
) -> WorkspaceDetail:
    workspace = await repos.workspaces.get(workspace_id)
    datasets = await repos.datasets.list_by_workspace(workspace_id)
    dashboards = await repos.dashboards.list_by_workspace(workspace_id)
    sessions = await repos.chat_sessions.list_by_workspace(workspace_id)
    return WorkspaceDetail.from_records(workspace, datasets, dashboards, sessions)


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceOut)
async def rename_workspace(
    workspace_id: str,
    body: RenameWorkspaceRequest,
    repos: Repositories = Depends(_repos),
) -> WorkspaceOut:
    record = await repos.workspaces.rename(workspace_id, body.name)
    return WorkspaceOut.from_record(record)


@router.delete(
    "/workspaces/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_workspace(
    workspace_id: str,
    body: DeleteWorkspaceRequest,
    request: Request,
    repos: Repositories = Depends(_repos),
) -> Response:
    workspace = await repos.workspaces.get(workspace_id)
    if body.confirm_name != workspace.name:
        raise StudioError(
            "VALIDATION_ERROR",
            "Nama konfirmasi tidak cocok dengan nama Workspace.",
            {"field": "confirm_name"},
            http_status=422,
        )

    await _cancel_ingestion_jobs(request, workspace_id)

    for hook in _cleanup_hooks(request):
        try:
            await hook(workspace_id)
        except Exception:  # noqa: BLE001 - hook tidak boleh menggagalkan penghapusan
            logger.exception("Hook pembersihan Workspace %s gagal", workspace_id)

    await repos.workspaces.delete(workspace_id)

    bus = getattr(request.app.state, "bus", None)
    if bus is not None:
        bus.close_workspace(workspace_id)

    await asyncio.to_thread(_rmtree_with_retry, _uploads_dir(request, workspace_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _cancel_ingestion_jobs(request: Request, workspace_id: str) -> None:
    """Batalkan job ingestion Workspace yang masih berjalan dan tunggu selesai."""
    ingestion = getattr(request.app.state, "ingestion", None)
    if ingestion is None or not hasattr(ingestion, "list_jobs"):
        return
    tasks: list[asyncio.Task[Any]] = []
    for job in ingestion.list_jobs(workspace_id):
        task = getattr(job, "task", None)
        if task is None or task.done():
            continue
        cancel_event = getattr(job, "cancel_event", None)
        if cancel_event is not None:
            cancel_event.set()
        task.cancel()
        tasks.append(task)
    if tasks:
        await asyncio.wait(tasks, timeout=_JOB_CANCEL_TIMEOUT_S)


def _make_writable(func: Callable[..., Any], path: str, _exc: Any) -> None:
    """Handler ``rmtree``: hapus atribut read-only (Windows) lalu coba lagi."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _rmtree_with_retry(path: Path) -> None:
    """``shutil.rmtree`` dengan retry singkat (handle file masih terkunci di Windows)."""
    for attempt in range(_RMTREE_ATTEMPTS):
        if not path.exists():
            return
        try:
            if sys.version_info >= (3, 12):
                shutil.rmtree(path, onexc=_make_writable)
            else:  # pragma: no cover - Python < 3.12
                shutil.rmtree(path, onerror=_make_writable)
        except OSError:
            pass
        if not path.exists():
            return
        if attempt < _RMTREE_ATTEMPTS - 1:
            time.sleep(_RMTREE_DELAY_S)
    shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        logger.warning("Folder upload Workspace tidak dapat dihapus seluruhnya: %s", path)


__all__ = ["router", "WorkspaceCleanupHook"]
