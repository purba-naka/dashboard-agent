"""Koneksi Metadata_Store (SQLite via aiosqlite), runner migrasi, helper transaksi.

Metadata_Store adalah satu file SQLite lokal ``DATA_DIR/studio.db`` (Req 28.1).
Seluruh state dipulihkan dari file ini saat startup (Req 28.4).

Pola pemakaian::

    db = Database.from_settings(settings)
    await db.open()          # buka koneksi bersama + jalankan migrasi tertunda
    async with db.transaction() as conn:
        await conn.execute("INSERT INTO workspaces ...", (...))
    row = await db.fetch_one("SELECT * FROM workspaces WHERE id = ?", (ws_id,))
    await db.close()

Catatan desain:

* Koneksi dibuka dengan ``isolation_level=None`` (autocommit) sehingga transaksi
  dikendalikan eksplisit lewat ``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``.
* ``PRAGMA journal_mode = WAL`` tidak dapat diubah di dalam transaksi dan
  ``PRAGMA foreign_keys`` berlaku per koneksi, sehingga keduanya dijalankan
  setiap kali koneksi dibuka. Statement PRAGMA di file migrasi dijalankan di
  luar transaksi; sisanya dijalankan atomik bersama pencatatan versi di
  ``schema_migrations``.
* Koneksi bersama dipakai bergantian oleh banyak coroutine; ``transaction()``
  dan helper ``execute/fetch_*`` diserialkan dengan ``asyncio.Lock`` agar
  statement coroutine lain tidak ikut masuk ke transaksi yang sedang berjalan.
  Di dalam ``transaction()`` gunakan koneksi yang di-yield (atau helper
  ``db.execute/fetch_*`` yang otomatis ikut transaksi aktif task tersebut).
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

if TYPE_CHECKING:
    from studio.config import Settings

DB_FILENAME = "studio.db"
MIGRATIONS_PACKAGE = "studio.store.migrations"
_MIGRATION_NAME_RE = re.compile(r"^(\d+)_[A-Za-z0-9_\-]+\.sql$")

Params = Sequence[Any] | dict[str, Any]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_migrations(package: str = MIGRATIONS_PACKAGE) -> list[Migration]:
    """Muat file ``NNN_nama.sql`` dari paket migrasi (importlib.resources), urut versi."""
    migrations: list[Migration] = []
    seen: dict[int, str] = {}
    for entry in resources.files(package).iterdir():
        match = _MIGRATION_NAME_RE.match(entry.name)
        if not match or not entry.is_file():
            continue
        version = int(match.group(1))
        if version in seen:
            raise RuntimeError(
                f"Versi migrasi duplikat {version}: {seen[version]} dan {entry.name}"
            )
        seen[version] = entry.name
        migrations.append(
            Migration(version=version, name=entry.name, sql=entry.read_text(encoding="utf-8"))
        )
    migrations.sort(key=lambda m: m.version)
    return migrations


def split_sql_statements(script: str) -> list[str]:
    """Pecah skrip SQL menjadi statement lengkap (sadar komentar & string literal)."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            stmt = buffer.strip()
            buffer = ""
            if _strip_sql_comments(stmt):
                statements.append(stmt)
    if _strip_sql_comments(buffer.strip()):
        raise ValueError(f"Statement SQL tidak lengkap di akhir skrip: {buffer.strip()[:80]!r}")
    return statements


def _strip_sql_comments(stmt: str) -> str:
    lines = [ln for ln in stmt.splitlines() if not ln.strip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";").strip()


def _is_pragma(stmt: str) -> bool:
    return _strip_sql_comments(stmt).upper().startswith("PRAGMA")


async def _configure_connection(conn: aiosqlite.Connection) -> None:
    """Pragma per koneksi: WAL, foreign key, busy timeout; row_factory = Row."""
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA busy_timeout = 5000")


async def _connect(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(path), isolation_level=None)
    try:
        await _configure_connection(conn)
    except BaseException:
        await conn.close()
        raise
    return conn


async def migrate(
    conn: aiosqlite.Connection, migrations: Iterable[Migration] | None = None
) -> list[int]:
    """Terapkan migrasi tertunda secara berurutan; idempoten.

    Setiap migrasi dijalankan dalam ``BEGIN IMMEDIATE`` bersama baris
    ``schema_migrations`` sehingga gagal = rollback penuh. Versi yang sudah
    tercatat dilewati (dicek ulang di dalam transaksi agar aman bila dua
    proses bermigrasi bersamaan). Mengembalikan daftar versi yang diterapkan.
    """
    pending = sorted(migrations if migrations is not None else load_migrations(),
                     key=lambda m: m.version)
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL"
        ")"
    )
    applied_now: list[int] = []
    for migration in pending:
        statements = split_sql_statements(migration.sql)
        # PRAGMA (mis. journal_mode) tidak boleh di dalam transaksi.
        for stmt in statements:
            if _is_pragma(stmt):
                await conn.execute(stmt)
        body = [s for s in statements if not _is_pragma(s)]

        await conn.execute("BEGIN IMMEDIATE")
        try:
            async with conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (migration.version,)
            ) as cur:
                already = await cur.fetchone() is not None
            if already:
                await conn.execute("COMMIT")
                continue
            for stmt in body:
                await conn.execute(stmt)
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (migration.version, _utc_now_iso()),
            )
            await conn.execute("COMMIT")
        except BaseException:
            await conn.execute("ROLLBACK")
            raise
        applied_now.append(migration.version)
    return applied_now


class Database:
    """Metadata_Store SQLite dengan satu koneksi bersama dan helper transaksi."""

    def __init__(self, path: Path | str) -> None:
        self.path: Path = Path(path).expanduser().resolve()
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        # Koneksi transaksi aktif milik task/konteks saat ini (untuk nesting & helper).
        self._tx_conn: ContextVar[aiosqlite.Connection | None] = ContextVar(
            f"studio_db_tx_{id(self)}", default=None
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(Path(settings.data_dir) / DB_FILENAME)

    # ------------------------------------------------------------------ lifecycle
    @property
    def is_open(self) -> bool:
        return self._conn is not None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database belum dibuka; panggil `await db.open()`")
        return self._conn

    async def open(self, *, run_migrations: bool = True) -> Database:
        """Buka koneksi bersama (idempoten) dan jalankan migrasi tertunda."""
        if self._conn is None:
            self._conn = await _connect(self.path)
            if run_migrations:
                try:
                    async with self._lock:
                        await migrate(self._conn)
                except BaseException:
                    await self.close()
                    raise
        return self

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            await conn.close()

    async def __aenter__(self) -> Database:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def migrate(self) -> list[int]:
        """Jalankan migrasi tertunda pada koneksi bersama."""
        async with self._lock:
            return await migrate(self.conn)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Koneksi baru terpisah (pragma sudah disetel), ditutup saat keluar konteks.

        Berguna untuk pembacaan paralel/terisolasi atau alat bantu; operasi
        aplikasi biasa sebaiknya memakai ``transaction()`` / helper ``fetch_*``.
        """
        conn = await _connect(self.path)
        try:
            yield conn
        finally:
            await conn.close()

    # ---------------------------------------------------------------- transaksi
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """``BEGIN IMMEDIATE`` → commit bila sukses, rollback bila exception.

        Transaksi bersarang dalam task yang sama bergabung ke transaksi luar.
        """
        current = self._tx_conn.get()
        if current is not None:
            yield current
            return
        async with self._lock:
            conn = self.conn
            await conn.execute("BEGIN IMMEDIATE")
            token = self._tx_conn.set(conn)
            try:
                yield conn
            except BaseException:
                await conn.execute("ROLLBACK")
                raise
            else:
                await conn.execute("COMMIT")
            finally:
                self._tx_conn.reset(token)

    # ------------------------------------------------------------------ helpers
    @asynccontextmanager
    async def _use(self) -> AsyncIterator[aiosqlite.Connection]:
        current = self._tx_conn.get()
        if current is not None:
            yield current
            return
        async with self._lock:
            yield self.conn

    async def execute(self, sql: str, params: Params = ()) -> int:
        """Jalankan satu statement; kembalikan ``rowcount``."""
        async with self._use() as conn:
            async with conn.execute(sql, params) as cur:
                return cur.rowcount

    async def fetch_one(self, sql: str, params: Params = ()) -> aiosqlite.Row | None:
        async with self._use() as conn:
            async with conn.execute(sql, params) as cur:
                return await cur.fetchone()

    async def fetch_all(self, sql: str, params: Params = ()) -> list[aiosqlite.Row]:
        async with self._use() as conn:
            async with conn.execute(sql, params) as cur:
                return list(await cur.fetchall())

    async def fetch_value(self, sql: str, params: Params = ()) -> Any:
        row = await self.fetch_one(sql, params)
        return None if row is None else row[0]


def row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    """Konversi ``aiosqlite.Row`` ke ``dict`` (None tetap None)."""
    return None if row is None else {k: row[k] for k in row.keys()}


__all__ = [
    "DB_FILENAME",
    "Database",
    "Migration",
    "load_migrations",
    "migrate",
    "row_to_dict",
    "split_sql_statements",
]
