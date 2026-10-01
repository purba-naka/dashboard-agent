"""Fixture patch lintas bahasa (Req 30.3): file ter-commit harus mutakhir dan konsisten."""

from __future__ import annotations

import json

import pytest

from studio.core.models import DashboardContent, PatchEvent
from studio.core.patches import replay
from tests.fixtures.gen_patch_fixtures import FIXTURE_PATH, build_fixtures, render_fixtures

_OPS = {
    "add_item",
    "remove_item",
    "set_item",
    "set_layout",
    "set_filters",
    "set_title",
    "set_brief",
}


def test_committed_fixture_is_up_to_date() -> None:
    assert FIXTURE_PATH.exists(), "jalankan: python -m tests.fixtures.gen_patch_fixtures"
    committed = FIXTURE_PATH.read_text(encoding="utf-8")
    assert committed == render_fixtures(), (
        "patches.json usang; jalankan: python -m tests.fixtures.gen_patch_fixtures"
    )


def test_generator_is_deterministic() -> None:
    assert render_fixtures() == render_fixtures()


def test_fixture_covers_every_op_and_undo_redo() -> None:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    patches = [p for s in data["scenarios"] for p in s["patches"]]
    assert {op["op"] for p in patches for op in p["ops"]} == _OPS
    assert {p["kind"] for p in patches} == {"normal", "undo", "redo"}
    set_item_kinds = {
        op["after"]["kind"] for p in patches for op in p["ops"] if op["op"] == "set_item"
    }
    assert set_item_kinds == {"chart", "insight", "kpi"}


def _committed_scenarios() -> list[dict]:
    # Pakai JSON ter-commit (yang dikonsumsi frontend); fallback ke hasil generator.
    if FIXTURE_PATH.exists():
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["scenarios"]
    return build_fixtures()["scenarios"]


@pytest.mark.parametrize("scenario", _committed_scenarios(), ids=lambda s: s["name"])
def test_replay_of_committed_json_matches_expected(scenario: dict) -> None:
    initial = DashboardContent.model_validate(scenario["initial"])
    patches = [PatchEvent.model_validate(p) for p in scenario["patches"]]
    expected = DashboardContent.model_validate(scenario["expected"])

    # Versi berurutan tanpa celah, mulai dari 0.
    assert [p.base_version for p in patches] == list(range(len(patches)))
    result = replay(initial, patches)
    assert result == expected
    assert result.model_dump(mode="json") == scenario["expected"]
