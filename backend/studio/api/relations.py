"""Router REST Relation_Candidate / Confirmed_Relation (Req 7.3, 7.4, 7.5, 7.8).

Endpoint (prefix ``/api``):

* ``GET    /workspaces/{ws}/relations?status=candidate|confirmed|rejected``
  (``status`` opsional; boleh dipisah koma, mis. ``candidate,confirmed``).
  Default: semua kecuali ``deleted``.
* ``POST   /workspaces/{ws}/relations/detect`` — jalankan ulang deteksi kandidat.
* ``POST   /workspaces/{ws}/relations/{id}/confirm`` → ``Relation`` (confirmed).
* ``POST   /workspaces/{ws}/relations/{id}/reject``  → ``Relation`` (rejected).
* ``DELETE /workspaces/{ws}/relations/{id}`` → ``204``; soft delete (status
  ``deleted``) sehingga item Dashboard yang query-nya memakai relasi ini
  berstatus ``invalid`` saat dibaca (status turunan, ``core/status.py``).

Setiap perubahan menerbitkan event workspace ``relation.updated`` berisi
``Relation`` (JSON) pada ``app.state.bus``; ``detect`` menerbitkan
``{relation_ids: [...]}`` seperti Profiler.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response

from studio.api.errors import StudioError
from studio.api.schemas import RelationOut
from studio.store.repos import RelationRecord, Repositories

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["relations"])

#: Status yang boleh diminta lewat ``?status=`` (``deleted`` tidak diekspos).
_LISTABLE_STATUSES = ("candidate", "confirmed", "rejected")


# ---------------------------------------------------------------------------
# Dependencies (dari ``request.app.state``)
# ---------------------------------------------------------------------------


def get_repos(request: Request) -> Repositories:
    return request.app.state.repos


def get_bus(request: Request) -> Any:
    return getattr(request.app.state, "bus", None)


def get_profiler(request: Request) -> Any:
    return getattr(request.app.state, "profiler", None)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _parse_status(raw: list[str] | None) -> list[str] | None:
    """``?status=a,b`` / ``?status=a&status=b`` → daftar unik; ``None`` = default repo."""
    if not raw:
        return None
    statuses: list[str] = []
    for part in raw:
        for token in part.split(","):
            token = token.strip().lower()
            if token and token not in statuses:
                statuses.append(token)
    if not statuses:
        return None
    unknown = [s for s in statuses if s not in _LISTABLE_STATUSES]
    if unknown:
        raise StudioError(
            "VALIDATION_ERROR",
            "Parameter status tidak valid.",
            {"invalid": unknown, "allowed": list(_LISTABLE_STATUSES)},
            http_status=422,
        )
    return statuses


def _publish(bus: Any, workspace_id: str, data: Any) -> None:
    """Terbitkan ``relation.updated``; kegagalan bus tidak menggagalkan request."""
    if bus is None:
        return
    try:
        bus.publish(workspace_id, "relation.updated", data)
    except Exception:
        logger.exception("Gagal menerbitkan relation.updated untuk Workspace %s", workspace_id)


def _publish_relation(bus: Any, record: RelationRecord) -> RelationOut:
    out = RelationOut.from_record(record)
    _publish(bus, record.workspace_id, out.model_dump(mode="json"))
    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/workspaces/{ws}/relations", response_model=list[RelationOut])
async def list_relations(
    ws: str,
    status: list[str] | None = Query(default=None),
    repos: Repositories = Depends(get_repos),
) -> list[RelationOut]:
    """Daftar relasi Workspace (Req 7.3); ``404`` bila Workspace tidak ada."""
    statuses = _parse_status(status)
    await repos.workspaces.get(ws)
    records = await repos.relations.list(ws, status=statuses)
    return [RelationOut.from_record(r) for r in records]


@router.post("/workspaces/{ws}/relations/detect", response_model=list[RelationOut])
async def detect_relations(
    ws: str,
    repos: Repositories = Depends(get_repos),
    bus: Any = Depends(get_bus),
    profiler: Any = Depends(get_profiler),
) -> list[RelationOut]:
    """Jalankan ulang deteksi Relation_Candidate; kembalikan kandidat Workspace."""
    if profiler is None:
        raise StudioError(
            "SERVICE_UNAVAILABLE",
            "Layanan profiling belum tersedia.",
            http_status=503,
        )
    await repos.workspaces.get(ws)
    records = await profiler.detect_relations(ws)
    ids = [r.id for r in records if r.status == "candidate"]
    if ids:
        _publish(bus, ws, {"relation_ids": ids})
    candidates = await repos.relations.list(ws, status="candidate")
    return [RelationOut.from_record(r) for r in candidates]


@router.post("/workspaces/{ws}/relations/{relation_id}/confirm", response_model=RelationOut)
async def confirm_relation(
    ws: str,
    relation_id: str,
    repos: Repositories = Depends(get_repos),
    bus: Any = Depends(get_bus),
) -> RelationOut:
    """Simpan sebagai Confirmed_Relation (Req 7.4)."""
    record = await repos.relations.confirm(relation_id, workspace_id=ws)
    return _publish_relation(bus, record)


@router.post("/workspaces/{ws}/relations/{relation_id}/reject", response_model=RelationOut)
async def reject_relation(
    ws: str,
    relation_id: str,
    repos: Repositories = Depends(get_repos),
    bus: Any = Depends(get_bus),
) -> RelationOut:
    """Tolak kandidat; tidak diusulkan lagi (Req 7.5)."""
    record = await repos.relations.reject(relation_id, workspace_id=ws)
    return _publish_relation(bus, record)


@router.delete("/workspaces/{ws}/relations/{relation_id}", status_code=204)
async def delete_relation(
    ws: str,
    relation_id: str,
    repos: Repositories = Depends(get_repos),
    bus: Any = Depends(get_bus),
) -> Response:
    """Soft delete (status ``deleted``); item dependen ``invalid`` saat dibaca (Req 7.8)."""
    record = await repos.relations.delete(relation_id, workspace_id=ws)
    _publish_relation(bus, record)
    return Response(status_code=204)


__all__ = ["router"]
