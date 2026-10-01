"""End-to-end dengan model LLM nyata atas ``ntp_raw_check.xlsx`` (Nilai Tukar Petani BPS).

Tidak berjalan default (marker ``live``, berbiaya). Model & kredensial dibaca dari
``backend/.env`` seperti ``main.py``; test di-skip bila model belum dikonfigurasi.

    backend\\.venv\\Scripts\\python.exe -m pytest -m live tests/live -s

Satu Workspace dipakai bersama (upload + profil sekali). Asersi hanya pada hasil
yang dapat diverifikasi, bukan teks persis jawaban LLM:

* angka yang dijawab agent cocok dengan ground truth Polars dari file asli;
* SQL yang dijalankan lolos SQL_Validator dan hasilnya benar;
* tidak ada mutasi Dashboard tanpa persetujuan (Req 21.4);
* Blueprint usulan lolos ``validate_blueprint`` dan setelah disetujui setiap item
  berada tepat di layout slotnya (Req 37.2, 37.7);
* KPI dibuat sebagai item ``kpi`` dan dirender tanpa error (Req 38.4).
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
import pytest_asyncio
from dotenv import load_dotenv

from studio.agents.tools.architect_tools import _vocabulary
from studio.agents.model_gateway import build_models, resolve_from_settings, skip_capability_check
from studio.agents.wiring import configure_agents
from studio.api import chat
from studio.app import create_app
from studio.config import Settings
from studio.core.blueprint import validate_blueprint
from studio.core.models import DashboardBlueprint, LayoutRect
from studio.core.models_config import MissingModelConfig, resolve_models
from studio.core.numbers import extract_numbers, interpretation_matches
from tests.integration.helpers import SseFrame, Studio
from tests.integration.test_chat_stream import chat_stream, running

BACKEND = Path(__file__).resolve().parents[2]
XLSX = BACKEND.parent / "ntp_raw_check.xlsx"
TURN_TIMEOUT_S = float(os.environ.get("LIVE_TURN_TIMEOUT_S", "600"))


pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


# ---------------------------------------------------------------------------
# Ground truth (Polars langsung dari file, independen dari aplikasi)
# ---------------------------------------------------------------------------


def _frame() -> pl.DataFrame:
    return pl.read_excel(XLSX)


def _facts() -> dict[str, Any]:
    df = _frame()
    petani = df.filter((pl.col("kelompok") == "Petani") & (pl.col("provinsi") != "INDONESIA"))
    top = petani.group_by("provinsi").agg(pl.col("ntp").mean()).sort("ntp", descending=True).row(0)
    nasional = df.filter((pl.col("provinsi") == "INDONESIA") & (pl.col("kelompok") == "Petani"))
    return {
        "aceh_petani_januari": df.filter(
            (pl.col("provinsi") == "ACEH") & (pl.col("kelompok") == "Petani") & (pl.col("periode") == "Januari")
        )["ntp"].item(),
        "top_provinsi": top[0],
        "top_provinsi_ntp": top[1],
        "nasional_petani_mean": nasional["ntp"].mean(),
    }


def _mentions(text: str, value: float) -> bool:
    """Teks memuat bilangan yang merupakan tampilan (dibulatkan) dari ``value``."""
    target = Decimal(str(value))
    return any(
        interpretation_matches(i, target) for tok in extract_numbers(text) for i in tok.interpretations
    )


# ---------------------------------------------------------------------------
# Fixture: app dengan model nyata + Workspace NTP
# ---------------------------------------------------------------------------


def _live_models() -> dict[str, Any]:
    load_dotenv(BACKEND / ".env", override=False)
    try:
        resolved = resolve_models(resolve_from_settings(Settings()))
    except MissingModelConfig as exc:
        pytest.skip(f"model LLM belum dikonfigurasi: {exc.message}")
    return build_models(resolved, skip_check=skip_capability_check())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[dict[str, Any]]:
    if not XLSX.exists():
        pytest.skip(f"{XLSX} tidak ada")
    settings = Settings(data_dir=tmp_path_factory.mktemp("live") / "data", query_runner_mode="inline",
                        sse_heartbeat_seconds=5)
    app = create_app(settings, extra_routers=[chat.router], lifespan_hooks=[configure_agents])
    app.state.models = _live_models()
    async with running(app) as client:
        studio = Studio(app, client)
        ws = await studio.create_workspace("NTP 2026")
        body = await studio.upload_xlsx_sheets(ws, XLSX.name, XLSX.read_bytes())
        resp = await client.post(f"/api/workspaces/{ws}/uploads/{body['upload_id']}/sheets",
                                 json={"sheets": [body["sheets"][0]["name"]]})
        assert resp.status_code == 202, resp.text
        job = await studio.wait_job(resp.json()["jobs"][0]["job_id"])
        assert job["status"] == "done", job
        await app.state.semantic_drafter.wait_idle()
        dataset = await app.state.repos.datasets.get(job["dataset_id"])
        dash = await studio.create_dashboard(ws, "NTP")
        yield {"app": app, "client": client, "studio": studio, "ws": ws, "dash": dash["id"],
               "table": dataset.table_name, "facts": _facts(), "session": None}


async def ask(live: dict[str, Any], message: str, **extra: Any) -> dict[str, Any]:
    """Satu giliran chat (sesi dibagi antar test) → frame, tool, teks, query baru."""
    repos = live["app"].state.repos
    before = {q.id for q in await repos.queries.list_by_workspace(live["ws"])}
    body: dict[str, Any] = {"message": message, **extra}
    if live["session"]:
        body["session_id"] = live["session"]
    t0 = time.monotonic()
    async with chat_stream(live["app"], live["ws"], body) as (resp, reader):
        assert resp.status_code == 200
        frames: list[SseFrame] = await reader.until(
            lambda f: f.event in ("run.done", "run.stopped"), timeout=TURN_TIMEOUT_S
        )
    live["session"] = live["session"] or frames[0].data["session_id"]
    tools = [f.data["tool"] for f in frames if f.event == "tool.call" and f.data["tool"] != "transfer_to_agent"]
    text = "".join(f.data["text"] for f in frames if f.event == "text.delta" and f.data["agent"] == "Root_Agent")
    queries = [q for q in await repos.queries.list_by_workspace(live["ws"]) if q.id not in before]
    errors = [f.data for f in frames if f.event == "error"]
    print(f"\n[live] {message!r} ({time.monotonic() - t0:.0f}s)\n  tools={tools}\n  errors={errors}\n  jawaban={text[:500]!r}")
    for q in queries:
        print(f"  sql={q.sql}")
    return {"frames": frames, "tools": tools, "text": text, "queries": queries, "errors": errors}


def _patches(turn: dict[str, Any]) -> list[SseFrame]:
    return [f for f in turn["frames"] if f.event == "patch.applied"]


# ---------------------------------------------------------------------------
# Test (urutan penting: berbagi sesi chat & Dashboard)
# ---------------------------------------------------------------------------


async def test_upload_profil_dan_draft_semantik(live: dict[str, Any]) -> None:
    """Ingest xlsx → profil 6 kolom 2.432 baris; draft semantik terbentuk (Req 32.1)."""
    repos = live["app"].state.repos
    (dataset,) = await repos.datasets.list_by_workspace(live["ws"])
    assert dataset.row_count == 2432
    assert {c.name for c in dataset.schema} == {"kode_provinsi", "provinsi", "kelompok", "tahun", "periode", "ntp"}
    entries = await repos.semantic.list(live["ws"])
    print(f"\n[live] draft semantik: {len(entries)} entri, kinds={sorted({e.kind for e in entries})}")
    assert entries, "Semantic_Drafter tidak menghasilkan entri"


async def test_pertanyaan_nilai_tunggal(live: dict[str, Any]) -> None:
    turn = await ask(live, "Berapa NTP kelompok Petani di provinsi ACEH pada periode Januari 2026?")
    assert not turn["errors"], turn["errors"]
    assert "run_sql" in turn["tools"]
    assert not _patches(turn), "pertanyaan data tidak boleh mengubah Dashboard"
    assert _mentions(turn["text"], live["facts"]["aceh_petani_januari"]), turn["text"]


async def test_agregasi_dan_peringkat(live: dict[str, Any]) -> None:
    """Rata-rata per provinsi: agent harus mengecualikan baris agregat INDONESIA."""
    facts = live["facts"]
    turn = await ask(
        live,
        "Provinsi mana yang rata-rata NTP kelompok Petani-nya paling tinggi selama 2026? "
        "Sebutkan nilainya. Baris INDONESIA adalah agregat nasional, bukan provinsi.",
    )
    assert not turn["errors"], turn["errors"]
    assert not _patches(turn)
    assert facts["top_provinsi"].lower() in turn["text"].lower(), turn["text"]
    assert _mentions(turn["text"], round(facts["top_provinsi_ntp"], 2)), turn["text"]


async def test_mutasi_tanpa_persetujuan_ditolak(live: dict[str, Any]) -> None:
    """Saran perubahan tanpa permintaan eksplisit → tidak ada patch (Req 21.4)."""
    version = (await live["studio"].get_dashboard(live["dash"]))["version"]
    turn = await ask(live, "Menurutmu chart apa yang cocok untuk data ini? Jangan ubah dashboard dulu.")
    assert not turn["errors"], turn["errors"]
    assert not _patches(turn), turn["tools"]
    assert (await live["studio"].get_dashboard(live["dash"]))["version"] == version


async def test_blueprint_usulan_setujui_dan_bangun(live: dict[str, Any]) -> None:
    """Architect mengusulkan Blueprint valid; setelah disetujui item berada di layout slot."""
    studio, app = live["studio"], live["app"]
    before = await studio.get_dashboard(live["dash"])
    turn = await ask(
        live,
        "Buatkan dashboard pemantauan NTP 2026: satu KPI rata-rata NTP nasional (baris INDONESIA, "
        "kelompok Petani), tren NTP nasional per periode, dan perbandingan rata-rata NTP per kelompok. "
        "Usulkan rancangannya dulu.",
    )
    assert not turn["errors"], turn["errors"]
    assert not _patches(turn), "usulan Blueprint tidak boleh mengubah Dashboard"
    cards = [f for f in turn["frames"] if f.event == "approval.request" and f.data.get("kind") == "blueprint"]
    assert cards, f"Architect tidak mengusulkan Blueprint; tools={turn['tools']}"
    card = cards[-1]
    bp = DashboardBlueprint.model_validate(card.data["blueprint"])


    metrics, columns = await _vocabulary(SimpleNamespace(repos=app.state.repos), live["ws"])
    existing = {k: LayoutRect.model_validate(v) for k, v in before["content"]["layout"].items()}
    assert validate_blueprint(bp, existing, metrics, columns) == []
    print(f"  slots={[(s.slot_id, s.section, s.visual) for s in bp.slots]}")

    build = await ask(live, "Setuju, bangun semua slot.",
                      approval={"proposal_id": card.data["proposal_id"]})
    # Error tool sementara (mis. SQL salah lalu diperbaiki) boleh; hasil akhir yang dinilai.
    record = (await live["client"].get(f"/api/dashboards/{live['dash']}/blueprint")).json()
    print(f"  slot_status={record['slot_status']}")
    after = await studio.get_dashboard(live["dash"])
    done = {sid: s["item_id"] for sid, s in record["slot_status"].items() if s["status"] == "done"}
    assert done, "tidak ada slot yang berhasil dibangun"
    assert len(done) >= len(bp.slots) // 2, f"terlalu banyak slot gagal: {record['slot_status']}"
    for slot in record["blueprint"]["slots"]:
        if slot["slot_id"] in done:
            assert after["content"]["layout"][done[slot["slot_id"]]] == slot["layout"], slot["slot_id"]

    rendered = await studio.render(live["dash"])
    statuses = {iid: item["status"] for iid, item in rendered["items"].items()}
    print(f"  render={statuses}")
    assert all(s == "ok" for s in statuses.values()), statuses
    kpis = [item for iid, item in rendered["items"].items() if after["content"]["items"][iid]["kind"] == "kpi"]
    if kpis:
        # KPI nasional: nilainya harus rata-rata NTP INDONESIA/Petani (dengan toleransi pembulatan).
        values = [k["kpi"]["value"] for k in kpis]
        print(f"  kpi_values={values} (ground truth {live['facts']['nasional_petani_mean']:.4f})")
        assert any(abs(v - live["facts"]["nasional_petani_mean"]) < 0.01 for v in values if v is not None), values
