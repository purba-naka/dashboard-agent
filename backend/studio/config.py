"""Konfigurasi aplikasi (pydantic-settings).

Nilai dibaca dari environment variable (case-insensitive): ``HOST``, ``PORT``,
``DATA_DIR``, ``CORS_ORIGINS``, ``MODEL_*``, serta opsi runtime opsional
``QUERY_TIMEOUT_S``, ``QUERY_WORKERS``, ``QUERY_RUNNER_MODE``,
``MAX_UPLOAD_BYTES``, dan ``SSE_HEARTBEAT_SECONDS``. File ``.env`` dimuat oleh
``main.py`` (bukan di sini) agar test tetap deterministik.

Nilai environment kosong (mis. ``DATA_DIR=`` di ``.env``) dianggap tidak diset
sehingga default dipakai.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"
#: Batas minimum (dan default) ukuran file upload: 1 GiB (Req 5.1).
MIN_MAX_UPLOAD_BYTES = 1 << 30


class Settings(BaseSettings):
    """Pengaturan runtime Backend_API."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
    )

    # Bind default ke loopback (Req 29.1).
    host: str = "127.0.0.1"
    port: int = 8000
    # Selalu absolut; path relatif di-resolve terhadap direktori `backend/`.
    data_dir: Path = DEFAULT_DATA_DIR
    # Env berupa daftar dipisah koma, mis. "http://localhost:3000,http://127.0.0.1:3000".
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    # Metadata_Store: kosong = SQLite `DATA_DIR/studio.db`; `postgresql://...` = PostgreSQL
    # (skema dikelola Alembic: `alembic upgrade head`).
    database_url: str | None = None

    # Model LLM mentah (format LiteLLM). Resolusi & validasi dilakukan terpisah.
    model_default: str | None = None
    model_root: str | None = None
    model_profiler: str | None = None
    model_query: str | None = None
    model_chart: str | None = None
    model_insight: str | None = None
    model_semantic: str | None = None
    model_architect: str | None = None

    # Anggaran karakter blok konteks semantik per agent (Req 33.3).
    semantic_context_budget: int = Field(default=12_000, ge=500)
    # Batas waktu pengayaan LLM Semantic_Drafter (detik, Req 32.7).
    semantic_draft_timeout_s: float = Field(default=60.0, gt=0)

    # Data_Engine: timeout eksekusi query (detik) dan jumlah proses worker
    # (None = default pool, lihat `studio.data.worker.default_pool_size`).
    query_timeout_s: float = Field(default=60.0, gt=0)
    query_workers: int | None = Field(default=None, ge=1)
    # "process" = QueryWorkerPool (spawn, timeout dapat membatalkan query);
    # "inline" = InlineQueryRunner di thread (untuk test / lingkungan terbatas).
    query_runner_mode: Literal["process", "inline"] = "process"
    # Batas ukuran file upload (byte); nilai di bawah 1 GiB dinaikkan ke 1 GiB (Req 5.1).
    max_upload_bytes: int = MIN_MAX_UPLOAD_BYTES
    # Interval komentar heartbeat SSE (detik).
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)

    @field_validator("max_upload_bytes", mode="after")
    @classmethod
    def _enforce_min_upload(cls, v: int) -> int:
        return max(int(v), MIN_MAX_UPLOAD_BYTES)

    @field_validator("query_runner_mode", mode="before")
    @classmethod
    def _normalize_runner_mode(cls, v: object) -> object:
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("host", mode="before")
    @classmethod
    def _strip_host(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            return v or "127.0.0.1"
        return v

    @field_validator("data_dir", mode="before")
    @classmethod
    def _default_blank_data_dir(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return DEFAULT_DATA_DIR
        return v

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute_data_dir(cls, v: Path) -> Path:
        v = v.expanduser()
        if not v.is_absolute():
            v = BACKEND_DIR / v
        return v.resolve()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator(
        "model_default",
        "model_root",
        "model_profiler",
        "model_query",
        "model_chart",
        "model_insight",
        "model_semantic",
        "model_architect",
        mode="before",
    )
    @classmethod
    def _blank_model_is_none(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip() or None
        return v


def is_loopback_host(host: str) -> bool:
    """True bila `host` adalah alamat loopback (`localhost`, `127.x.x.x`, `::1`)."""
    h = host.strip().strip("[]").lower()
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def non_loopback_warning(host: str) -> str | None:
    """Pesan peringatan startup bila host bukan loopback (Req 29.2), selain itu None."""
    if is_loopback_host(host):
        return None
    return (
        f"PERINGATAN: Backend_API di-bind ke host '{host}' yang bukan loopback. "
        "API ini tidak memiliki autentikasi dan dapat diakses dari jaringan."
    )
