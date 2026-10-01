"""Router upload, job konversi, dan Dataset (design "REST & SSE API contracts").

Endpoint:

- ``POST /api/workspaces/{ws}/uploads`` — multipart ``file``. CSV →
  ``202 {upload_id, job_id}``; XLSX → ``200 {upload_id, sheets}``; ekstensi
  lain → ``415 UNSUPPORTED_FORMAT`` (Req 2.1, 2.4, 3.2).
- ``POST /api/workspaces/{ws}/uploads/{upload_id}/sheets`` — ``{sheets}`` →
  ``202 {jobs: [{sheet, job_id}]}`` (Req 3.2, 3.3).
- ``GET /api/jobs/{job_id}`` — ``{status, progress, dataset_id?, error?}`` (Req 5.3).
- ``GET /api/workspaces/{ws}/datasets`` — ``Dataset[]``.
- ``GET /api/workspaces/{ws}/datasets/{id}`` — ``{dataset, schema,
  column_profiles, quality, column_mapping}`` (Req 2.5, 6.2).
- ``PATCH /api/workspaces/{ws}/datasets/{id}`` — ``{privacy_no_samples?}`` →
  ``Dataset`` (Req 27.4).
- ``POST /api/workspaces/{ws}/datasets/{id}/reupload`` — multipart ``file``
  (+ ``sheet`` opsional untuk XLSX) → ``202 {job_id}`` atau
  ``409 SCHEMA_MISMATCH {missing, added, changed}`` (Req 26.1, 26.3).

Batas ukuran upload (Req 5.1): ``IngestionService.max_upload_bytes`` →
``settings.max_upload_bytes`` (bila ada) → :data:`DEFAULT_MAX_UPLOAD_BYTES`
(1 GiB). Nilai terkonfigurasi di bawah :data:`MIN_MAX_UPLOAD_BYTES` (1 GiB)
dinaikkan ke batas minimum tersebut. Body multipart di-parse streaming oleh
Starlette ke ``SpooledTemporaryFile`` (di-spill ke disk setelah 1 MiB)
sehingga memori tetap terbatas; ``Content-Length`` yang jelas melebihi batas
ditolak ``413`` sebelum body dibaca. File sementara ditutup/dihapus setelah
request selesai.
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from starlette.datastructures import UploadFile
from starlette.requests import ClientDisconnect

from studio.api.errors import StudioError
from studio.api.schemas import (
    CsvUploadResponse,
    DatasetDetail,
    DatasetOut,
    JobStatus,
    ReuploadResponse,
    SelectSheetsRequest,
    SelectSheetsResponse,
    SheetInfo,
    SheetJob,
    UpdateDatasetRequest,
    XlsxUploadResponse,
)
from studio.data.ingestion import IngestionService, UploadInterrupted, UploadTooLarge
from studio.store.repos import Repositories, UploadRecord

__all__ = ["DEFAULT_MAX_UPLOAD_BYTES", "MIN_MAX_UPLOAD_BYTES", "router"]

#: Batas minimum ukuran file upload (Req 5.1: "hingga minimal 1 GB").
MIN_MAX_UPLOAD_BYTES = 1 << 30
#: Batas default bila tidak ada konfigurasi.
DEFAULT_MAX_UPLOAD_BYTES = MIN_MAX_UPLOAD_BYTES
#: Toleransi overhead multipart (boundary, header part, field ``sheet``) pada cek Content-Length.
_MULTIPART_OVERHEAD_BYTES = 1 << 20

router = APIRouter(prefix="/api", tags=["datasets"])

_MULTIPART_FILE_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "sheet": {"type": "string"},
                    },
                }
            }
        },
    }
}


# ---------------------------------------------------------------------------
# Dependencies (dari ``request.app.state``)
# ---------------------------------------------------------------------------


def _repos(request: Request) -> Repositories:
    return request.app.state.repos


def _ingestion(request: Request) -> IngestionService:
    return request.app.state.ingestion


def _upload_limit(request: Request) -> int:
    """Batas ukuran file upload efektif (byte), minimal 1 GiB."""
    configured = getattr(request.app.state.ingestion, "max_upload_bytes", None)
    if configured is None:
        settings = getattr(request.app.state, "settings", None)
        configured = getattr(settings, "max_upload_bytes", None)
    if configured is None:
        return DEFAULT_MAX_UPLOAD_BYTES
    return max(int(configured), MIN_MAX_UPLOAD_BYTES)


ReposDep = Annotated[Repositories, Depends(_repos)]
IngestionDep = Annotated[IngestionService, Depends(_ingestion)]
UploadLimitDep = Annotated[int, Depends(_upload_limit)]


# ---------------------------------------------------------------------------
# Helper multipart
# ---------------------------------------------------------------------------


def _check_content_length(request: Request, limit: int) -> None:
    raw = request.headers.get("content-length")
    if raw is None:
        return
    try:
        length = int(raw)
    except ValueError:
        return
    if length > limit + _MULTIPART_OVERHEAD_BYTES:
        raise UploadTooLarge("upload", limit)


@contextlib.asynccontextmanager
async def _multipart_file(request: Request, limit: int):  # type: ignore[no-untyped-def]
    """Parse body multipart; yield ``(UploadFile, sheet | None)``; file temp ditutup di akhir."""
    _check_content_length(request, limit)
    try:
        form = await request.form(max_files=1, max_fields=8)
    except ClientDisconnect as exc:
        raise UploadInterrupted("upload", "klien terputus") from exc
    try:
        file = form.get("file")
        if not isinstance(file, UploadFile):
            raise StudioError(
                "VALIDATION_ERROR",
                "Field multipart 'file' wajib diisi dengan file.",
                {"field": "file"},
                http_status=422,
            )
        filename = file.filename or ""
        if file.size is not None and file.size > limit:
            raise UploadTooLarge(filename or "upload", limit)
        sheet_raw = form.get("sheet")
        sheet = (sheet_raw.strip() or None) if isinstance(sheet_raw, str) else None
        yield file, sheet
    finally:
        await form.close()


async def _discard_quietly(ingestion: IngestionService, ws: str, upload: UploadRecord) -> None:
    with contextlib.suppress(Exception):
        await ingestion.discard_upload(ws, upload)


# ---------------------------------------------------------------------------
# Upload & job
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{ws}/uploads",
    response_model=CsvUploadResponse | XlsxUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=_MULTIPART_FILE_BODY,
    responses={200: {"model": XlsxUploadResponse, "description": "XLSX: daftar sheet"}},
)
async def upload_file(
    ws: str,
    request: Request,
    response: Response,
    ingestion: IngestionDep,
    limit: UploadLimitDep,
) -> CsvUploadResponse | XlsxUploadResponse:
    """Upload CSV (job konversi langsung) atau XLSX (kembalikan daftar sheet)."""
    async with _multipart_file(request, limit) as (file, _sheet):
        # Ekstensi dicek lebih dulu di save_upload (415, tanpa menulis apa pun).
        upload = await ingestion.save_upload(ws, file.filename or "", file)

    if upload.kind == "csv":
        try:
            job_id = await ingestion.start_csv_job(ws, upload)
        except BaseException:
            await _discard_quietly(ingestion, ws, upload)
            raise
        response.status_code = status.HTTP_202_ACCEPTED
        return CsvUploadResponse(upload_id=upload.id, job_id=job_id)

    # XLSX: workbook rusak → upload dibuang oleh service, 422 (Req 3.4).
    sheets = await ingestion.list_upload_sheets(upload)
    response.status_code = status.HTTP_200_OK
    return XlsxUploadResponse(
        upload_id=upload.id,
        sheets=[SheetInfo(name=s.name, rows_hint=s.rows_hint) for s in sheets],
    )


@router.post(
    "/workspaces/{ws}/uploads/{upload_id}/sheets",
    response_model=SelectSheetsResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def select_sheets(
    ws: str, upload_id: str, body: SelectSheetsRequest, ingestion: IngestionDep
) -> SelectSheetsResponse:
    """Satu job konversi per sheet terpilih (Req 3.2); sheet kosong gagal di job (Req 3.3)."""
    jobs = await ingestion.start_sheet_jobs(ws, upload_id, body.sheets)
    return SelectSheetsResponse(jobs=[SheetJob(sheet=s, job_id=j) for s, j in jobs])


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str, ingestion: IngestionDep) -> JobStatus:
    return JobStatus.from_job(ingestion.get_job_record(job_id))


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------


@router.get("/workspaces/{ws}/datasets", response_model=list[DatasetOut])
async def list_datasets(ws: str, repos: ReposDep) -> list[DatasetOut]:
    await repos.workspaces.get(ws)
    return [DatasetOut.from_record(d) for d in await repos.datasets.list_by_workspace(ws)]


@router.get("/workspaces/{ws}/datasets/{dataset_id}", response_model=DatasetDetail)
async def get_dataset(ws: str, dataset_id: str, repos: ReposDep) -> DatasetDetail:
    """Skema, profil kolom, kualitas data, dan pemetaan nama kolom (Req 2.5, 6.2)."""
    dataset = await repos.datasets.get(dataset_id, workspace_id=ws)
    profile = await repos.profiles.get_or_none(dataset.id)
    return DatasetDetail.from_records(dataset, profile)


@router.patch("/workspaces/{ws}/datasets/{dataset_id}", response_model=DatasetOut)
async def update_dataset(
    ws: str, dataset_id: str, body: UpdateDatasetRequest, repos: ReposDep
) -> DatasetOut:
    """Toggle ``privacy_no_samples`` (Req 27.4)."""
    dataset = await repos.datasets.get(dataset_id, workspace_id=ws)
    if body.privacy_no_samples is not None:
        dataset = await repos.datasets.set_privacy(dataset.id, body.privacy_no_samples)
    return DatasetOut.from_record(dataset)


@router.post(
    "/workspaces/{ws}/datasets/{dataset_id}/reupload",
    response_model=ReuploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra=_MULTIPART_FILE_BODY,
)
async def reupload_dataset(
    ws: str,
    dataset_id: str,
    request: Request,
    repos: ReposDep,
    ingestion: IngestionDep,
    limit: UploadLimitDep,
) -> ReuploadResponse:
    """Ganti data Dataset bila skema cocok (Req 26.1); tidak cocok → 409 (Req 26.3).

    Untuk XLSX, sheet = field ``sheet`` → ``dataset.sheet_name`` → sheet pertama.
    """
    # 404 lebih dulu agar file tidak disimpan untuk Dataset yang tidak ada.
    await repos.datasets.get(dataset_id, workspace_id=ws)
    async with _multipart_file(request, limit) as (file, sheet):
        upload = await ingestion.save_upload(ws, file.filename or "", file)
    try:
        job_id = await ingestion.start_reupload(ws, dataset_id, upload, sheet=sheet)
    except BaseException:
        await _discard_quietly(ingestion, ws, upload)
        raise
    return ReuploadResponse(job_id=job_id)
