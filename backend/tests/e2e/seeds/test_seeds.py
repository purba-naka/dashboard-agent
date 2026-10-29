"""Ground truth seed e2e konsisten dengan file di disk dan reference_sql (plan A2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.seeds.generate import REFERENCE_SQL, compute_facts, load_frames, run_sql

SEEDS = Path(__file__).resolve().parent
EXPECTED = json.loads((SEEDS / "expected_facts.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frames():
    return load_frames()


def test_facts_match_files(frames) -> None:
    assert compute_facts(frames) == EXPECTED["facts"]


@pytest.mark.parametrize("key", sorted(REFERENCE_SQL))
def test_reference_sql_runs(frames, key: str) -> None:
    assert run_sql(frames, EXPECTED["reference_sql"][key]).height >= 1


def test_planted_patterns() -> None:
    f = EXPECTED["facts"]
    assert f["revenue_total_selesai"] < f["revenue_total_semua_status"]
    assert max(f["revenue_by_wilayah_selesai"], key=f["revenue_by_wilayah_selesai"].get) == "Jawa"
    assert max(f["revenue_by_kategori_selesai"], key=f["revenue_by_kategori_selesai"].get) == "Elektronik"
    assert max(f["unit_by_kategori_selesai"], key=f["unit_by_kategori_selesai"].get) == "Makanan"
    assert f["toko_turun"]["slope_pct_per_bulan"] < -5
    assert f["bulan_anomali"]["rasio_vs_rata_lain"] > 1.5
    assert f["null_kota_count"] == 2
    assert all(r["overlap_pct"] == 100.0 for r in EXPECTED["relations"])
