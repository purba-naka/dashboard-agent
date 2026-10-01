"""Router REST Semantic_Model, verifikasi item, dan Blueprint (Req 31, 32.10, 34.2, 37).

Endpoint (prefix ``/api``):

* ``GET    /workspaces/{ws}/semantic`` — model + run draft terakhir (``?status=``, ``?kind=``).
* ``POST   /workspaces/{ws}/semantic/entries`` — entri baru pengguna (``confirmed``/``user``).
* ``PATCH  /workspaces/{ws}/semantic/entries/{id}`` — edit body (``confirmed``/``user``, Req 31.7).
* ``POST   /workspaces/{ws}/semantic/entries/{id}/confirm`` | ``/reject``.
* ``POST   /workspaces/{ws}/semantic/confirm-all`` — semua ``candidate`` → ``confirmed``.
* ``GET    /workspaces/{ws}/semantic/export`` — ``text/yaml``.
* ``POST   /workspaces/{ws}/semantic/import`` — ``text/yaml``; seluruhnya ditolak bila ada entri invalid.
* ``POST   /dashboards/{id}/items/{item_id}/verify`` — jadikan Verified_Query ``confirmed``.
* ``GET    /dashboards/{id}/blueprint`` — Blueprint aktif/terakhir + status slot.

Setiap perubahan menerbitkan ``semantic.updated`` pada bus Workspace.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ValidationError

from studio.api.errors import StudioError
from studio.core.models import SEMANTIC_BODY_MODELS, BusinessMetric, SemanticKind
from studio.core.semantic import MetricError, entry_key, validate_metric_static, verified_query_key
from studio.core.semantic_yaml import DocEntry, SemanticDoc, SemanticImportInvalid, export_yaml, import_yaml
from studio.core.status import item_query_id
from studio.core.verified_queries import is_valid_sql
from studio.store.repos import Repositories, SemanticEntryRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["semantic"])

_STATUSES = ("candidate", "confirmed", "rejected")


def get_repos(request: Request) -> Repositories:
    return request.app.state.repos


def get_engine(request: Request) -> Any:
    return getattr(request.app.state, "engine", None)


def get_bus(request: Request) -> Any:
    return getattr(request.app.state, "bus", None)


def get_drafter(request: Request) -> Any:
    return getattr(request.app.state, "semantic_drafter", None)


class SemanticEntryOut(BaseModel):
    id: str
    kind: str
    entry_key: str
    status: str
    source: str
    dataset_id: str | None
    body: dict[str, Any]
    #: Metrik & Verified_Query: masih lolos validasi terhadap tabel/relasi saat ini.
    valid: bool = True
    updated_at: str


class DraftRunOut(BaseModel):
    id: str
    status: str
    discarded: list[dict[str, Any]]
    started_at: str
    finished_at: str | None


class SemanticModelOut(BaseModel):
    domain: str | None
    domain_confidence: float | None
    assumptions: list[str]
    semantic_version: int
    entries: list[SemanticEntryOut]
    draft_run: DraftRunOut | None


class NewEntryIn(BaseModel):
    kind: SemanticKind
    body: dict[str, Any]


class PatchEntryIn(BaseModel):
    body: dict[str, Any]


def _publish(bus: Any, ws: str, data: dict[str, Any] | None = None) -> None:
    if bus is None:
        return
    try:
        bus.publish(ws, "semantic.updated", data or {})
    except Exception:
        logger.exception("Gagal menerbitkan semantic.updated")


async def _tables(engine: Any, repos: Repositories, ws: str) -> tuple[dict[str, Any], tuple[Any, ...]]:
    if engine is not None:
        wt = await engine.workspace_tables(ws)
        return dict(wt.schemas), tuple(wt.relations)
    datasets = await repos.datasets.list_by_workspace(ws)
    return {d.table_name: d.schema for d in datasets}, ()


def _is_valid(record: SemanticEntryRecord, tables: dict[str, Any], relations: tuple[Any, ...]) -> bool:
    if record.kind == "metric":
        try:
            validate_metric_static(BusinessMetric.model_validate(record.body), tables, relations)
        except (StudioError, ValidationError):
            return False
        return True
    if record.kind == "verified_query":
        return is_valid_sql(str(record.body.get("sql", "")), tables, relations)
    if record.kind == "column":
        cols = tables.get(str(record.body.get("table")))
        return cols is not None and any(getattr(c, "name", c) == record.body.get("column") for c in cols)
    return True


def _out(record: SemanticEntryRecord, valid: bool = True) -> SemanticEntryOut:
    return SemanticEntryOut(
        id=record.id,
        kind=record.kind,
        entry_key=record.entry_key,
        status=record.status,
        source=record.source,
        dataset_id=record.dataset_id,
        body=record.body,
        valid=valid,
        updated_at=record.updated_at.isoformat(),
    )


def _validate_body(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return SEMANTIC_BODY_MODELS[kind].model_validate(body).model_dump(mode="json")
    except ValidationError as exc:
        raise StudioError(
            "VALIDATION_ERROR",
            "Isi entri semantik tidak valid.",
            {"errors": exc.errors(include_url=False, include_context=False)},
            http_status=422,
        ) from exc


async def _validate_sql_entry(engine: Any, ws: str, kind: str, body: dict[str, Any]) -> None:
    """Validasi penuh (V1–V7) untuk metrik & Verified_Query bila Data_Engine tersedia."""
    if engine is None:
        return
    if kind == "metric":
        await engine.validate_metric(ws, BusinessMetric.model_validate(body))
    elif kind == "verified_query":
        try:
            await engine.validate(ws, body["sql"])
        except StudioError as exc:
            raise StudioError(
                "VERIFIED_QUERY_INVALID",
                f"SQL Verified_Query ditolak SQL_Validator ({exc.code}).",
                {"validator_code": exc.code, **exc.details},
                http_status=422,
            ) from exc


async def _dataset_for(repos: Repositories, ws: str, kind: str, body: dict[str, Any]) -> str | None:
    if kind != "column":
        return None
    ds = await repos.datasets.get_by_table(ws, str(body.get("table")))
    if ds is None:
        raise StudioError(
            "VALIDATION_ERROR",
            f"Tabel '{body.get('table')}' tidak ada di Workspace.",
            {"table": body.get("table")},
            http_status=422,
        )
    return ds.id


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/workspaces/{ws}/semantic", response_model=SemanticModelOut)
async def get_semantic(
    ws: str,
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    repos: Repositories = Depends(get_repos),
    engine: Any = Depends(get_engine),
) -> SemanticModelOut:
    await repos.workspaces.get(ws)
    statuses = [s.strip() for s in status.split(",") if s.strip()] if status else None
    if statuses and any(s not in _STATUSES for s in statuses):
        raise StudioError("VALIDATION_ERROR", "Parameter status tidak valid.", {"allowed": list(_STATUSES)}, http_status=422)
    if kind is not None and kind not in SEMANTIC_BODY_MODELS:
        raise StudioError("VALIDATION_ERROR", "Parameter kind tidak valid.", {"allowed": sorted(SEMANTIC_BODY_MODELS)}, http_status=422)
    records = await repos.semantic.list(ws, kind=kind, status=statuses)  # type: ignore[arg-type]
    tables, relations = await _tables(engine, repos, ws)
    meta = await repos.semantic_meta.get(ws)
    run = await repos.draft_runs.latest(ws)
    return SemanticModelOut(
        domain=meta.domain,
        domain_confidence=meta.domain_confidence,
        assumptions=meta.assumptions,
        semantic_version=meta.semantic_version,
        entries=[_out(r, _is_valid(r, tables, relations)) for r in records],
        draft_run=None
        if run is None
        else DraftRunOut(
            id=run.id,
            status=run.status,
            discarded=run.discarded,
            started_at=run.started_at.isoformat(),
            finished_at=run.finished_at.isoformat() if run.finished_at else None,
        ),
    )


@router.post("/workspaces/{ws}/semantic/entries", response_model=SemanticEntryOut, status_code=201)
async def create_entry(
    ws: str,
    payload: NewEntryIn,
    repos: Repositories = Depends(get_repos),
    engine: Any = Depends(get_engine),
    bus: Any = Depends(get_bus),
) -> SemanticEntryOut:
    await repos.workspaces.get(ws)
    body = _validate_body(payload.kind, payload.body)
    await _validate_sql_entry(engine, ws, payload.kind, body)
    dataset_id = await _dataset_for(repos, ws, payload.kind, body)
    record = await repos.semantic.upsert(
        ws,
        kind=payload.kind,
        entry_key=entry_key(payload.kind, body),
        body=body,
        status="confirmed",
        source="user",
        dataset_id=dataset_id,
    )
    _publish(bus, ws, {"entry_id": record.id})
    return _out(record)


@router.patch("/workspaces/{ws}/semantic/entries/{entry_id}", response_model=SemanticEntryOut)
async def patch_entry(
    ws: str,
    entry_id: str,
    payload: PatchEntryIn,
    repos: Repositories = Depends(get_repos),
    engine: Any = Depends(get_engine),
    bus: Any = Depends(get_bus),
) -> SemanticEntryOut:
    current = await repos.semantic.get(entry_id, workspace_id=ws)
    body = _validate_body(current.kind, {**current.body, **payload.body})
    if entry_key(current.kind, body) != current.entry_key:
        raise StudioError(
            "VALIDATION_ERROR",
            "Field kunci entri tidak dapat diubah; buat entri baru.",
            {"entry_key": current.entry_key},
            http_status=422,
        )
    await _validate_sql_entry(engine, ws, current.kind, body)
    record = await repos.semantic.update_body(entry_id, body)
    _publish(bus, ws, {"entry_id": record.id})
    return _out(record)


@router.post("/workspaces/{ws}/semantic/entries/{entry_id}/confirm", response_model=SemanticEntryOut)
async def confirm_entry(
    ws: str, entry_id: str, repos: Repositories = Depends(get_repos), bus: Any = Depends(get_bus)
) -> SemanticEntryOut:
    await repos.semantic.get(entry_id, workspace_id=ws)
    record = await repos.semantic.set_status(entry_id, "confirmed")
    _publish(bus, ws, {"entry_id": record.id})
    return _out(record)


@router.post("/workspaces/{ws}/semantic/entries/{entry_id}/reject", response_model=SemanticEntryOut)
async def reject_entry(
    ws: str, entry_id: str, repos: Repositories = Depends(get_repos), bus: Any = Depends(get_bus)
) -> SemanticEntryOut:
    await repos.semantic.get(entry_id, workspace_id=ws)
    record = await repos.semantic.set_status(entry_id, "rejected")
    _publish(bus, ws, {"entry_id": record.id})
    return _out(record)


@router.post("/workspaces/{ws}/semantic/confirm-all")
async def confirm_all(
    ws: str, repos: Repositories = Depends(get_repos), bus: Any = Depends(get_bus)
) -> dict[str, int]:
    await repos.workspaces.get(ws)
    count = await repos.semantic.confirm_all_candidates(ws)
    if count:
        _publish(bus, ws, {"confirmed": count})
    return {"confirmed": count}


@router.get("/workspaces/{ws}/semantic/export", response_class=PlainTextResponse)
async def export_semantic(ws: str, repos: Repositories = Depends(get_repos)) -> PlainTextResponse:
    workspace = await repos.workspaces.get(ws)
    meta = await repos.semantic_meta.get(ws)
    records = await repos.semantic.list(ws)
    doc = SemanticDoc(
        domain=meta.domain,
        domain_confidence=meta.domain_confidence,
        assumptions=tuple(meta.assumptions),
        entries=tuple(DocEntry(r.kind, r.status, r.body) for r in records),
    )
    return PlainTextResponse(
        export_yaml(doc, name=workspace.name),
        media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="semantic-{ws}.yaml"'},
    )


@router.post("/workspaces/{ws}/semantic/import", response_model=SemanticModelOut)
async def import_semantic(
    ws: str,
    request: Request,
    repos: Repositories = Depends(get_repos),
    engine: Any = Depends(get_engine),
    bus: Any = Depends(get_bus),
) -> SemanticModelOut:
    await repos.workspaces.get(ws)
    raw = (await request.body()).decode("utf-8", errors="replace")
    doc = import_yaml(raw)

    # Validasi SQL & tabel seluruh entri lebih dulu; tidak ada yang ditulis bila gagal.
    issues: list[dict[str, Any]] = []
    datasets: dict[str, str | None] = {}
    for entry in doc.entries:
        try:
            await _validate_sql_entry(engine, ws, entry.kind, entry.body)
            datasets[entry.entry_key] = await _dataset_for(repos, ws, entry.kind, entry.body)
        except StudioError as exc:
            issues.append({"path": entry.kind, "entry_key": entry.entry_key, "reason": exc.message})
    if issues:
        raise SemanticImportInvalid(issues)

    async with repos.db.transaction():
        for entry in doc.entries:
            await repos.semantic.upsert(
                ws,
                kind=entry.kind,  # type: ignore[arg-type]
                entry_key=entry.entry_key,
                body=entry.body,
                status=entry.status,  # type: ignore[arg-type]
                source="user",
                dataset_id=datasets.get(entry.entry_key),
            )
        if doc.domain is not None or doc.assumptions:
            await repos.semantic_meta.set_domain(ws, doc.domain, doc.domain_confidence, doc.assumptions)
    _publish(bus, ws, {"imported": len(doc.entries)})
    return await get_semantic(ws, None, None, repos, engine)


@router.post("/dashboards/{dashboard_id}/items/{item_id}/verify", response_model=SemanticEntryOut)
async def verify_item(
    dashboard_id: str,
    item_id: str,
    repos: Repositories = Depends(get_repos),
    bus: Any = Depends(get_bus),
) -> SemanticEntryOut:
    """Jadikan query item sebagai Verified_Query ``confirmed`` (Req 34.2)."""
    dashboard = await repos.dashboards.get(dashboard_id)
    item = dashboard.content.items.get(item_id)
    if item is None:
        raise StudioError("NOT_FOUND", f"Item '{item_id}' tidak ditemukan.", {"item_id": item_id}, http_status=404)
    query = await repos.queries.get_or_none(item_query_id(item))
    if query is None:
        raise StudioError("UNKNOWN_QUERY", "Query item tidak ditemukan.", {"item_id": item_id}, http_status=404)
    key = verified_query_key(query.sql)
    existing = await repos.semantic.get_by_key(dashboard.workspace_id, key)
    question = (existing.body.get("question") if existing else None) or item.title
    body = {"question": question, "sql": query.sql, "query_id": query.id, "item_id": item_id}
    record = await repos.semantic.upsert(
        dashboard.workspace_id,
        kind="verified_query",
        entry_key=key,
        body=body,
        status="confirmed",
        source="user",
    )
    _publish(bus, dashboard.workspace_id, {"entry_id": record.id})
    return _out(record)


@router.get("/dashboards/{dashboard_id}/blueprint")
async def get_blueprint(dashboard_id: str, repos: Repositories = Depends(get_repos)) -> dict[str, Any] | None:
    await repos.dashboards.get(dashboard_id)
    record = await repos.blueprints.latest(dashboard_id)
    if record is None:
        return None
    return {
        "id": record.id,
        "status": record.status,
        "blueprint": record.blueprint,
        "slot_status": record.slot_status,
        "created_at": record.created_at.isoformat(),
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


__all__ = ["router", "MetricError"]
