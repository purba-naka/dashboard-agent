"""Session_Service ADK pulih dari koneksi aiosqlite yang mati di pool.

Regresi: stream chat yang dibatalkan di tengah query mematikan koneksi pooled, lalu
setiap giliran berikutnya gagal ``sqlite3.OperationalError: no active connection``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from studio.agents.runner import APP_NAME, USER_ID, build_session_service


@pytest.mark.asyncio
async def test_session_service_recovers_from_dead_pooled_connection(tmp_path: Path) -> None:
    service = build_session_service(tmp_path)
    engine = service.db_engine

    created = await service.create_session(app_name=APP_NAME, user_id=USER_ID)

    # Matikan koneksi DBAPI di bawah pool tanpa memberi tahu pool (efek pembatalan task).
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        raw = await conn.get_raw_connection()
        await raw.driver_connection.close()

    got = await service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=created.id)
    assert got is not None and got.id == created.id
    await engine.dispose()
