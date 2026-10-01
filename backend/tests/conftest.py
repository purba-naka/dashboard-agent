"""Konfigurasi pytest bersama untuk seluruh test backend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from hypothesis import settings

# Profil Hypothesis default proyek: minimal 100 contoh per property.
# Bisa diganti via env HYPOTHESIS_PROFILE (mis. "dev" untuk iterasi cepat).
settings.register_profile("studio", max_examples=100)
settings.register_profile("dev", max_examples=20)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "studio"))


@pytest.fixture(autouse=True)
def _no_dev_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """litellm memuat `.env` saat import; jangan biarkan test menyentuh DB dev."""
    monkeypatch.delenv("DATABASE_URL", raising=False)


async def reset_pg(db) -> None:
    """Kosongkan semua tabel public kecuali `alembic_version` (termasuk tabel sesi ADK)."""
    tables = await db.fetch_all(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename <> 'alembic_version'"
    )
    names = ", ".join(f'"{t["tablename"]}"' for t in tables)
    await db.execute(f"TRUNCATE {names} CASCADE")


@pytest.fixture
async def metadata_db(tmp_path: Path):
    """Metadata_Store: SQLite sementara, atau Postgres bila `TEST_DATABASE_URL` diset.

    DB Postgres test harus sudah di-`alembic upgrade head`; semua tabel di-truncate.
    """
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        from studio.store.pg import PgDatabase

        db = await PgDatabase(url).open()
        await reset_pg(db)
    else:
        from studio.store.db import Database

        db = await Database(tmp_path / "studio.db").open()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Direktori DATA_DIR sementara yang terisolasi per test.

    Membuat `<tmp>/data/uploads/` dan mengarahkan env `DATA_DIR` ke sana
    sehingga Settings/komponen penyimpanan tidak menyentuh `data/` asli.
    """
    data_dir = tmp_path / "data"
    (data_dir / "uploads").mkdir(parents=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    return data_dir
