"""Fixture integration test: app FastAPI nyata (lifespan penuh) di atas DATA_DIR sementara."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from studio.app import create_app
from studio.config import Settings
from studio.store.pg import PgDatabase
from tests.conftest import reset_pg
from tests.integration.helpers import Studio, running


@pytest.fixture
async def settings(tmp_path: Path) -> Settings:
    """Runner query inline (thread) agar cepat; heartbeat SSE pendek agar disconnect cepat terdeteksi."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        async with PgDatabase(url) as db:
            await reset_pg(db)
    return Settings(
        data_dir=tmp_path / "data",
        query_runner_mode="inline",
        sse_heartbeat_seconds=0.2,
        database_url=url,
    )


@pytest.fixture
async def studio(settings: Settings) -> AsyncIterator[Studio]:
    """App berjalan + klien httpx (ASGI) + helper alur REST."""
    app = create_app(settings)
    async with running(app) as client:
        yield Studio(app, client)
