"""App factory FastAPI untuk Backend_API.

Lifespan (startup, urutan):

1. buat ``DATA_DIR`` dan ``DATA_DIR/uploads``;
2. buka Metadata_Store SQLite + jalankan migrasi (Req 28.1, 28.4);
3. ``EventBus``, query runner (``QueryWorkerPool`` / ``InlineQueryRunner``),
   ``DataEngine``, ``DashboardStore``, ``Profiler``, ``IngestionService``;
4. isi ``app.state`` dengan nama yang dipakai router ``studio.api.*``;
5. jalankan ``lifespan_hooks`` tambahan (mis. wiring chat/ADK, task 18.11).

Shutdown berjalan terbalik: hook tambahan, ``ingestion.aclose()``,
``query_runner.close()``, lalu ``db.close()``.

Titik ekstensi agent (task 18.11)::

    create_app(
        settings,
        extra_routers=[chat.router],
        lifespan_hooks=[configure_agents],  # (app) -> async context manager
    )

``configure_agents(app)`` dijalankan setelah semua service di ``app.state``
tersedia; ia dapat memasang ``app.state.insight_rewriter``, menambahkan hook ke
``app.state.workspace_cleanup_hooks``, dan membersihkan runner ADK saat keluar.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from studio.api import dashboards, datasets, events, queries, relations, semantic, workspaces
from studio.api.errors import register_error_handlers
from studio.config import Settings
from studio.data.engine import DataEngine
from studio.data.ingestion import IngestionService
from studio.data.profiler import Profiler
from studio.data.semantic_drafter import SemanticDrafter
from studio.data.worker import InlineQueryRunner, QueryWorkerPool
from studio.events.bus import EventBus
from studio.store.dashboard_store import DashboardStore
from studio.store.db import Database
from studio.store.pg import PgDatabase, is_postgres_url
from studio.store.repos import DatasetRecord, Repositories

#: Hook lifespan tambahan: ``hook(app)`` → async context manager yang dimasuki
#: setelah service siap dan keluar sebelum service ditutup.
LifespanHook = Callable[[FastAPI], AbstractAsyncContextManager[object]]

#: Router REST/SSE inti; semuanya ber-prefix ``/api``.
CORE_ROUTERS: tuple[APIRouter, ...] = (
    workspaces.router,
    datasets.router,
    relations.router,
    dashboards.router,
    queries.router,
    events.router,
    semantic.router,
)


def build_query_runner(settings: Settings) -> QueryWorkerPool | InlineQueryRunner:
    """``QueryWorkerPool`` (default) atau ``InlineQueryRunner`` sesuai ``query_runner_mode``."""
    if settings.query_runner_mode == "inline":
        return InlineQueryRunner(size=settings.query_workers, timeout_s=settings.query_timeout_s)
    return QueryWorkerPool(size=settings.query_workers, timeout_s=settings.query_timeout_s)


def _ensure_data_dirs(data_dir: Path) -> None:
    (data_dir / "uploads").mkdir(parents=True, exist_ok=True)


def create_app(
    settings: Settings | None = None,
    *,
    extra_routers: Iterable[APIRouter] = (),
    lifespan_hooks: Sequence[LifespanHook] = (),
) -> FastAPI:
    settings = settings or Settings()
    hooks = tuple(lifespan_hooks)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            data_dir = Path(settings.data_dir)
            await asyncio.to_thread(_ensure_data_dirs, data_dir)

            # PostgreSQL bila DATABASE_URL diset (skema via Alembic), selain itu SQLite lokal.
            db = (
                PgDatabase(settings.database_url)  # type: ignore[arg-type]
                if is_postgres_url(settings.database_url)
                else Database.from_settings(settings)
            )
            await db.open()  # SQLite: menjalankan migrasi tertunda
            stack.push_async_callback(db.close)
            repos = Repositories(db)
            bus = EventBus()

            query_runner = build_query_runner(settings)
            stack.push_async_callback(query_runner.close)
            await query_runner.start()

            engine = DataEngine(repos, query_runner, data_dir)
            dashboard_store = DashboardStore(repos, bus)
            profiler = Profiler(repos, bus, engine)
            semantic_drafter = SemanticDrafter(
                repos,
                bus,
                validate_metric=engine.validate_metric,
                timeout_s=settings.semantic_draft_timeout_s,
            )
            stack.push_async_callback(semantic_drafter.aclose)

            async def on_dataset_registered(dataset: DatasetRecord) -> None:
                # Profil + relasi, lalu draft semantik di latar belakang (Req 32.1).
                await profiler.on_dataset_registered(dataset)
                await semantic_drafter.on_dataset_profiled(dataset)

            ingestion = IngestionService(
                repos,
                bus,
                data_dir,
                on_dataset_registered=on_dataset_registered,
                max_upload_bytes=settings.max_upload_bytes,
            )
            stack.push_async_callback(ingestion.aclose)

            state = app.state
            state.settings = settings
            state.db = db
            state.repos = repos
            state.bus = bus
            state.query_runner = query_runner
            state.engine = engine
            state.dashboard_store = dashboard_store
            state.profiler = profiler
            state.ingestion = ingestion
            state.semantic_drafter = semantic_drafter
            # Diisi wiring agent (task 18.11): hapus sesi ADK & penyusun ulang insight.
            state.workspace_cleanup_hooks = []
            state.insight_rewriter = None

            for hook in hooks:
                await stack.enter_async_context(hook(app))

            yield

    app = FastAPI(title="Dashboard Studio Agent", lifespan=lifespan)
    app.state.settings = settings

    # CORS hanya untuk origin Frontend lokal yang dikonfigurasi (Req 29.4).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for router in (*CORE_ROUTERS, *extra_routers):
        app.include_router(router)

    return app
