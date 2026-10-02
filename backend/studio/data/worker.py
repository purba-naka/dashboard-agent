"""Eksekusi query Polars di proses worker terpisah (Req 5.4, 10.6, 22.2, 23.1).

``collect()`` Polars tidak dapat dibatalkan dari thread lain, sehingga
satu-satunya pembatalan yang andal untuk timeout adalah membunuh prosesnya.
``QueryWorkerPool`` menjaga ``min(4, cpu_count)`` proses ``spawn``; setiap job
dikirim sebagai deskripsi JSON (path Parquet, FilterSet, rencana propagasi,
SQL) — bukan objek LazyFrame — lalu worker membangun konteksnya sendiri::

    frames = {t: pl.scan_parquet(path) for t, path in tables.items()}
    frames = propagation.materialize(frames, filter_set, plan)
    lf = pl.SQLContext(frames=frames, eager=False).execute(sql)   # SQL tidak diubah
    df = lf.collect(engine="streaming")                           # hanya hasil akhir

Hasil dikembalikan sebagai Arrow IPC bytes. Timeout → proses di-terminate,
diganti proses baru di latar belakang, dan ``StudioError("QUERY_TIMEOUT")``.

Catatan Windows/pytest: target proses (``_worker_main``) dan ``run_job`` adalah
fungsi level modul sehingga dapat di-pickle oleh konteks ``spawn`` tanpa
bergantung pada guard ``__main__`` pemanggil. Pesan antar-proses hanya berisi
tipe primitif (dict/list/str/int/bytes).

``InlineQueryRunner`` menyediakan API yang sama di dalam proses (thread) untuk
test atau lingkungan tanpa biaya spawn; timeout-nya hanya melepas pemanggil —
komputasi di thread tetap berjalan sampai selesai.
"""

from __future__ import annotations

import asyncio
import io
import logging
import multiprocessing as mp
import os
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal

import polars as pl

from studio.api.errors import StudioError
from studio.core.filters import dump_filter_set, parse_filter_set
from studio.core.propagation import PropagationPlan, materialize

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "QueryMode",
    "QueryJob",
    "QueryOutput",
    "QueryWorkerPool",
    "InlineQueryRunner",
    "default_pool_size",
    "run_job",
]

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
#: Batas waktu proses worker baru siap (impor Polars dsb.).
STARTUP_TIMEOUT_S = 120.0

QueryMode = Literal["execute", "schema"]
_MODES: frozenset[str] = frozenset({"execute", "schema"})


def default_pool_size() -> int:
    return max(1, min(4, os.cpu_count() or 1))


# ---------------------------------------------------------------------------
# Job & hasil
# ---------------------------------------------------------------------------


@dataclass
class QueryJob:
    """Deskripsi job yang JSON-serializable.

    ``filters`` menerima list dict JSON, model Pydantic, atau ``Predicate``
    frozen dan dinormalisasi ke bentuk JSON kanonik; ``plan`` menerima
    ``PropagationPlan`` atau dict hasil ``to_dict``. ``mode="schema"`` hanya
    menghitung skema output (validasi V7) tanpa eksekusi.
    """

    tables: dict[str, str]
    sql: str
    filters: list[dict[str, Any]] = field(default_factory=list)
    plan: dict[str, Any] | None = None
    mode: QueryMode = "execute"
    row_limit: int | None = None

    def __post_init__(self) -> None:
        self.tables = {str(k): str(Path(v)) for k, v in dict(self.tables).items()}
        self.sql = str(self.sql)
        self.filters = dump_filter_set(parse_filter_set(list(self.filters or ())))
        if isinstance(self.plan, PropagationPlan):
            self.plan = self.plan.to_dict()
        elif self.plan is not None:
            self.plan = PropagationPlan.from_dict(self.plan).to_dict()
        if self.mode not in _MODES:
            raise ValueError(f"mode tidak dikenal: {self.mode!r}")
        if self.row_limit is not None:
            self.row_limit = int(self.row_limit)
            if self.row_limit < 0:
                raise ValueError("row_limit tidak boleh negatif")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": dict(self.tables),
            "sql": self.sql,
            "filters": [dict(f) for f in self.filters],
            "plan": self.plan,
            "mode": self.mode,
            "row_limit": self.row_limit,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> QueryJob:
        return cls(
            tables=dict(d["tables"]),
            sql=d["sql"],
            filters=list(d.get("filters") or ()),
            plan=d.get("plan"),
            mode=d.get("mode", "execute"),
            row_limit=d.get("row_limit"),
        )


@dataclass(frozen=True)
class QueryOutput:
    """Hasil job: skema ``[(nama, dtype)]`` dan (mode execute) Arrow IPC bytes."""

    schema: list[tuple[str, str]]
    ipc: bytes | None = None
    #: Jumlah baris yang dikembalikan (``None`` untuk mode schema).
    row_count: int | None = None
    #: ``True`` bila hasil dipotong oleh ``row_limit``.
    truncated: bool = False
    elapsed_s: float = 0.0

    @property
    def columns(self) -> list[dict[str, str]]:
        return [{"name": n, "type": t} for n, t in self.schema]

    def to_frame(self) -> pl.DataFrame:
        """Decode IPC → ``pl.DataFrame`` (mode schema → frame kosong berkolom)."""
        if self.ipc is None:
            return pl.DataFrame({n: [] for n, _ in self.schema})
        return pl.read_ipc(io.BytesIO(self.ipc), memory_map=False)


def _schema_list(schema: Mapping[str, pl.DataType]) -> list[tuple[str, str]]:
    return [(name, str(dtype)) for name, dtype in schema.items()]


# ---------------------------------------------------------------------------
# Eksekusi (berjalan di proses worker; juga dipakai InlineQueryRunner)
# ---------------------------------------------------------------------------


def _build_lazy(job: QueryJob) -> pl.LazyFrame:
    frames: dict[str, pl.LazyFrame] = {
        name: pl.scan_parquet(path) for name, path in job.tables.items()
    }
    if job.filters or job.plan:
        plan = PropagationPlan.from_dict(job.plan) if job.plan else PropagationPlan()
        frames = materialize(frames, parse_filter_set(job.filters), plan)
    ctx = pl.SQLContext(frames=frames, eager=False)
    return ctx.execute(job.sql)  # teks SQL tidak diubah (Req 22.2)


def _error(kind: str, exc: BaseException, **extra: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"kind": kind, "type": type(exc).__name__, "message": str(exc), **extra},
    }


def run_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Jalankan satu job dan kembalikan respons primitif (tidak pernah raise).

    Respons sukses: ``{"ok": True, "schema", "ipc", "row_count", "truncated", "elapsed_s"}``.
    Respons gagal: ``{"ok": False, "error": {"kind", "type", "message", ...}}`` dengan
    ``kind`` ∈ ``studio`` (StudioError, mis. INVALID_FILTER), ``polars``, ``internal``.
    """
    started = time.perf_counter()
    try:
        job = QueryJob.from_dict(payload)
        lf = _build_lazy(job)
        if job.mode == "schema":
            schema = lf.collect_schema()
            return {
                "ok": True,
                "schema": _schema_list(schema),
                "ipc": None,
                "row_count": None,
                "truncated": False,
                "elapsed_s": time.perf_counter() - started,
            }
        truncated = False
        if job.row_limit is not None:
            lf = lf.limit(job.row_limit + 1)
        df = lf.collect(engine="streaming")  # hanya hasil akhir yang dimaterialisasi (Req 5.4)
        if job.row_limit is not None and df.height > job.row_limit:
            df = df.head(job.row_limit)
            truncated = True
        buf = io.BytesIO()
        df.write_ipc(buf)
        return {
            "ok": True,
            "schema": _schema_list(df.schema),
            "ipc": buf.getvalue(),
            "row_count": df.height,
            "truncated": truncated,
            "elapsed_s": time.perf_counter() - started,
        }
    except StudioError as exc:
        return _error(
            "studio",
            exc,
            code=exc.code,
            details=exc.details,
            http_status=exc.http_status,
        )
    except pl.exceptions.PolarsError as exc:
        return _error("polars", exc)
    except Exception as exc:  # noqa: BLE001 — worker tidak boleh crash
        return _error("internal", exc)


def _to_output(resp: Mapping[str, Any]) -> QueryOutput:
    """Respons worker → ``QueryOutput`` atau raise ``StudioError`` terstruktur."""
    if resp.get("ok"):
        return QueryOutput(
            schema=[(str(n), str(t)) for n, t in resp["schema"]],
            ipc=resp.get("ipc"),
            row_count=resp.get("row_count"),
            truncated=bool(resp.get("truncated")),
            elapsed_s=float(resp.get("elapsed_s") or 0.0),
        )
    err = dict(resp.get("error") or {})
    kind = err.get("kind")
    message = str(err.get("message") or "Query gagal.")
    if kind == "studio":
        raise StudioError(
            str(err.get("code") or "QUERY_FAILED"),
            message,
            err.get("details") or {},
            int(err.get("http_status") or 400),
        )
    if kind == "polars":
        raise StudioError(
            "POLARS_ERROR", message, {"error_type": err.get("type")}, http_status=422
        )
    raise StudioError(
        "QUERY_FAILED", message, {"error_type": err.get("type")}, http_status=500
    )


def _timeout_error(timeout_s: float) -> StudioError:
    return StudioError(
        "QUERY_TIMEOUT",
        f"Query melebihi batas waktu {timeout_s:g} detik dan dihentikan.",
        {"timeout_s": timeout_s},
        http_status=504,
    )


def _closed_error() -> StudioError:
    return StudioError("POOL_CLOSED", "Query worker pool tidak aktif.", http_status=503)


# ---------------------------------------------------------------------------
# Proses worker
# ---------------------------------------------------------------------------


def _worker_main(conn: Connection) -> None:
    """Entry proses worker (level modul agar dapat di-pickle oleh ``spawn``)."""
    try:
        conn.send(("ready", os.getpid()))
        while True:
            try:
                msg = conn.recv()
            except (EOFError, OSError):
                break  # proses induk hilang
            if msg is None:
                break
            try:
                resp = run_job(msg)
            except BaseException as exc:  # noqa: BLE001 — pertahankan loop worker
                resp = _error("internal", exc)
            try:
                conn.send(resp)
            except (OSError, ValueError):
                break
    except KeyboardInterrupt:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class _Worker:
    __slots__ = ("process", "conn")

    def __init__(self, process: mp.process.BaseProcess, conn: Connection) -> None:
        self.process = process
        self.conn = conn

    @property
    def pid(self) -> int | None:
        return self.process.pid

    def kill(self) -> None:
        """Terminate paksa (tidak menunggu job selesai)."""
        try:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(5)
        except (OSError, ValueError):
            pass
        self._close_conn()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Hentikan dengan sopan (sentinel ``None``), fallback terminate."""
        try:
            self.conn.send(None)
        except (OSError, ValueError):
            pass
        try:
            self.process.join(timeout)
        except (OSError, ValueError):
            pass
        self.kill()

    def _close_conn(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------


class QueryWorkerPool:
    """Pool proses ``spawn`` untuk eksekusi query dengan timeout yang dapat dibatalkan.

    ``run`` mengambil worker idle, mengirim job, lalu menunggu respons dengan
    polling non-blocking. Bila melewati ``timeout_s`` (atau task dibatalkan)
    proses worker di-terminate dan diganti di latar belakang.
    """

    def __init__(
        self,
        size: int | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        *,
        startup_timeout_s: float = STARTUP_TIMEOUT_S,
    ) -> None:
        self.size = default_pool_size() if size is None else max(1, int(size))
        self.timeout_s = float(timeout_s)
        self.startup_timeout_s = float(startup_timeout_s)
        self._ctx = mp.get_context("spawn")
        self._start_lock = threading.Lock()
        self._idle: asyncio.Queue[_Worker | None] | None = None
        self._workers: set[_Worker] = set()
        self._pending: set[asyncio.Task[None]] = set()
        self._started = False
        self._closed = False

    # -- siklus hidup -------------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    @property
    def worker_pids(self) -> list[int]:
        return sorted(w.pid for w in self._workers if w.pid is not None)

    async def start(self) -> None:
        if self._started:
            return
        self._idle = asyncio.Queue()
        self._started = True
        workers = await asyncio.gather(
            *(asyncio.to_thread(self._spawn_worker) for _ in range(self.size)),
            return_exceptions=True,
        )
        errors = [w for w in workers if isinstance(w, BaseException)]
        for w in workers:
            if isinstance(w, _Worker):
                self._workers.add(w)
                self._idle.put_nowait(w)
        if errors:
            await self.close()
            raise errors[0]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in list(self._pending):
            task.cancel()
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)
        workers = list(self._workers)
        self._workers.clear()
        if workers:
            await asyncio.gather(
                *(asyncio.to_thread(w.shutdown) for w in workers), return_exceptions=True
            )
        if self._idle is not None:
            self._idle.put_nowait(None)  # bangunkan penunggu → POOL_CLOSED

    async def __aenter__(self) -> QueryWorkerPool:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _spawn_worker(self) -> _Worker:
        """Mulai satu proses dan tunggu sinyal siap (blocking; jalankan di thread)."""
        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        process = self._ctx.Process(
            target=_worker_main, args=(child_conn,), name="studio-query-worker", daemon=True
        )
        with self._start_lock:
            process.start()
        child_conn.close()  # EOF di induk saat proses anak mati
        worker = _Worker(process, parent_conn)
        try:
            if not parent_conn.poll(self.startup_timeout_s):
                raise TimeoutError("worker tidak siap tepat waktu")
            msg = parent_conn.recv()
            if not (isinstance(msg, tuple) and msg and msg[0] == "ready"):
                raise RuntimeError(f"sinyal siap tidak valid: {msg!r}")
        except BaseException as exc:
            worker.kill()
            raise StudioError(
                "WORKER_START_FAILED",
                f"Gagal memulai query worker: {exc}",
                http_status=500,
            ) from exc
        return worker

    # -- penggantian worker -------------------------------------------------

    def _replace(self, worker: _Worker) -> None:
        """Buang ``worker`` (terminate) dan jadwalkan proses pengganti."""
        self._workers.discard(worker)
        task = asyncio.get_running_loop().create_task(self._replace_task(worker))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _replace_task(self, old: _Worker) -> None:
        await asyncio.to_thread(old.kill)
        if self._closed:
            return
        try:
            new = await asyncio.to_thread(self._spawn_worker)
        except Exception:
            log.exception("Gagal mengganti query worker; ukuran pool berkurang.")
            if not self._workers and not self._pending_others() and self._idle is not None:
                self._idle.put_nowait(None)  # tidak ada worker tersisa
            return
        if self._closed:
            await asyncio.to_thread(new.shutdown)
            return
        self._workers.add(new)
        assert self._idle is not None
        self._idle.put_nowait(new)

    def _pending_others(self) -> bool:
        current = asyncio.current_task()
        return any(t is not current and not t.done() for t in self._pending)

    # -- eksekusi -------------------------------------------------------------

    async def _acquire(self) -> _Worker:
        if not self._started:
            await self.start()
        if self._closed or self._idle is None:
            raise _closed_error()
        while True:
            worker = await self._idle.get()
            if worker is not None:
                return worker
            if not self._closed and (
                self._workers or any(not t.done() for t in self._pending)
            ):
                continue  # sentinel basi: masih ada worker / pengganti
            self._idle.put_nowait(None)  # teruskan ke penunggu lain
            if self._closed:
                raise _closed_error()
            raise StudioError(
                "WORKER_UNAVAILABLE", "Tidak ada query worker yang aktif.", http_status=503
            )

    async def run(self, job: QueryJob, timeout_s: float | None = None) -> QueryOutput:
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        payload = job.to_dict()
        worker = await self._acquire()
        healthy = False
        try:
            try:
                worker.conn.send(payload)
            except (OSError, ValueError) as exc:
                raise StudioError(
                    "QUERY_FAILED", f"Query worker tidak dapat dihubungi: {exc}", http_status=500
                ) from exc
            deadline = time.monotonic() + timeout
            delay = 0.001
            while not worker.conn.poll():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _timeout_error(timeout)
                await asyncio.sleep(min(delay, remaining))
                delay = min(delay * 2, 0.02)
            try:
                resp = await asyncio.to_thread(worker.conn.recv)
            except (EOFError, OSError) as exc:
                raise StudioError(
                    "QUERY_FAILED", "Query worker berhenti tidak terduga.", http_status=500
                ) from exc
            healthy = True
        finally:
            # Timeout, crash, atau pembatalan task → proses mungkin masih sibuk.
            if not healthy:
                self._replace(worker)
            elif self._closed:
                await asyncio.to_thread(worker.shutdown)
            else:
                assert self._idle is not None
                self._idle.put_nowait(worker)
        return _to_output(resp)


# ---------------------------------------------------------------------------
# Runner in-process
# ---------------------------------------------------------------------------


class InlineQueryRunner:
    """API sama dengan ``QueryWorkerPool`` tetapi berjalan di thread proses ini.

    Timeout hanya melepas pemanggil (``QUERY_TIMEOUT``); komputasi Polars di
    thread tidak dapat dihentikan dan tetap berjalan hingga selesai.
    """

    def __init__(self, size: int | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self.size = default_pool_size() if size is None else max(1, int(size))
        self.timeout_s = float(timeout_s)
        self._sem: asyncio.Semaphore | None = None
        self._closed = False

    @property
    def started(self) -> bool:
        return self._sem is not None and not self._closed

    @property
    def worker_pids(self) -> list[int]:
        return [os.getpid()]

    async def start(self) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.size)

    async def close(self) -> None:
        self._closed = True

    async def __aenter__(self) -> InlineQueryRunner:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def run(self, job: QueryJob, timeout_s: float | None = None) -> QueryOutput:
        if self._closed:
            raise _closed_error()
        await self.start()
        assert self._sem is not None
        timeout = self.timeout_s if timeout_s is None else float(timeout_s)
        payload = job.to_dict()
        async with self._sem:
            try:
                resp = await asyncio.wait_for(asyncio.to_thread(run_job, payload), timeout)
            except (asyncio.TimeoutError, TimeoutError):
                raise _timeout_error(timeout) from None
        return _to_output(resp)
