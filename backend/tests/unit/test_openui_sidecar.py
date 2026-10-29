"""Klien sidecar OpenUI berbicara dengan proses bun asli (D6)."""

from __future__ import annotations

import shutil

import pytest

from studio.core.openui.sidecar import OpenUISidecar, SidecarError

pytestmark = pytest.mark.skipif(shutil.which("bun") is None, reason="bun tidak terpasang")

PROGRAM = "\n".join(
    [
        "root = VContainer([row])",
        'q_trend = Query("render_query", {query: "q_02"}, {rows: []})',
        'slot_trend = LineChart("Tren", q_trend, "month", ["revenue"])',
        "row = HContainer([slot_trend])",
    ]
)


@pytest.fixture(scope="module")
def sidecar():
    sc = OpenUISidecar(timeout_s=30)
    yield sc
    sc.close()


def test_analyse_maps_positional_props(sidecar: OpenUISidecar) -> None:
    result = sidecar.analyse(PROGRAM)
    assert result["errors"] == []
    chart = next(s for s in result["statements"] if s["id"] == "slot_trend")
    assert chart["component"] == "LineChart"
    assert chart["props"]["data"] == {"$ref": "q_trend"}
    assert chart["props"]["y"] == ["revenue"]


def test_apply_reports_diff(sidecar: OpenUISidecar) -> None:
    result = sidecar.apply(PROGRAM, 'slot_trend = LineChart("Tren baru", q_trend, "month", ["revenue"])')
    assert result["replaced"] == ["slot_trend"]
    assert result["added"] == [] and result["removed"] == []


def test_prompt_is_cached(sidecar: OpenUISidecar) -> None:
    first = sidecar.prompt(preamble="Canvas")
    assert "VContainer(" in first and "render_kpi" in first
    assert sidecar.prompt(preamble="Canvas") is first


def test_unknown_method_raises(sidecar: OpenUISidecar) -> None:
    with pytest.raises(SidecarError):
        sidecar.call("nope")
    # Proses tetap dapat dipakai setelah error tingkat aplikasi.
    assert sidecar.call("ping")["ok"] is True


def test_missing_executable() -> None:
    with pytest.raises(SidecarError):
        OpenUISidecar(bun="bun-yang-tidak-ada").call("ping")
