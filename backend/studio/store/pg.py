"""Metadata_Store PostgreSQL (asyncpg) dengan antarmuka sama dengan ``db.Database``.

Repositori menulis SQL bergaya SQLite (placeholder ``?``); adapter ini
menerjemahkan ``?`` → ``$n`` (di luar string literal) dan memetakan
pelanggaran constraint ke ``sqlite3.IntegrityError`` agar penerjemah error
repositori tetap berlaku. Skema dikelola Alembic, bukan oleh ``open()``.

Kolom disimpan sebagai TEXT/INTEGER/DOUBLE PRECISION seperti di SQLite
(timestamp = string ISO, JSON = string) sehingga kode repositori tidak berubah.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

import asyncpg

from studio.store.db import Params


@lru_cache(maxsize=512)
def to_pg_sql(sql: str) -> str:
    """``?`` → ``$1, $2, ...``; ``?`` di dalam literal ``'...'`` dibiarkan."""
    out: list[str] = []
    n = 0
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str  # '' (escape) membalik dua kali → tetap benar
        if ch == "?" and not in_str:
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


def _args(params: Params) -> list[Any]:
    if isinstance(params, Mapping):
        raise TypeError("Parameter bernama tidak didukung; gunakan placeholder '?'.")
    return list(params)


def _command_rowcount(status: str) -> int:
    """``'UPDATE 3'`` / ``'INSERT 0 1'`` → 3 / 1."""
    tail = status.rsplit(" ", 1)[-1]
    return int(tail) if tail.isdigit() else 0


@asynccontextmanager
async def _integrity() -> AsyncIterator[None]:
    try:
        yield
    except asyncpg.UniqueViolationError as exc:
        raise sqlite3.IntegrityError(f"UNIQUE constraint failed: {exc}") from exc
    except asyncpg.ForeignKeyViolationError as exc:
        raise sqlite3.IntegrityError(f"FOREIGN KEY constraint failed: {exc}") from exc
    except asyncpg.CheckViolationError as exc:
        raise sqlite3.IntegrityError(f"CHECK constraint failed: {exc}") from exc
    except asyncpg.NotNullViolationError as exc:
        raise sqlite3.IntegrityError(f"NOT NULL constraint failed: {exc}") from exc


class PgDatabase:
    """Pool asyncpg; transaksi per task lewat ``ContextVar`` (bersarang = gabung)."""

    def __init__(self, url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        # SQLAlchemy-style URL (postgresql+asyncpg://) juga diterima.
        self.url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
        self._min, self._max = min_size, max_size
        self._pool: asyncpg.Pool | None = None
        self._tx_conn: ContextVar[asyncpg.Connection | None] = ContextVar(
            f"studio_pg_tx_{id(self)}", default=None
        )

    @property
    def is_open(self) -> bool:
        return self._pool is not None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Database belum dibuka; panggil `await db.open()`")
        return self._pool

    async def open(self, *, run_migrations: bool = False) -> PgDatabase:
        """Buka pool. Skema tidak dibuat di sini: jalankan ``alembic upgrade head``."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.url, min_size=self._min, max_size=self._max)
            missing = await self._pool.fetchval("SELECT to_regclass('public.workspaces') IS NULL")
            if missing:
                await self.close()
                raise RuntimeError(
                    "Skema PostgreSQL belum ada. Jalankan `alembic upgrade head` di folder backend."
                )
        return self

    async def close(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            await pool.close()

    async def __aenter__(self) -> PgDatabase:
        return await self.open()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        current = self._tx_conn.get()
        if current is not None:
            yield current
            return
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                token = self._tx_conn.set(conn)
                try:
                    yield conn
                finally:
                    self._tx_conn.reset(token)

    @asynccontextmanager
    async def _use(self) -> AsyncIterator[asyncpg.Connection]:
        current = self._tx_conn.get()
        if current is not None:
            yield current
            return
        async with self.pool.acquire() as conn:
            yield conn

    async def execute(self, sql: str, params: Params = ()) -> int:
        async with self._use() as conn, _integrity():
            return _command_rowcount(await conn.execute(to_pg_sql(sql), *_args(params)))

    async def fetch_one(self, sql: str, params: Params = ()) -> asyncpg.Record | None:
        async with self._use() as conn, _integrity():
            return await conn.fetchrow(to_pg_sql(sql), *_args(params))

    async def fetch_all(self, sql: str, params: Params = ()) -> list[asyncpg.Record]:
        async with self._use() as conn, _integrity():
            return list(await conn.fetch(to_pg_sql(sql), *_args(params)))

    async def fetch_value(self, sql: str, params: Params = ()) -> Any:
        row = await self.fetch_one(sql, params)
        return None if row is None else row[0]


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and url.split("://", 1)[0].split("+", 1)[0] in ("postgresql", "postgres")


__all__ = ["PgDatabase", "is_postgres_url", "to_pg_sql"]
