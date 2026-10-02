"""Ingestion_Service: orkestrasi upload → job konversi → Dataset (Req 2.1, 3.2, 4.1,
4.2, 5.1, 5.3, 5.5, 26.1–26.3, 28.3, 29.5).

Alur yang dipakai endpoint FastAPI (task 16.3)::

    upload = await svc.save_upload(ws, file.filename, file)       # 415 bila ekstensi salah
    job_id = await svc.start_csv_job(ws, upload)                  # CSV → 202 {upload_id, job_id}
    sheets = await svc.list_upload_sheets(upload)                 # XLSX → 200 {upload_id, sheets}
    jobs   = await svc.start_sheet_jobs(ws, upload.id, ["Sheet1"])  # [(sheet, job_id)]
    job_id = await svc.start_reupload(ws, dataset_id, upload)     # 202, atau SchemaMismatch (409)
    status = svc.get_job(job_id)                                  # {status, progress, dataset_id?, error?}

Penyimpanan (``DATA_DIR/uploads/{workspace_id}/``):

- File asli: nama hasil ``sanitize_filename`` (prefiks ULID), path diverifikasi
  ``safe_join``. ``uploads.stored_path`` = nama file tersebut (relatif terhadap
  folder workspace).
- Parquet Dataset: ``{dataset_id}.parquet``. ``datasets.parquet_path`` disimpan
  relatif terhadap ``DATA_DIR/uploads`` (``"{workspace_id}/{dataset_id}.parquet"``);
  gunakan :meth:`IngestionService.resolve_parquet_path` untuk path absolut.

Job konversi berjalan di ``asyncio.to_thread`` (maks ``max_concurrent_jobs``
bersamaan; sisanya berstatus ``queued``). Konversi menulis ke file *staging*
``{dataset_id}.{job_id}.staging.parquet`` (via ``.partial`` milik
``ParquetBatchWriter``), lalu finalisasi dalam satu transaksi Metadata_Store:
insert/update Dataset kemudian ``os.replace`` staging → file final; kegagalan
apa pun me-rollback baris DB. Bila job gagal/terputus, file staging/parsial
dihapus; untuk CSV (dan upload re-upload) file asli beserta baris ``uploads``
juga dihapus sehingga tidak ada sisa file (Req 5.5). File XLSX asli
dipertahankan saat satu sheet gagal karena sheet lain dapat berasal dari file
yang sama.

Progres (Req 5.3): nilai 0–99 monoton dari konverter (estimasi bagian file yang
telah diproses) dikirim sebagai ``job.progress {job_id, progress}`` setiap
batch, plus heartbeat setiap ≤ ``heartbeat_interval`` detik selama job
berjalan; ``100`` + ``job.done {job_id, dataset_id}`` setelah Dataset
terdaftar; ``job.failed {job_id, error}`` saat gagal.

Re-upload (Req 26): file baru di-parse ke Parquet staging, ``diff_schemas``
dengan skema tersimpan; kosong → ganti file atomik + ``data_version``/
``data_updated_at`` naik (``DatasetRepo.update_file``); tidak kosong →
``SchemaMismatch`` (``SCHEMA_MISMATCH {missing, added, changed}``) dan Dataset
lama tidak berubah. Pilihan desain: untuk CSV, header dibaca secara sinkron di
``start_reupload`` (murah: satu baris) sehingga kolom hilang/bertambah langsung
ditolak dengan 409; perubahan *tipe* (butuh inferensi penuh) dan re-upload XLSX
dideteksi di job dan dilaporkan lewat ``job.failed`` dengan error
``SCHEMA_MISMATCH`` yang sama.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import inspect
import io
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import fastexcel

from studio.api.errors import StudioError
from studio.core.identifiers import (
    ColumnMappingEntry,
    check_extension,
    make_table_name,
    normalize_columns,
    safe_join,
    sanitize_filename,
)
from studio.core.models import ColumnInfo
from studio.core.schema_diff import SchemaDiff, diff_schemas
from studio.data.csv_reader import DEFAULT_BATCH_SIZE, convert_csv_to_parquet
from studio.data.parquet_writer import ParquetBatchWriter, partial_path_for
from studio.data.xlsx_reader import (
    SchemaWidened,
    SheetInfo,
    SheetNotFound,
    UnreadableWorkbook,
    XlsxSheetReader,
    list_sheets,
)
from studio.events.bus import EventBus
from studio.store.repos import DatasetRecord, Repositories, UploadRecord, new_id, not_found

if TYPE_CHECKING:
    from studio.config import Settings

__all__ = [
    "DEFAULT_MAX_CONCURRENT_JOBS",
    "HEARTBEAT_INTERVAL_S",
    "UPLOAD_CHUNK_BYTES",
    "DatasetHook",
    "IngestionService",
    "Job",
    "JobKind",
    "JobState",
    "SchemaMismatch",
    "UploadInterrupted",
    "UploadSource",
    "UploadTooLarge",
]

logger = logging.getLogger(__name__)

#: Ukuran chunk penulisan file upload ke disk (Req 5.1).
UPLOAD_CHUNK_BYTES = 1 << 20
#: Interval heartbeat ``job.progress`` selama job berjalan (Req 5.3: ≤ 1 dtk).
HEARTBEAT_INTERVAL_S = 1.0
DEFAULT_MAX_CONCURRENT_JOBS = 2

#: Porsi progres XLSX untuk membaca/menginferensi sheet; sisanya untuk batch.
_XLSX_SCHEMA_SHARE = 10.0
#: Percobaan konversi XLSX: awal + ulang setelah ``SchemaWidened``. Pelebaran
#: monoton (integer→float→string) sehingga pengulangan konvergen dengan cepat.
_MAX_XLSX_ATTEMPTS = 3
_STAGING_SUFFIX = ".staging.parquet"
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY_S = 0.1

JobState = Literal["queued", "running", "done", "failed"]
JobKind = Literal["csv", "xlsx_sheet", "reupload"]
#: Hook opsional setelah Dataset dibuat / di-re-upload (mis. profiling, task 14.5).
DatasetHook = Callable[[DatasetRecord], Awaitable[None]]
#: Sumber body upload: ``UploadFile``/objek ber-``read(n)``, async/sync iterable byte, atau bytes.
UploadSource = Any

_T = TypeVar("_T")


# ---------------------------------------------------------------------------
# Error domain
# ---------------------------------------------------------------------------


class SchemaMismatch(StudioError):
    """Skema file re-upload tidak kompatibel dengan Dataset tersimpan (Req 26.3)."""

    def __init__(self, diff: SchemaDiff, table_name: str | None = None) -> None:
        parts: list[str] = []
        if diff.missing:
            parts.append("kolom hilang: " + ", ".join(diff.missing))
        if diff.added:
            parts.append("kolom baru: " + ", ".join(diff.added))
        if diff.changed:
            parts.append(
                "tipe berubah: "
                + ", ".join(f"{c.name} ({c.old_type} → {c.new_type})" for c in diff.changed)
            )
        target = f" Dataset '{table_name}'" if table_name else " Dataset"
        super().__init__(
            code="SCHEMA_MISMATCH",
            message=f"Skema file baru tidak cocok dengan{target}: {'; '.join(parts)}.",
            details=diff.as_details(),
            http_status=409,
        )
        self.diff = diff


class UploadInterrupted(StudioError):
    """Body upload terputus / gagal ditulis; file parsial sudah dihapus (Req 5.5)."""

    def __init__(self, filename: str, reason: str | None = None) -> None:
        details: dict[str, Any] = {"filename": filename}
        if reason:
            details["reason"] = reason
        super().__init__(
            code="UPLOAD_INTERRUPTED",
            message=f"Upload '{filename}' terputus sebelum selesai.",
            details=details,
            http_status=400,
        )


class UploadTooLarge(StudioError):
    """Ukuran upload melebihi batas yang dikonfigurasi."""

    def __init__(self, filename: str, limit_bytes: int) -> None:
        super().__init__(
            code="PAYLOAD_TOO_LARGE",
            message=f"File '{filename}' melebihi batas ukuran upload ({limit_bytes} byte).",
            details={"filename": filename, "limit_bytes": limit_bytes},
            http_status=413,
        )


class _JobCancelled(Exception):
    """Dilempar dari callback progres di thread saat job dibatalkan (shutdown)."""


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(eq=False)
class Job:
    """Satu job konversi di registry in-memory."""

    id: str
    workspace_id: str
    kind: JobKind
    upload_id: str
    sheet: str | None = None
    #: Dataset target re-upload (None untuk job pembuatan Dataset baru).
    target_dataset_id: str | None = None
    status: JobState = "queued"
    progress: float = 0.0
    dataset_id: str | None = None
    error: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utc_now)
    finished_at: datetime | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def to_status(self) -> dict[str, Any]:
        """Bentuk ``JobStatus`` frontend: ``{status, progress, dataset_id?, error?}``."""
        out: dict[str, Any] = {"status": self.status, "progress": round(self.progress, 1)}
        if self.dataset_id is not None:
            out["dataset_id"] = self.dataset_id
        if self.error is not None:
            out["error"] = self.error
        return out


@dataclass(frozen=True, slots=True)
class _Converted:
    """Hasil konversi di thread: file staging + metadata skema."""

    staging: Path
    columns: list[ColumnInfo]
    mapping: list[ColumnMappingEntry]
    row_count: int


class _ProgressTracker:
    """Callback progres thread-safe: monoton, dibatasi 99, publish saat berubah."""

    def __init__(self, job: Job, publish: Callable[[Job], None]) -> None:
        self._job = job
        self._publish = publish
        self._lock = threading.Lock()
        self._last_sent: float | None = None

    def update(self, pct: float) -> None:
        job = self._job
        if job.cancel_event.is_set():
            raise _JobCancelled()
        with self._lock:
            value = min(99.0, max(job.progress, float(pct)))
            job.progress = value
            shown = round(value, 1)
            send = shown != self._last_sent
            if send:
                self._last_sent = shown
        if send:
            self._publish(job)


# ---------------------------------------------------------------------------
# Helper file (sinkron; dipanggil di thread)
# ---------------------------------------------------------------------------


def _unlink_quiet(path: str | os.PathLike[str] | None) -> None:
    if path is None:
        return
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)


def _remove_staging(staging: Path) -> None:
    _unlink_quiet(partial_path_for(staging))
    _unlink_quiet(staging)


def _replace_with_retry(src: Path, dst: Path) -> None:
    """``os.replace`` dengan retry singkat: di Windows target yang sedang dibuka
    pembaca (mis. worker query) menolak diganti sementara (PermissionError)."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY_S)


def _display_name(filename: str) -> str:
    """Basename nama file asli untuk tampilan (tanpa komponen path klien)."""
    base = re.split(r"[/\\]", str(filename or ""))[-1].strip()
    return base or "upload"


def _reason(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return (lines[0] if lines else type(exc).__name__)[:300]


def _sheet_names(path: Path, filename: str) -> list[str]:
    try:
        return list(fastexcel.read_excel(path).sheet_names)
    except Exception as exc:  # file rusak / terenkripsi / bukan zip
        raise UnreadableWorkbook(filename, _reason(exc)) from exc


def _csv_header_names(path: Path) -> list[str] | None:
    """Header CSV ternormalisasi (satu record pertama); None bila tidak terbaca.

    Dipakai untuk pra-cek re-upload yang murah; error parse sebenarnya
    dilaporkan oleh job konversi.
    """
    try:
        with (
            open(path, "rb") as raw,
            io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text,
        ):
            header = next(csv.reader(text), None)
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if not header:
        return None
    names, _ = normalize_columns(header)
    return names


def _xlsx_progress(rows_done: int, total_rows: int | None, batch_size: int) -> float:
    span = 99.0 - _XLSX_SCHEMA_SHARE
    if total_rows:
        return _XLSX_SCHEMA_SHARE + span * min(1.0, rows_done / total_rows)
    # Jalur blok (> 100 MB): total baris tidak diketahui → kurva asimtotik.
    return _XLSX_SCHEMA_SHARE + span * (rows_done / (rows_done + 5 * batch_size))


def _convert_sheet(
    src: Path,
    sheet: str,
    staging: Path,
    progress: Callable[[float], None],
    batch_size: int,
) -> _Converted:
    """Konversi satu sheet XLSX ke Parquet staging (dijalankan di thread)."""
    logical_types: dict[str, Any] | None = None
    for _ in range(_MAX_XLSX_ATTEMPTS):
        reader = XlsxSheetReader(src, sheet, logical_types=logical_types, batch_size=batch_size)
        schema = reader.polars_schema()  # sheet tanpa data → EmptySheet
        total = None if reader.uses_blocks else reader.read().height
        progress(_XLSX_SCHEMA_SHARE)
        rows = 0
        try:
            with ParquetBatchWriter(staging, schema) as writer:
                for batch in reader.iter_batches(batch_size):
                    writer.write(batch)
                    rows += batch.height
                    progress(_xlsx_progress(rows, total, batch_size))
        except SchemaWidened as exc:  # writer sudah menghapus .partial
            logical_types = dict(exc.schema)
            continue
        return _Converted(staging, reader.schema(), reader.column_mapping, writer.rows_written)
    raise StudioError(
        "CONVERSION_FAILED",
        f"Tipe kolom sheet '{sheet}' tidak stabil setelah beberapa percobaan konversi.",
        {"sheet": sheet},
        http_status=422,
    )


async def _iter_upload_chunks(source: UploadSource, chunk_size: int) -> AsyncIterator[bytes]:
    """Normalisasi sumber body upload menjadi chunk berukuran ≈ ``chunk_size``."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]
        return

    read = getattr(source, "read", None)
    if callable(read):  # starlette UploadFile (async read) atau file-like sinkron
        is_async = inspect.iscoroutinefunction(read)
        while True:
            chunk = await read(chunk_size) if is_async else await asyncio.to_thread(read, chunk_size)
            if not chunk:
                return
            yield bytes(chunk)

    buf = bytearray()
    if isinstance(source, AsyncIterable):  # mis. Request.stream()
        async for part in source:
            buf += part
            if len(buf) >= chunk_size:
                yield bytes(buf)
                buf.clear()
    elif isinstance(source, Iterable):
        for part in source:
            buf += part
            if len(buf) >= chunk_size:
                yield bytes(buf)
                buf.clear()
    else:
        raise TypeError(f"Sumber upload tidak didukung: {type(source).__name__}")
    if buf:
        yield bytes(buf)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class IngestionService:
    """Orkestrasi upload CSV/XLSX, job konversi Parquet, dan re-upload Dataset."""

    def __init__(
        self,
        repos: Repositories,
        bus: EventBus,
        data_dir: str | os.PathLike[str],
        *,
        on_dataset_registered: DatasetHook | None = None,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_S,
        max_upload_bytes: int | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        chunk_bytes: int = UPLOAD_CHUNK_BYTES,
    ) -> None:
        if max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs harus >= 1")
        if not 0 < heartbeat_interval <= 1.0:
            raise ValueError("heartbeat_interval harus dalam (0, 1] detik")
        self.repos = repos
        self.bus = bus
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.uploads_root = self.data_dir / "uploads"
        self.on_dataset_registered = on_dataset_registered
        self.heartbeat_interval = heartbeat_interval
        self.max_upload_bytes = max_upload_bytes
        self.batch_size = batch_size
        self.chunk_bytes = chunk_bytes
        self._slots = asyncio.Semaphore(max_concurrent_jobs)
        self._jobs: dict[str, Job] = {}

    @classmethod
    def from_settings(
        cls, settings: Settings, repos: Repositories, bus: EventBus, **kwargs: Any
    ) -> IngestionService:
        return cls(repos, bus, settings.data_dir, **kwargs)

    # -- path ---------------------------------------------------------------

    def workspace_dir(self, workspace_id: str) -> Path:
        """``DATA_DIR/uploads/{workspace_id}`` (diverifikasi tetap di bawah uploads)."""
        return safe_join(self.uploads_root, workspace_id)

    def upload_path(self, upload: UploadRecord) -> Path:
        """Path absolut file asli sebuah upload."""
        return safe_join(self.workspace_dir(upload.workspace_id), upload.stored_path)

    @staticmethod
    def parquet_rel_path(workspace_id: str, dataset_id: str) -> str:
        """Nilai ``datasets.parquet_path`` (relatif terhadap ``DATA_DIR/uploads``)."""
        return f"{workspace_id}/{dataset_id}.parquet"

    def resolve_parquet_path(self, dataset: DatasetRecord | str) -> Path:
        """Path absolut file Parquet dari ``DatasetRecord`` atau nilai ``parquet_path``."""
        raw = dataset.parquet_path if isinstance(dataset, DatasetRecord) else str(dataset)
        path = Path(raw)
        return path if path.is_absolute() else safe_join(self.uploads_root, raw)

    def _staging_path(self, workspace_id: str, dataset_id: str, job_id: str) -> Path:
        return safe_join(self.workspace_dir(workspace_id), f"{dataset_id}.{job_id}{_STAGING_SUFFIX}")

    # -- upload -------------------------------------------------------------

    async def save_upload(
        self, workspace_id: str, filename: str, source: UploadSource
    ) -> UploadRecord:
        """Simpan body upload ke ``data/uploads/{ws}/`` secara streaming (chunk 1 MiB).

        Ekstensi nama *asli* dicek lebih dulu (``UnsupportedFormat`` 415, tanpa
        menulis apa pun). Body ditulis ke ``.partial`` lalu di-rename; bila
        klien terputus / error, file parsial dihapus dan ``UploadInterrupted``
        di-raise (Req 5.5, 29.5).
        """
        ext = check_extension(filename)
        await self.repos.workspaces.get(workspace_id)
        ws_dir = self.workspace_dir(workspace_id)
        stored_name = sanitize_filename(filename)
        target = safe_join(ws_dir, stored_name)
        partial = partial_path_for(target)
        await asyncio.to_thread(ws_dir.mkdir, parents=True, exist_ok=True)

        size = 0
        try:
            fh = await asyncio.to_thread(open, partial, "wb")
            try:
                async for chunk in _iter_upload_chunks(source, self.chunk_bytes):
                    size += len(chunk)
                    if self.max_upload_bytes is not None and size > self.max_upload_bytes:
                        raise UploadTooLarge(_display_name(filename), self.max_upload_bytes)
                    await asyncio.to_thread(fh.write, chunk)
            finally:
                await asyncio.to_thread(fh.close)
            await asyncio.to_thread(os.replace, partial, target)
        except BaseException as exc:
            _unlink_quiet(partial)
            _unlink_quiet(target)
            if isinstance(exc, StudioError) or not isinstance(exc, Exception):
                raise
            raise UploadInterrupted(_display_name(filename), _reason(exc)) from exc

        try:
            return await self.repos.uploads.create(
                workspace_id,
                original_name=_display_name(filename),
                stored_path=stored_name,
                kind="csv" if ext == ".csv" else "xlsx",
                size_bytes=size,
            )
        except BaseException:
            _unlink_quiet(target)
            raise

    async def list_upload_sheets(self, upload: UploadRecord) -> list[SheetInfo]:
        """Daftar sheet upload XLSX (Req 3.2). Workbook rusak → upload dibuang, 422."""
        upload = await self._get_upload(upload.workspace_id, upload, kind="xlsx")
        try:
            return await asyncio.to_thread(list_sheets, self.upload_path(upload))
        except UnreadableWorkbook as exc:
            await self._discard_upload(upload)
            raise UnreadableWorkbook(upload.original_name, exc.details.get("reason")) from exc

    async def discard_upload(self, workspace_id: str, upload: UploadRecord | str) -> None:
        """Hapus upload yang belum dipakai Dataset (baris + file asli)."""
        await self._discard_upload(await self._get_upload(workspace_id, upload))

    # -- job: pembuatan Dataset --------------------------------------------

    async def start_csv_job(self, workspace_id: str, upload: UploadRecord | str) -> str:
        """Mulai job konversi CSV → Parquet → Dataset; kembalikan ``job_id``."""
        upload = await self._get_upload(workspace_id, upload, kind="csv")
        job = self._new_job(workspace_id, "csv", upload.id)
        src = self.upload_path(upload)
        dataset_id = new_id()
        staging = self._staging_path(workspace_id, dataset_id, job.id)
        batch_size = self.batch_size

        def convert(progress: Callable[[float], None]) -> _Converted:
            columns, mapping, rows = convert_csv_to_parquet(
                src, staging, progress, batch_size=batch_size
            )
            return _Converted(staging, columns, mapping, rows)

        async def register(conv: _Converted) -> DatasetRecord:
            return await self._register_new(
                workspace_id, dataset_id, upload, conv, table_base=upload.original_name
            )

        async def cleanup() -> None:
            _remove_staging(staging)
            await self._discard_upload(upload)

        self._launch(job, convert, register, cleanup)
        return job.id

    async def start_sheet_jobs(
        self, workspace_id: str, upload: UploadRecord | str, sheets: Sequence[str]
    ) -> list[tuple[str, str]]:
        """Satu job per sheet terpilih (Req 3.2); kembalikan ``[(sheet, job_id)]``.

        Nama sheet divalidasi sinkron (``SheetNotFound`` 422); sheet tanpa baris
        data gagal di job dengan ``EMPTY_SHEET {sheet}`` (Req 3.3).
        """
        upload = await self._get_upload(workspace_id, upload, kind="xlsx")
        selected = list(dict.fromkeys(str(s) for s in sheets))
        if not selected:
            raise StudioError(
                "VALIDATION_ERROR", "Pilih minimal satu sheet.", {"sheets": []}, http_status=422
            )
        src = self.upload_path(upload)
        available = await asyncio.to_thread(_sheet_names, src, upload.original_name)
        for sheet in selected:
            if sheet not in available:
                raise SheetNotFound(sheet, available)
        # Workbook satu sheet: nama tabel dari nama file (hindari "sheet1").
        single_sheet = len(available) == 1
        return [
            (sheet, self._start_sheet_job(workspace_id, upload, sheet, single_sheet))
            for sheet in selected
        ]

    def _start_sheet_job(
        self, workspace_id: str, upload: UploadRecord, sheet: str, single_sheet: bool
    ) -> str:
        job = self._new_job(workspace_id, "xlsx_sheet", upload.id, sheet=sheet)
        src = self.upload_path(upload)
        dataset_id = new_id()
        staging = self._staging_path(workspace_id, dataset_id, job.id)
        batch_size = self.batch_size

        def convert(progress: Callable[[float], None]) -> _Converted:
            return _convert_sheet(src, sheet, staging, progress, batch_size)

        async def register(conv: _Converted) -> DatasetRecord:
            return await self._register_new(
                workspace_id,
                dataset_id,
                upload,
                conv,
                table_base=upload.original_name if single_sheet else sheet,
                sheet=sheet,
            )

        async def cleanup() -> None:
            _remove_staging(staging)

        self._launch(job, convert, register, cleanup)
        return job.id

    async def _register_new(
        self,
        workspace_id: str,
        dataset_id: str,
        upload: UploadRecord,
        conv: _Converted,
        *,
        table_base: str,
        sheet: str | None = None,
    ) -> DatasetRecord:
        """Insert Dataset + ``os.replace`` staging → final dalam satu transaksi."""
        rel = self.parquet_rel_path(workspace_id, dataset_id)
        final = self.resolve_parquet_path(rel)
        moved = False
        try:
            async with self.repos.db.transaction():
                existing = await self.repos.datasets.list_table_names(workspace_id)
                dataset = await self.repos.datasets.create(
                    workspace_id,
                    table_name=make_table_name(table_base, existing),
                    source_name=upload.original_name,
                    parquet_path=rel,
                    schema=conv.columns,
                    column_mapping=conv.mapping,
                    row_count=conv.row_count,
                    upload_id=upload.id,
                    sheet_name=sheet,
                    dataset_id=dataset_id,
                )
                await asyncio.to_thread(_replace_with_retry, conv.staging, final)
                moved = True
        except BaseException:
            if moved:  # commit gagal setelah file dipindah
                _unlink_quiet(final)
            raise
        return dataset

    # -- job: re-upload -----------------------------------------------------

    async def start_reupload(
        self,
        workspace_id: str,
        dataset_id: str,
        upload: UploadRecord | str,
        *,
        sheet: str | None = None,
    ) -> str:
        """Mulai job re-upload Dataset (Req 26.1); kembalikan ``job_id``.

        Untuk CSV, header dicek sinkron: kolom hilang/bertambah → upload baru
        dibuang dan ``SchemaMismatch`` (409) di-raise. Ketidakcocokan tipe (dan
        seluruh cek XLSX) dilaporkan job sebagai ``job.failed`` ``SCHEMA_MISMATCH``.
        Untuk XLSX, sheet = ``sheet`` → ``dataset.sheet_name`` → sheet pertama.
        """
        dataset = await self.repos.datasets.get(dataset_id, workspace_id=workspace_id)
        upload = await self._get_upload(workspace_id, upload)
        src = self.upload_path(upload)

        if upload.kind == "csv":
            header = await asyncio.to_thread(_csv_header_names, src)
            if header is not None:
                old_names = [c.name for c in dataset.schema]
                names_diff = SchemaDiff(
                    missing=tuple(n for n in old_names if n not in header),
                    added=tuple(n for n in header if n not in old_names),
                )
                if not names_diff.is_empty:
                    await self._discard_upload(upload)
                    raise SchemaMismatch(names_diff, dataset.table_name)

        job = self._new_job(
            workspace_id, "reupload", upload.id, sheet=sheet, target_dataset_id=dataset.id
        )
        staging = self._staging_path(workspace_id, dataset.id, job.id)
        batch_size = self.batch_size
        kind = upload.kind
        sheet_pref = sheet or dataset.sheet_name
        original_name = upload.original_name

        def convert(progress: Callable[[float], None]) -> _Converted:
            if kind == "csv":
                columns, mapping, rows = convert_csv_to_parquet(
                    src, staging, progress, batch_size=batch_size
                )
                return _Converted(staging, columns, mapping, rows)
            target_sheet = sheet_pref or _sheet_names(src, original_name)[0]
            return _convert_sheet(src, target_sheet, staging, progress, batch_size)

        async def register(conv: _Converted) -> DatasetRecord:
            return await self._register_reupload(workspace_id, dataset.id, upload, conv)

        async def cleanup() -> None:
            _remove_staging(staging)
            await self._discard_upload(upload)

        self._launch(job, convert, register, cleanup)
        return job.id

    async def _register_reupload(
        self, workspace_id: str, dataset_id: str, upload: UploadRecord, conv: _Converted
    ) -> DatasetRecord:
        """``diff_schemas`` → update Dataset (+``data_version``) + ``os.replace`` atomik."""
        async with self.repos.db.transaction():
            current = await self.repos.datasets.get(dataset_id, workspace_id=workspace_id)
            diff = diff_schemas(current.schema, conv.columns)
            if not diff.is_empty:
                raise SchemaMismatch(diff, current.table_name)
            final = self.resolve_parquet_path(current)
            dataset = await self.repos.datasets.update_file(
                dataset_id,
                parquet_path=current.parquet_path,
                schema=conv.columns,
                column_mapping=conv.mapping,
                row_count=conv.row_count,
                upload_id=upload.id,
            )
            await asyncio.to_thread(_replace_with_retry, conv.staging, final)
        return dataset

    # -- status -------------------------------------------------------------

    def get_job_record(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise not_found("Job", job_id)
        return job

    def get_job(self, job_id: str) -> dict[str, Any]:
        """``JobStatus``: ``{status, progress, dataset_id?, error?}``; tidak ada → 404."""
        return self.get_job_record(job_id).to_status()

    def list_jobs(self, workspace_id: str | None = None) -> list[Job]:
        return [
            j for j in self._jobs.values() if workspace_id is None or j.workspace_id == workspace_id
        ]

    async def wait_job(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Tunggu job selesai (untuk test/alat bantu); kembalikan ``JobStatus``."""
        job = self.get_job_record(job_id)
        if job.task is not None and not job.task.done():
            await asyncio.wait_for(asyncio.shield(job.task), timeout)
        return job.to_status()

    async def aclose(self, timeout: float = 10.0) -> None:
        """Batalkan job berjalan (shutdown); file parsial dibersihkan oleh job."""
        pending = [j for j in self._jobs.values() if j.task is not None and not j.task.done()]
        for job in pending:
            job.cancel_event.set()
            assert job.task is not None
            job.task.cancel()
        if pending:
            await asyncio.wait([j.task for j in pending if j.task is not None], timeout=timeout)

    # -- internal: job runner ----------------------------------------------

    def _new_job(
        self,
        workspace_id: str,
        kind: JobKind,
        upload_id: str,
        *,
        sheet: str | None = None,
        target_dataset_id: str | None = None,
    ) -> Job:
        job = Job(
            id=new_id(),
            workspace_id=workspace_id,
            kind=kind,
            upload_id=upload_id,
            sheet=sheet,
            target_dataset_id=target_dataset_id,
        )
        self._jobs[job.id] = job
        return job

    def _launch(
        self,
        job: Job,
        convert: Callable[[Callable[[float], None]], _Converted],
        register: Callable[[_Converted], Awaitable[DatasetRecord]],
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        job.task = asyncio.create_task(
            self._run(job, convert, register, cleanup), name=f"ingest-{job.id}"
        )

    async def _run(
        self,
        job: Job,
        convert: Callable[[Callable[[float], None]], _Converted],
        register: Callable[[_Converted], Awaitable[DatasetRecord]],
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        tracker = _ProgressTracker(job, self._publish_progress)
        try:
            async with self._slots:
                if job.cancel_event.is_set():
                    raise _JobCancelled()
                job.status = "running"
                self._publish_progress(job)
                heartbeat = asyncio.create_task(self._heartbeat(job))
                try:
                    converted = await self._in_thread(job, convert, tracker.update)
                    if job.cancel_event.is_set():
                        _remove_staging(converted.staging)
                        raise _JobCancelled()
                    dataset = await register(converted)
                finally:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
        except BaseException as exc:
            try:
                await cleanup()
            except Exception:
                logger.exception("Pembersihan job ingest %s gagal", job.id)
            self._mark_failed(job, exc)
            if not isinstance(exc, Exception):  # CancelledError, KeyboardInterrupt
                raise
            return

        self._mark_done(job, dataset)
        if self.on_dataset_registered is not None:
            try:
                await self.on_dataset_registered(dataset)
            except Exception:
                logger.exception("Hook on_dataset_registered gagal untuk Dataset %s", dataset.id)

    async def _in_thread(self, job: Job, fn: Callable[..., _T], *args: Any) -> _T:
        """``asyncio.to_thread`` yang menunggu thread berhenti bila task dibatalkan,
        sehingga pembersihan file tidak berlomba dengan penulisan di thread."""
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            job.cancel_event.set()  # thread berhenti di checkpoint progres berikutnya
            with contextlib.suppress(BaseException):
                converted = await fut
                if isinstance(converted, _Converted):
                    _remove_staging(converted.staging)
            raise

    async def _heartbeat(self, job: Job) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            self._publish_progress(job)

    # -- internal: event & status -----------------------------------------

    def _publish(self, workspace_id: str, event_type: str, data: dict[str, Any]) -> None:
        try:
            self.bus.publish(workspace_id, event_type, data)
        except Exception:
            logger.exception("Gagal menerbitkan event %s", event_type)

    def _publish_progress(self, job: Job) -> None:
        self._publish(
            job.workspace_id,
            "job.progress",
            {"job_id": job.id, "progress": round(job.progress, 1)},
        )

    def _mark_done(self, job: Job, dataset: DatasetRecord) -> None:
        job.progress = 100.0
        job.status = "done"
        job.dataset_id = dataset.id
        job.finished_at = _utc_now()
        self._publish_progress(job)
        self._publish(job.workspace_id, "job.done", {"job_id": job.id, "dataset_id": dataset.id})

    def _mark_failed(self, job: Job, exc: BaseException) -> None:
        if isinstance(exc, StudioError):
            error = exc.to_dict()
            logger.info("Job ingest %s gagal: %s", job.id, exc.code)
        elif isinstance(exc, (_JobCancelled, asyncio.CancelledError)):
            error = {
                "code": "CONVERSION_FAILED",
                "message": "Konversi dibatalkan sebelum selesai.",
                "details": {"reason": "cancelled"},
            }
        else:
            logger.error("Job ingest %s gagal", job.id, exc_info=exc)
            error = {
                "code": "CONVERSION_FAILED",
                "message": "Konversi file ke Parquet gagal.",
                "details": {"reason": _reason(exc)},
            }
        job.status = "failed"
        job.error = error
        job.finished_at = _utc_now()
        self._publish(job.workspace_id, "job.failed", {"job_id": job.id, "error": error})

    # -- internal: upload ---------------------------------------------------

    async def _get_upload(
        self,
        workspace_id: str,
        upload: UploadRecord | str,
        *,
        kind: Literal["csv", "xlsx"] | None = None,
    ) -> UploadRecord:
        record = upload if isinstance(upload, UploadRecord) else await self.repos.uploads.get(upload)
        if record.workspace_id != workspace_id:
            raise not_found("Upload", record.id)
        if kind is not None and record.kind != kind:
            raise StudioError(
                "VALIDATION_ERROR",
                f"Upload '{record.original_name}' bukan file .{kind}.",
                {"upload_id": record.id, "kind": record.kind, "expected": kind},
                http_status=422,
            )
        return record

    async def _discard_upload(self, upload: UploadRecord) -> None:
        """Hapus baris ``uploads`` + file asli, kecuali masih dirujuk Dataset."""
        try:
            await self.repos.uploads.delete(upload.id)
        except StudioError as exc:
            if exc.code == "CONFLICT":  # masih dipakai Dataset lain: pertahankan file
                return
            if exc.code != "NOT_FOUND":
                raise
        _unlink_quiet(self.upload_path(upload))
