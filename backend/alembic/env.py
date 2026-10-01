"""Alembic env: URL dari env `DATABASE_URL` (atau `backend/.env`). Migrasi ditulis tangan."""

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = os.environ.get("DATABASE_URL", "").strip()
if not url.startswith(("postgresql", "postgres")):
    raise RuntimeError("Set DATABASE_URL=postgresql://... (di env atau backend/.env)")
url = url.split("://", 1)[1]
config.set_main_option("sqlalchemy.url", f"postgresql+asyncpg://{url}")

target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
