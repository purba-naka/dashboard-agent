"""Router detail query tersimpan (Req 14.7).

``GET /api/queries/{query_id}`` → ``QueryDetail`` ``{sql, columns, rows, row_count,
executed_at, filters}``: SQL apa adanya, skema output, snapshot hasil ≤ 1.000
baris, jumlah baris total, waktu eksekusi, dan filter yang aktif saat eksekusi.
Dipakai panel detail Insight_Card/chart untuk menampilkan SQL sumber dan bukti.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from studio.api.schemas import QueryDetail
from studio.store.repos import Repositories

router = APIRouter(prefix="/api", tags=["queries"])


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


@router.get("/queries/{query_id}", response_model=QueryDetail)
async def get_query(query_id: str, repos: Repositories = Depends(_repos)) -> QueryDetail:
    """``404 NOT_FOUND`` bila query tidak ada."""
    return QueryDetail.from_record(await repos.queries.get(query_id))


__all__ = ["router"]
