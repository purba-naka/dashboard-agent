"""Unit test Data_Engine (Req 10.6, 10.7, 11.2, 7.5, 22.2).

Sebagian besar test memakai ``InlineQueryRunner`` (tanpa biaya spawn). Test timeout
memakai ``QueryWorkerPool`` sungguhan (``size=1``) agar pembatalan dengan membunuh
proses worker dan penggantiannya benar-benar teruji.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path

import polars as pl
import pytest

from studio.api.errors import StudioError
from studio.core.filters import in_values
from studio.core.models import ColumnInfo
from studio.core.relations import RelationCandidate
from studio.data.engine import DataEngine, format_catalog
from studio.data.worker import InlineQueryRunner, QueryWorkerPool

from studio.store.repos import MAX_SNAPSHOT_ROWS, DatasetRecord, Repositories, new_id

JOIN_SQL = (
    "SELECT c.region, SUM(o.amount) AS total FROM orders AS o "
    "JOIN customers AS c ON o.customer_id = c.customer_id "
    "GROUP BY c.region ORDER BY c.region"
)


# ---------------------------------------------------------------------------
# Fixture & helper
# ---------------------------------------------------------------------------


@pytest.fixture
async def repos(metadata_db) -> AsyncIterator[Repositories]:
    yield Repositories(metadata_db)


async def _add_dataset(
    repos: Repositories, data_dir: Path, ws_id: str, table: str, df: pl.DataFrame
) -> DatasetRecord:
    """Tulis Parquet di ``DATA_DIR/uploads/{ws}/{dataset_id}.parquet`` dan daftarkan Dataset."""
    ds_id = new_id()
    rel_path = f"{ws_id}/{ds_id}.parquet"
    target = data_dir / "uploads" / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(target)
    type_of = {pl.Int64: "integer", pl.Float64: "float", pl.String: "string"}
    schema = [ColumnInfo(name=n, type=type_of[t]) for n, t in df.schema.items()]
    return await repos.datasets.create(
        ws_id,
        table_name=table,
        source_name=f"{table}.csv",
        parquet_path=rel_path,
        schema=schema,
        column_mapping=[(n, n) for n in df.columns],
        row_count=df.height,
        dataset_id=ds_id,
    )


async def _query_count(repos: Repositories, ws_id: str) -> int:
    return await repos.db.fetch_value(
        "SELECT COUNT(*) FROM queries WHERE workspace_id = ?", (ws_id,)
    )


@pytest.fixture
async def ws(repos: Repositories, tmp_data_dir: Path) -> dict:
    """Workspace berisi ``customers`` (4 baris) dan ``orders`` (6 baris)."""
    workspace = await repos.workspaces.create("Penjualan")
    customers = await _add_dataset(
        repos,
        tmp_data_dir,
        workspace.id,
        "customers",
        pl.DataFrame(
            {"customer_id": [1, 2, 3, 4], "region": ["north", "south", "north", "east"]}
        ),
    )
    orders = await _add_dataset(
        repos,
        tmp_data_dir,
        workspace.id,
        "orders",
        pl.DataFrame(
            {
                "order_id": [10, 11, 12, 13, 14, 15],
                "customer_id": [1, 1, 2, 3, 4, 4],
                "amount": [100.0, 50.0, 70.0, 30.0, 20.0, 10.0],
            }
        ),
    )
    return {"id": workspace.id, "customers": customers, "orders": orders}


@pytest.fixture
def engine(repos: Repositories, tmp_data_dir: Path) -> DataEngine:
    return DataEngine(repos, InlineQueryRunner(size=2), tmp_data_dir)


async def _confirm_relation(repos: Repositories, ws: dict) -> str:
    customers, orders = ws["customers"], ws["orders"]
    [cand] = await repos.relations.upsert_candidates(
        ws["id"],
        [
            RelationCandidate(
                candidate_key="customers.customer_id|orders.customer_id",
                from_dataset_id=customers.id,
                from_table="customers",
                from_column="customer_id",
                to_dataset_id=orders.id,
                to_table="orders",
                to_column="customer_id",
                cardinality="one_to_many",
                overlap_pct=100.0,
            )
        ],
    )
    confirmed = await repos.relations.confirm(cand.id, workspace_id=ws["id"])
    return confirmed.id


def _assert_catalog_details(err: StudioError) -> None:
    assert sorted(err.details["tables"]) == ["customers", "orders"]
    catalog = err.details["catalog"]
    assert catalog["customers"] == ["customer_id:integer", "region:string"]
    assert catalog["orders"] == ["order_id:integer", "customer_id:integer", "amount:float"]


# ---------------------------------------------------------------------------
# Eksekusi & penyimpanan query (Req 10.5, 10.7, 22.2)
# ---------------------------------------------------------------------------


async def test_execute_stores_sql_unchanged_and_snapshot(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    sql = "  select   customer_id, SUM(amount) AS total\nFROM orders\n GROUP BY customer_id ORDER BY customer_id ; "

    result = await engine.execute(ws["id"], sql, created_by="user")

    assert [(c.name, c.type) for c in result.columns] == [
        ("customer_id", "integer"),
        ("total", "float"),
    ]
    assert result.rows == [[1, 150.0], [2, 70.0], [3, 30.0], [4, 30.0]]
    assert result.row_count == 4
    assert result.truncated_for_storage is False

    record = await repos.queries.get(result.query_id, workspace_id=ws["id"])
    assert record.sql == sql  # teks SQL identik, termasuk spasi & titik koma
    assert [list(r) for r in record.rows] == result.rows
    assert record.row_count == 4
    assert record.output_schema == tuple(result.columns)
    assert record.tables_used == ("orders",)
    assert record.dataset_ids == (ws["orders"].id,)
    assert record.created_by == "user"
    assert record.lineage["customer_id"] == {"table": "orders", "column": "customer_id"}
    assert await _query_count(repos, ws["id"]) == 1


async def test_snapshot_capped_at_max_snapshot_rows(
    engine: DataEngine, repos: Repositories, ws: dict, tmp_data_dir: Path
) -> None:
    total = MAX_SNAPSHOT_ROWS + 500
    await _add_dataset(
        repos, tmp_data_dir, ws["id"], "numbers", pl.DataFrame({"n": list(range(total))})
    )

    result = await engine.execute(ws["id"], "SELECT n FROM numbers ORDER BY n")

    assert result.row_count == total
    assert len(result.rows) == total  # pemanggil tetap menerima hasil penuh
    assert result.truncated_for_storage is True
    record = await repos.queries.get(result.query_id)
    assert record.row_count == total
    assert len(record.rows) == MAX_SNAPSHOT_ROWS
    assert [r[0] for r in record.rows] == list(range(MAX_SNAPSHOT_ROWS))
    assert record.truncated_for_storage is True


# ---------------------------------------------------------------------------
# Error SQL_Validator + katalog (Req 11.2)
# ---------------------------------------------------------------------------


async def test_unknown_table_error_includes_catalog(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    with pytest.raises(StudioError) as info:
        await engine.execute(ws["id"], "SELECT * FROM invoices")

    err = info.value
    assert err.code == "UNKNOWN_TABLE"
    assert "invoices" in err.message
    _assert_catalog_details(err)
    assert await _query_count(repos, ws["id"]) == 0


async def test_polars_error_message_contains_polars_message_and_catalog(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    sql = "SELECT no_such_column FROM orders"

    with pytest.raises(StudioError) as info:
        await engine.validate(ws["id"], sql)

    err = info.value
    assert err.code == "POLARS_ERROR"
    assert err.http_status == 422
    polars_msg = err.details["polars_message"]
    assert polars_msg and "no_such_column" in polars_msg
    assert polars_msg in err.message
    # Pesan memuat daftar tabel & kolom yang tersedia (untuk retry Query_Agent).
    assert "Tabel tersedia" in err.message
    assert "orders(order_id:integer, customer_id:integer, amount:float)" in err.message
    assert "customers(customer_id:integer, region:string)" in err.message
    _assert_catalog_details(err)
    assert err.details["catalog"] == format_catalog(await engine.catalog(ws["id"]))

    # Jalur execute memberi error yang sama dan tidak menyimpan query.
    with pytest.raises(StudioError) as info2:
        await engine.execute(ws["id"], sql)
    assert info2.value.code == "POLARS_ERROR"
    assert await _query_count(repos, ws["id"]) == 0


async def test_join_requires_confirmed_relation(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    with pytest.raises(StudioError) as info:
        await engine.execute(ws["id"], JOIN_SQL)
    assert info.value.code == "UNCONFIRMED_JOIN"
    _assert_catalog_details(info.value)

    rel_id = await _confirm_relation(repos, ws)
    result = await engine.execute(ws["id"], JOIN_SQL)

    assert result.rows == [["east", 30.0], ["north", 180.0], ["south", 70.0]]
    record = await repos.queries.get(result.query_id)
    assert record.relations_used == frozenset({rel_id})
    assert set(record.tables_used) == {"orders", "customers"}
    assert await _query_count(repos, ws["id"]) == 1


async def test_candidate_relation_is_not_enough_for_join(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    customers, orders = ws["customers"], ws["orders"]
    await repos.relations.upsert_candidates(
        ws["id"],
        [
            RelationCandidate(
                candidate_key="customers.customer_id|orders.customer_id",
                from_dataset_id=customers.id,
                from_table="customers",
                from_column="customer_id",
                to_dataset_id=orders.id,
                to_table="orders",
                to_column="customer_id",
                cardinality="one_to_many",
                overlap_pct=100.0,
            )
        ],
    )
    with pytest.raises(StudioError) as info:
        await engine.validate(ws["id"], JOIN_SQL)
    assert info.value.code == "UNCONFIRMED_JOIN"


# ---------------------------------------------------------------------------
# run_saved & filter (render/refresh tanpa query baru)
# ---------------------------------------------------------------------------


async def test_run_saved_reuses_query_and_applies_filters(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    await _confirm_relation(repos, ws)
    sql = "SELECT SUM(amount) AS total FROM orders"
    first = await engine.execute(ws["id"], sql)
    assert first.rows == [[280.0]]

    unfiltered = await engine.run_saved(first.query_id, workspace_id=ws["id"])
    assert unfiltered.query_id == first.query_id
    assert unfiltered.rows == [[280.0]]

    # Filter langsung pada tabel query.
    direct = await engine.run_saved(first.query_id, [in_values("orders", "customer_id", [4])])
    assert direct.rows == [[30.0]]

    # Filter pada customers merambat ke orders lewat Confirmed_Relation.
    record = await repos.queries.get(first.query_id)
    propagated = await engine.run_saved(record, [in_values("customers", "region", ["north"])])
    assert propagated.query_id == first.query_id
    assert propagated.rows == [[180.0]]

    assert await _query_count(repos, ws["id"]) == 1
    stored = await repos.queries.get(first.query_id)
    assert stored.sql == sql
    assert [list(r) for r in stored.rows] == [[280.0]]  # snapshot asli tidak berubah


async def test_execute_with_filters_stores_filters(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    result = await engine.execute(
        ws["id"],
        "SELECT COUNT(*) AS n FROM orders",
        [in_values("orders", "customer_id", [1, 4])],
    )
    assert result.rows == [[4]]
    record = await repos.queries.get(result.query_id)
    assert record.sql == "SELECT COUNT(*) AS n FROM orders"
    assert len(record.filters) == 1


async def test_run_saved_rejects_other_workspace(
    engine: DataEngine, repos: Repositories, ws: dict
) -> None:
    first = await engine.execute(ws["id"], "SELECT COUNT(*) AS n FROM orders")
    other = await repos.workspaces.create("Lain")
    with pytest.raises(StudioError) as info:
        await engine.run_saved(first.query_id, workspace_id=other.id)
    assert info.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Timeout dengan worker pool sungguhan (Req 10.6)
# ---------------------------------------------------------------------------


async def test_timeout_kills_worker_and_pool_recovers(
    repos: Repositories, tmp_data_dir: Path
) -> None:
    workspace = await repos.workspaces.create("Berat")
    rows = 100_000
    await _add_dataset(
        repos, tmp_data_dir, workspace.id, "big", pl.DataFrame({"x": list(range(rows))})
    )
    # Self CROSS JOIN 1e10 baris (streaming, memori konstan) — jauh melewati 1 detik.
    slow_sql = (
        "SELECT COUNT(*) AS n, SUM(a.x * b.x) AS s FROM big AS a "
        "CROSS JOIN big AS b WHERE (a.x + b.x) % 7 = 0"
    )

    pool = QueryWorkerPool(size=1, timeout_s=1.0)
    try:
        async with asyncio.timeout(60):  # batas keseluruhan test (termasuk spawn)
            await pool.start()
            engine = DataEngine(repos, pool, tmp_data_dir)
            pids_before = pool.worker_pids
            assert len(pids_before) == 1

            started = time.monotonic()
            with pytest.raises(StudioError) as info:
                await engine.execute(workspace.id, slow_sql, timeout_s=1.0)
            elapsed = time.monotonic() - started

            assert info.value.code == "QUERY_TIMEOUT"
            assert info.value.http_status == 504
            assert info.value.details["timeout_s"] == 1.0
            assert elapsed < 10, f"timeout tidak membatalkan eksekusi ({elapsed:.1f}s)"
            assert await _query_count(repos, workspace.id) == 0

            # Pool tetap berfungsi: query berikutnya dilayani worker pengganti.
            result = await engine.execute(workspace.id, "SELECT COUNT(*) AS n FROM big")
            assert result.rows == [[rows]]
            pids_after = pool.worker_pids
            assert len(pids_after) == 1
            assert pids_after != pids_before
            assert await _query_count(repos, workspace.id) == 1
    finally:
        await pool.close()
    assert pool.worker_pids == []
