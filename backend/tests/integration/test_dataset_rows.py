"""Endpoint pratinjau baris dataset: paginasi, batas limit, privasi, 404."""

from __future__ import annotations

from typing import Any

from tests.integration.helpers import Studio

async def _setup(studio: Studio) -> tuple[str, dict[str, Any], str]:
    ws = await studio.create_workspace("Rows")
    await studio.upload_sample(ws, "customers.csv")
    dataset = (await studio.client.get(f"/api/workspaces/{ws}/datasets")).json()[0]
    return ws, dataset, f"/api/workspaces/{ws}/datasets/{dataset['id']}/rows"

async def test_rows_pagination(studio: Studio) -> None:
    _, dataset, url = await _setup(studio)
    first = await studio.client.get(url, params={"offset": 0, "limit": 2})
    assert first.status_code == 200, first.text
    body = first.json()
    assert [c["name"] for c in body["columns"]] == [c["name"] for c in dataset["schema"]]
    assert len(body["rows"]) == 2 and body["total"] == dataset["row_count"]
    assert (body["offset"], body["limit"]) == (0, 2)
    assert all(len(r) == len(body["columns"]) for r in body["rows"])
    second = (await studio.client.get(url, params={"offset": 1, "limit": 2})).json()
    assert second["rows"][0] == body["rows"][1]

async def test_rows_limit_capped_at_100(studio: Studio) -> None:
    _, _, url = await _setup(studio)
    body = (await studio.client.get(url, params={"limit": 5000})).json()
    assert body["limit"] == 100 and len(body["rows"]) <= 100
    assert (await studio.client.get(url, params={"offset": -1})).status_code == 422

async def test_rows_rejected_when_privacy_no_samples(studio: Studio) -> None:
    ws, dataset, url = await _setup(studio)
    patched = await studio.client.patch(
        f"/api/workspaces/{ws}/datasets/{dataset['id']}", json={"privacy_no_samples": True}
    )
    assert patched.status_code == 200, patched.text
    resp = await studio.client.get(url)
    assert resp.status_code == 403
    err = resp.json()["error"]
    assert err["code"] == "PRIVACY_NO_SAMPLES" and "rows" not in resp.json()

async def test_rows_unknown_dataset_404(studio: Studio) -> None:
    ws, _, _ = await _setup(studio)
    resp = await studio.client.get(f"/api/workspaces/{ws}/datasets/nope/rows")
    assert resp.status_code == 404
