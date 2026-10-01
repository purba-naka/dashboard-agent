"""Unit test repositori Metadata_Store (Req 1.1, 1.3, 1.4, 7.4, 7.5, 10.7, 29.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime


import pytest

from studio.api.errors import StudioError
from studio.core.models import ColumnInfo, DashboardContent, PatchEvent, SetTitleOp
from studio.core.relations import RelationCandidate

from studio.store.repos import LOCAL_OWNER_ID, MAX_SNAPSHOT_ROWS, Repositories

SCHEMA = [ColumnInfo(name="customer_id", type="integer"), ColumnInfo(name="name", type="string")]


@pytest.fixture
async def repos(metadata_db) -> AsyncIterator[Repositories]:
    yield Repositories(metadata_db)


async def _dataset(repos: Repositories, ws_id: str, table: str, upload_id: str | None = None):
    return await repos.datasets.create(
        ws_id,
        table_name=table,
        source_name=f"{table}.csv",
        parquet_path=f"{ws_id}/{table}.parquet",
        schema=SCHEMA,
        column_mapping=[("Customer ID", "customer_id"), ("Name", "name")],
        row_count=3,
        upload_id=upload_id,
    )


def _candidate(from_ds, to_ds, key: str = "customers.customer_id|orders.customer_id"):
    return RelationCandidate(
        candidate_key=key,
        from_dataset_id=from_ds.id,
        from_table=from_ds.table_name,
        from_column="customer_id",
        to_dataset_id=to_ds.id,
        to_table=to_ds.table_name,
        to_column="customer_id",
        cardinality="one_to_many",
        overlap_pct=87.5,
    )


async def _count(repos: Repositories, table: str, ws_id: str) -> int:
    return await repos.db.fetch_value(
        f"SELECT COUNT(*) FROM {table} WHERE workspace_id = ?", (ws_id,)
    )


# ---------------------------------------------------------------------------
# Workspace CRUD & owner_id
# ---------------------------------------------------------------------------


async def test_workspace_crud(repos: Repositories) -> None:
    ws = await repos.workspaces.create("Penjualan")
    assert len(ws.id) == 26  # ULID
    assert ws.name == "Penjualan"
    assert ws.owner_id == LOCAL_OWNER_ID == "local"
    assert ws.created_at.tzinfo is not None

    assert await repos.workspaces.get(ws.id) == ws
    assert [w.id for w in await repos.workspaces.list()] == [ws.id]
    assert [w.id for w in await repos.workspaces.list(owner_id="local")] == [ws.id]
    assert await repos.workspaces.list(owner_id="other") == []

    renamed = await repos.workspaces.rename(ws.id, "Penjualan 2025")
    assert renamed.name == "Penjualan 2025"
    assert renamed.created_at == ws.created_at
    assert renamed.updated_at >= ws.updated_at

    await repos.workspaces.delete(ws.id)
    assert await repos.workspaces.get_or_none(ws.id) is None
    for call in (
        repos.workspaces.get(ws.id),
        repos.workspaces.rename(ws.id, "x"),
        repos.workspaces.delete(ws.id),
    ):
        with pytest.raises(StudioError) as exc_info:
            await call
        assert exc_info.value.code == "NOT_FOUND"
        assert exc_info.value.http_status == 404


async def test_dataset_and_dashboard_inherit_local_owner(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    ds = await _dataset(repos, ws.id, "customers")
    dash = await repos.dashboards.create(ws.id, "Dashboard A")

    assert ds.owner_id == dash.owner_id == "local"
    assert ds.schema == tuple(SCHEMA)
    assert [(m.original, m.normalized) for m in ds.column_mapping] == [
        ("Customer ID", "customer_id"),
        ("Name", "name"),
    ]
    assert ds.data_version == 1
    assert dash.version == 0
    assert dash.content == DashboardContent(title="Dashboard A")

    with pytest.raises(StudioError) as exc_info:
        await _dataset(repos, "missing-workspace", "t")
    assert exc_info.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# UNIQUE table_name per workspace
# ---------------------------------------------------------------------------


async def test_table_name_unique_per_workspace(repos: Repositories) -> None:
    ws_a = await repos.workspaces.create("A")
    ws_b = await repos.workspaces.create("B")
    await _dataset(repos, ws_a.id, "customers")

    with pytest.raises(StudioError) as exc_info:
        await _dataset(repos, ws_a.id, "customers")
    assert exc_info.value.code == "CONFLICT"
    assert exc_info.value.http_status == 409

    # Nama tabel sama di Workspace lain diizinkan.
    other = await _dataset(repos, ws_b.id, "customers")
    assert other.workspace_id == ws_b.id
    assert await repos.datasets.list_table_names(ws_a.id) == ["customers"]
    assert (await repos.datasets.get_by_table(ws_b.id, "customers")).id == other.id


# ---------------------------------------------------------------------------
# Cascade hapus Workspace
# ---------------------------------------------------------------------------


async def test_delete_workspace_cascades_all_metadata(repos: Repositories) -> None:
    ws = await repos.workspaces.create("Hapus")
    keep = await repos.workspaces.create("Tetap")

    upload = await repos.uploads.create(
        ws.id, original_name="customers.csv", stored_path="customers.csv", kind="csv", size_bytes=10
    )
    customers = await _dataset(repos, ws.id, "customers", upload_id=upload.id)
    orders = await _dataset(repos, ws.id, "orders")
    await repos.profiles.upsert(customers.id, data_version=1, columns=[], quality={"duplicate_rows": 0})
    [relation] = await repos.relations.upsert_candidates(ws.id, [_candidate(customers, orders)])
    query = await repos.queries.create(ws.id, sql="SELECT 1 AS x", output_schema=[], rows=[])
    dash = await repos.dashboards.create(ws.id, "D")
    patch = await repos.patches.insert(
        PatchEvent(
            id="p1",
            dashboard_id=dash.id,
            version=1,
            base_version=0,
            source="user",
            ops=[SetTitleOp(before="D", after="D2")],
            inverse_ops=[SetTitleOp(before="D2", after="D")],
            created_at=datetime.now(UTC),
        )
    )
    session = await repos.chat_sessions.create(ws.id, "Chat")
    proposal = await repos.proposals.create(session.id, "Tambah chart")
    kept_ds = await _dataset(repos, keep.id, "customers")

    await repos.workspaces.delete(ws.id)

    assert await repos.workspaces.get_or_none(ws.id) is None
    for table in ("uploads", "datasets", "relations", "queries", "dashboards", "chat_sessions"):
        assert await _count(repos, table, ws.id) == 0, table
    assert await repos.uploads.get_or_none(upload.id) is None
    assert await repos.profiles.get_or_none(customers.id) is None
    assert await repos.relations.get_or_none(relation.id) is None
    assert await repos.queries.get_or_none(query.id) is None
    assert await repos.dashboards.get_or_none(dash.id) is None
    assert await repos.patches.get_or_none(patch.id) is None
    assert await repos.chat_sessions.get_or_none(session.id) is None
    assert await repos.proposals.get_or_none(proposal.id) is None
    # Workspace lain tidak tersentuh.
    assert (await repos.workspaces.get(keep.id)).id == keep.id
    assert (await repos.datasets.get(kept_ds.id)).workspace_id == keep.id


# ---------------------------------------------------------------------------
# Status relasi
# ---------------------------------------------------------------------------


async def test_relation_status_transitions(repos: Repositories) -> None:
    ws = await repos.workspaces.create("R")
    customers = await _dataset(repos, ws.id, "customers")
    orders = await _dataset(repos, ws.id, "orders")
    cand = _candidate(customers, orders)

    [rel] = await repos.relations.upsert_candidates(ws.id, [cand])
    assert rel.status == "candidate"
    assert (rel.from_table, rel.to_table) == ("customers", "orders")
    assert rel.cardinality == "one_to_many"
    assert rel.overlap_pct == 87.5
    assert rel.decided_at is None

    # Konfirmasi → Confirmed_Relation (Req 7.4).
    confirmed = await repos.relations.confirm(rel.id, workspace_id=ws.id)
    assert confirmed.status == "confirmed"
    assert confirmed.decided_at is not None
    assert await repos.relations.confirmed_ids(ws.id) == frozenset({rel.id})
    assert [r.id for r in await repos.relations.list_confirmed(ws.id)] == [rel.id]

    # Tolak → tidak diusulkan lagi; upsert ulang tidak menghidupkannya (Req 7.5).
    await repos.relations.reject(rel.id)
    assert await repos.relations.rejected_keys(ws.id) == frozenset({cand.candidate_key})
    assert await repos.relations.confirmed_ids(ws.id) == frozenset()
    [again] = await repos.relations.upsert_candidates(ws.id, [cand])
    assert (again.id, again.status) == (rel.id, "rejected")

    # Soft delete → disembunyikan dari list; konfirmasi/tolak → NOT_FOUND.
    deleted = await repos.relations.delete(rel.id)
    assert deleted.status == "deleted"
    assert await repos.relations.list(ws.id) == []
    for call in (repos.relations.confirm(rel.id), repos.relations.reject(rel.id)):
        with pytest.raises(StudioError) as exc_info:
            await call
        assert exc_info.value.code == "NOT_FOUND"

    # Terdeteksi ulang dari deleted → candidate dengan id yang sama.
    [revived] = await repos.relations.upsert_candidates(ws.id, [cand])
    assert (revived.id, revived.status) == (rel.id, "candidate")

    # Workspace lain tidak dapat mengubah relasi ini.
    other = await repos.workspaces.create("Lain")
    with pytest.raises(StudioError) as exc_info:
        await repos.relations.confirm(rel.id, workspace_id=other.id)
    assert exc_info.value.code == "NOT_FOUND"


# ---------------------------------------------------------------------------
# Snapshot query ≤ 1.000 baris
# ---------------------------------------------------------------------------


async def test_query_snapshot_truncated_to_1000_rows(repos: Repositories) -> None:
    ws = await repos.workspaces.create("Q")
    schema = [ColumnInfo(name="i", type="integer"), ColumnInfo(name="d", type="date")]
    rows = [[i, date(2024, 1, 1 + i % 28)] for i in range(1500)]
    sql = "SELECT i, d FROM t -- apa adanya"

    rec = await repos.queries.create(ws.id, sql=sql, output_schema=schema, rows=rows)

    assert MAX_SNAPSHOT_ROWS == 1000
    assert len(rec.rows) == 1000
    assert rec.row_count == 1500
    assert rec.truncated_for_storage is True
    assert rec.sql == sql
    assert rec.columns == tuple(schema)
    assert rec.rows[0] == (0, "2024-01-01")  # date → ISO
    assert rec.rows[-1] == (999, date(2024, 1, 1 + 999 % 28).isoformat())

    small = await repos.queries.create(ws.id, sql="SELECT 1", output_schema=schema[:1], rows=[[1], [2]])
    assert small.rows == ((1,), (2,))
    assert small.row_count == 2
    assert small.truncated_for_storage is False

    fetched = await repos.queries.get(rec.id, workspace_id=ws.id)
    assert fetched == rec
