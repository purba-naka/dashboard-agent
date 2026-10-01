"""Unit test repositori Semantic_Model & Blueprint (Req 31.4, 31.7, 31.8, 31.9, 37.6)."""

from __future__ import annotations

from collections.abc import AsyncIterator


import pytest

from studio.api.errors import StudioError
from studio.core.models import ColumnInfo, DashboardContent

from studio.store.repos import Repositories

SCHEMA = [ColumnInfo(name="amount", type="float"), ColumnInfo(name="region", type="string")]


@pytest.fixture
async def repos(metadata_db) -> AsyncIterator[Repositories]:
    yield Repositories(metadata_db)


async def _dataset(repos: Repositories, ws_id: str, table: str = "sales"):
    return await repos.datasets.create(
        ws_id,
        table_name=table,
        source_name=f"{table}.csv",
        parquet_path=f"{ws_id}/{table}.parquet",
        schema=SCHEMA,
        column_mapping=[("Amount", "amount"), ("Region", "region")],
        row_count=3,
    )


async def test_upsert_unique_key_and_version_bump(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    v0 = (await repos.semantic_meta.get(ws.id)).semantic_version
    first = await repos.semantic.upsert(
        ws.id, kind="metric", entry_key="metric:revenue", body={"name": "revenue"}
    )
    second = await repos.semantic.upsert(
        ws.id, kind="metric", entry_key="metric:revenue", body={"name": "revenue", "label": "R"}
    )
    assert first.id == second.id
    assert second.body["label"] == "R"
    assert first.status == "candidate" and first.source == "auto"
    assert len(await repos.semantic.list(ws.id)) == 1
    assert (await repos.semantic_meta.get(ws.id)).semantic_version > v0


async def test_rejected_kept_and_reported(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    entry = await repos.semantic.upsert(ws.id, kind="term", entry_key="term:gm", body={})
    await repos.semantic.set_status(entry.id, "rejected")
    assert await repos.semantic.rejected_keys(ws.id) == {"term:gm"}
    assert await repos.semantic.list(ws.id, status="rejected")
    assert not await repos.semantic.list(ws.id, status=["candidate", "confirmed"])


async def test_update_body_marks_user_confirmed(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    entry = await repos.semantic.upsert(ws.id, kind="instruction", entry_key="instr:x", body={"text": "a"})
    edited = await repos.semantic.update_body(entry.id, {"text": "b"})
    assert (edited.source, edited.status, edited.body) == ("user", "confirmed", {"text": "b"})
    assert edited.decided_at is not None


async def test_confirm_all_only_candidates(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    a = await repos.semantic.upsert(ws.id, kind="term", entry_key="term:a", body={})
    b = await repos.semantic.upsert(ws.id, kind="term", entry_key="term:b", body={})
    await repos.semantic.set_status(b.id, "rejected")
    assert await repos.semantic.confirm_all_candidates(ws.id) == 1
    assert (await repos.semantic.get(a.id)).status == "confirmed"
    assert (await repos.semantic.get(b.id)).status == "rejected"


async def test_cascade_on_dataset_and_workspace_delete(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    ds = await _dataset(repos, ws.id)
    await repos.semantic.upsert(
        ws.id, kind="column", entry_key="col:sales.amount", body={}, dataset_id=ds.id
    )
    await repos.semantic.upsert(ws.id, kind="metric", entry_key="metric:revenue", body={})
    await repos.datasets.delete(ds.id)
    assert [e.entry_key for e in await repos.semantic.list(ws.id)] == ["metric:revenue"]
    await repos.workspaces.delete(ws.id)
    assert await repos.db.fetch_value("SELECT COUNT(*) FROM semantic_entries") == 0


async def test_get_wrong_workspace_not_found(repos: Repositories) -> None:
    ws1 = await repos.workspaces.create("A")
    ws2 = await repos.workspaces.create("B")
    entry = await repos.semantic.upsert(ws1.id, kind="term", entry_key="term:a", body={})
    with pytest.raises(StudioError):
        await repos.semantic.get(entry.id, workspace_id=ws2.id)


async def test_draft_runs_latest(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    run = await repos.draft_runs.create(ws.id, None)
    done = await repos.draft_runs.finish(run.id, "llm_failed", [{"entry_key": "k", "reason": "r"}])
    assert done.status == "llm_failed" and done.discarded[0]["reason"] == "r"
    assert (await repos.draft_runs.latest(ws.id)).id == run.id  # type: ignore[union-attr]


async def test_blueprint_lifecycle_and_proposal_payload(repos: Repositories) -> None:
    ws = await repos.workspaces.create("W")
    dash = await repos.dashboards.create(ws.id, "D", content=DashboardContent(title="D"))
    session = await repos.chat_sessions.create(ws.id, "s")
    proposal = await repos.proposals.create(
        session.id, "rancangan", kind="blueprint", payload={"slots": []}
    )
    assert (proposal.kind, proposal.payload) == ("blueprint", {"slots": []})

    bp = {"slots": [{"slot_id": "a"}, {"slot_id": "b"}]}
    first = await repos.blueprints.create(dash.id, bp, proposal_id=proposal.id)
    assert first.slot_status == {"a": {"status": "pending"}, "b": {"status": "pending"}}
    updated = await repos.blueprints.update_slot(first.id, "a", "done", item_id="it1")
    assert updated.slot_status["a"] == {"status": "done", "item_id": "it1"}
    second = await repos.blueprints.create(dash.id, bp)
    assert (await repos.blueprints.get(first.id)).status == "stopped"
    assert (await repos.blueprints.latest(dash.id)).id == second.id  # type: ignore[union-attr]
    with pytest.raises(StudioError):
        await repos.blueprints.update_slot(second.id, "zzz", "done")
