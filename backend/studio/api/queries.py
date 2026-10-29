"""Router detail query tersimpan (Req 14.7).

``GET /api/queries/{query_id}`` → ``QueryDetail`` ``{sql, columns, rows, row_count,
executed_at, filters}``: SQL apa adanya, skema output, snapshot hasil ≤ 1.000
baris, jumlah baris total, waktu eksekusi, dan filter yang aktif saat eksekusi.
Dipakai panel detail Insight_Card/chart untuk menampilkan SQL sumber dan bukti.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from studio.api.schemas import QueryDetail
from studio.store.repos import Repositories

router = APIRouter(prefix="/api", tags=["queries"])


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


@router.get("/queries/{query_id}", response_model=QueryDetail)
async def get_query(query_id: str, repos: Repositories = Depends(_repos)) -> QueryDetail:
    """``404 NOT_FOUND`` bila query tidak ada."""
    return QueryDetail.from_record(await repos.queries.get(query_id))


class ExecuteQueryRequest(BaseModel):
    sql: str = Field(min_length=1)


@router.post("/workspaces/{ws}/queries", status_code=201)
async def execute_query(
    ws: str, body: ExecuteQueryRequest, request: Request, repos: Repositories = Depends(_repos)
) -> dict[str, Any]:
    """Eksekusi SQL pengguna lewat Data_Engine (SELECT read-only, divalidasi SQL_Validator)."""
    await repos.workspaces.get(ws)
    result = await request.app.state.engine.execute(ws, body.sql, created_by="user")
    return {"query_id": result.query_id, "row_count": result.row_count}


@router.get("/workspaces/{ws}/queries")
async def list_queries(
    ws: str, limit: int = Query(default=100, ge=1, le=500), repos: Repositories = Depends(_repos)
) -> list[dict[str, Any]]:
    """Query terbaru di Workspace (tanpa snapshot baris) untuk audit dan debugging."""
    await repos.workspaces.get(ws)
    return [
        {
            "id": q.id,
            "sql": q.sql,
            "tables_used": list(q.tables_used),
            "relations_used": sorted(q.relations_used),
            "row_count": q.row_count,
            "executed_at": q.executed_at.isoformat(),
            "created_by": q.created_by,
        }
        for q in await repos.queries.list_by_workspace(ws, limit=limit)
    ]


__all__ = ["router"]
