"""Regresi BUG A: giliran SARAN/OPINI tidak boleh mentransfer ke agent pelaksana mutasi.

Gejala di trace nyata (``tests/eval/results``): pesan "Menurutmu chart apa yang
cocok ...? Jangan ubah dashboard dulu." membuat Root_Agent memanggil
``classify_turn`` lalu ``transfer_to_agent`` ke Chart_Designer_Agent (lalu
Blueprint_Builder_Agent) dan giliran berakhir tanpa jawaban berguna — padahal
``root.md`` sudah melarangnya. Prompt saja tidak cukup, jadi guard harus
deterministik di kode.

Tes di sini memakai wiring asli ``build_agents`` + :class:`FakeModel` (tanpa
LLM) sehingga bisa merah/hijau tanpa biaya model.
"""

from __future__ import annotations

import pytest

from studio.agents.definitions import (
    ARCHITECT_NAME,
    BUILDER_NAME,
    CHART_NAME,
    INSIGHT_NAME,
    PROFILER_NAME,
    QUERY_NAME,
    ROOT_NAME,
)
from studio.agents.tools.transfer_guard import SINGLE_ITEM_CODE, make_root_route_guard
from studio.agents.turn_policy import (
    GENERAL_QUESTION_INTENT,
    TURN_INTENT_KEY,
    begin_turn,
    is_advice_message,
    is_advice_turn,
    record_turn_classification,
)

from tests.unit.agent_harness import FakeModel, call, run_turn, text

SARAN = "Menurutmu chart apa yang cocok untuk data penjualan ini? Jangan ubah dashboard dulu."
MUTASI = "Tambahkan bar chart total revenue per channel ke dashboard sekarang."
DATA = "Berapa total revenue (kolom amount) seluruh transaksi?"
RANCANG = (
    "Rancangkan dashboard penjualan lengkap untuk manajer: KPI, tren bulanan, "
    "dan perbandingan region. Usulkan rancangannya dulu, jangan dibangun."
)

#: Agent pelaksana mutasi Dashboard: giliran saran tidak boleh masuk ke sini.
MUTATING_AGENTS = (CHART_NAME, INSIGHT_NAME, BUILDER_NAME)


# ---------------------------------------------------------------------------
# Predikat murni (cepat, tanpa ADK)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (SARAN, True),
        ("chart apa yang cocok untuk data ini", True),
        ("Menurut kamu, grafik mana yang lebih baik?", True),
        ("Sarankan visualisasi untuk revenue", True),
        ("tolong beri rekomendasi chart", True),
        ("jangan ubah dashboard dulu", True),  # larangan mengubah (root.md)
        (DATA, False),
        (MUTASI, False),
        ("lanjutkan", False),
        ("", False),
        (None, False),
    ],
)
def test_is_advice_message(message: str | None, expected: bool) -> None:
    assert is_advice_message(message) is expected


def test_is_advice_message_hanya_sinyal_permukaan() -> None:
    """Pola saran bisa muncul di pesan perintah; keputusan giliran ada di
    :func:`is_advice_turn`.

    Catatan: heuristik ``_NEGATION`` yang sudah ada (dipakai
    :func:`is_explicit_change_message`) memperlakukan kata "sarankan" sebagai
    permintaan opini, sehingga pesan campuran seperti ini memang BUKAN perintah
    perubahan eksplisit — approval gate pun tidak akan terbuka untuknya.
    """
    pesan = "Tambahkan chart dan sarankan warna yang cocok"
    assert is_advice_message(pesan) is True
    assert is_advice_turn(_state(pesan)) is True


def _state(message: str, **extra: object) -> dict[str, object]:
    state: dict[str, object] = dict(begin_turn(user_message=message))
    state.update(extra)
    return state


def test_is_advice_turn_giliran_saran() -> None:
    assert is_advice_turn(_state(SARAN)) is True


def test_is_advice_turn_perintah_eksplisit_tidak_diblokir() -> None:
    assert is_advice_turn(_state(MUTASI)) is False
    assert is_advice_turn(_state(DATA)) is False


def test_is_advice_turn_persetujuan_proposal_tidak_diblokir() -> None:
    state = dict(begin_turn(user_message="Ya, sarankan yang paling cocok", approved_proposal_id="p-1"))
    assert is_advice_turn(state) is False


def test_is_advice_turn_intent_general_question() -> None:
    state = _state("tanya dulu soal datanya")
    state[TURN_INTENT_KEY] = GENERAL_QUESTION_INTENT
    assert is_advice_turn(state) is True


def test_is_advice_turn_klasifikasi_llm_keliru_tidak_membuka_gate() -> None:
    """``classify_turn`` bisa menyatakan permintaan eksplisit dengan evidence verbatim
    dari kalimat "jangan ubah"; itu tidak boleh membuka jalan ke agent mutasi."""
    state = _state(SARAN)
    record_turn_classification(
        state,
        intent="dashboard_modification",
        explicit_change_request=True,
        evidence="Jangan ubah dashboard dulu",
    )
    assert is_advice_turn(state) is True


# ---------------------------------------------------------------------------
# Alur ADK nyata lewat build_agents (guard deterministik)
# ---------------------------------------------------------------------------


async def test_saran_opini_tidak_transfer_ke_chart() -> None:
    """Kasus eval ``gate-saran-tanpa-mutasi``: Chart_Designer_Agent tidak boleh aktif."""
    turn = await run_turn(
        SARAN,
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME}), text("Saya sarankan bar chart.")],
        chart=[text("Chart mengeksekusi mutasi.")],
    )
    assert CHART_NAME not in turn.authors, turn.authors
    assert turn.authors == [ROOT_NAME], turn.authors
    assert "Saya sarankan bar chart." in turn.answer
    # Tool transfer tetap "dipanggil" model; guard menolaknya (tanpa raise).
    assert turn.transfers == []
    blocked = turn.response_of("transfer_to_agent")
    assert blocked is not None and blocked.get("blocked") is True, blocked


async def test_saran_opini_tidak_transfer_ke_blueprint_builder() -> None:
    turn = await run_turn(
        SARAN,
        root=[call("transfer_to_agent", {"agent_name": BUILDER_NAME}), text("Saran saya: bar chart.")],
    )
    assert BUILDER_NAME not in turn.authors, turn.authors
    assert turn.transfers == []


async def test_saran_opini_tidak_transfer_ke_insight() -> None:
    turn = await run_turn(
        SARAN,
        root=[call("transfer_to_agent", {"agent_name": INSIGHT_NAME}), text("Saran saya: bar chart.")],
    )
    assert INSIGHT_NAME not in turn.authors, turn.authors


async def test_saran_opini_tetap_boleh_transfer_ke_agent_baca_saja() -> None:
    """Guard hanya menahan agent pelaksana mutasi; Query/Profiler tetap boleh."""
    turn = await run_turn(
        SARAN,
        root=[call("transfer_to_agent", {"agent_name": QUERY_NAME})],
        query=[text("Query menjawab.")],
    )
    assert QUERY_NAME in turn.authors, turn.authors


async def test_larangan_mengubah_tanpa_saran_tidak_transfer_ke_chart() -> None:
    """Pesan "jangan ubah dashboard dulu" (tanpa kata saran) juga bukan giliran mutasi."""
    turn = await run_turn(
        "Jangan ubah dashboard dulu.",
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME}), text("Baik, dashboard tidak diubah.")],
        chart=[text("Chart mengeksekusi mutasi.")],
    )
    assert CHART_NAME not in turn.authors, turn.authors
    assert turn.transfers == []


async def test_pertanyaan_data_tetap_mentransfer_ke_query() -> None:
    turn = await run_turn(
        DATA,
        root=[call("transfer_to_agent", {"agent_name": QUERY_NAME})],
        query=[text("Total revenue 1500.")],
    )
    assert QUERY_NAME in turn.authors, turn.authors
    assert turn.transfers == [QUERY_NAME]


async def test_pertanyaan_kualitas_data_tetap_mentransfer_ke_profiler() -> None:
    turn = await run_turn(
        "Bagaimana kualitas data tabel customers? Kolom mana yang punya nilai kosong?",
        root=[call("transfer_to_agent", {"agent_name": PROFILER_NAME})],
        profiler=[text("Profil dikirim.")],
    )
    assert PROFILER_NAME in turn.authors, turn.authors


async def test_permintaan_mutasi_eksplisit_tetap_mentransfer_ke_chart() -> None:
    turn = await run_turn(
        MUTASI,
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME})],
        chart=[text("Chart dibuat.")],
    )
    assert CHART_NAME in turn.authors, turn.authors
    assert turn.transfers == [CHART_NAME]


async def test_rancang_dashboard_tetap_mentransfer_ke_architect() -> None:
    """``route-architect-blueprint``: pesan memuat "jangan"/"usulkan" tapi tujuannya
    Dashboard_Architect_Agent (perencana, tanpa mutasi) — tidak boleh diblokir."""
    turn = await run_turn(
        RANCANG,
        root=[call("transfer_to_agent", {"agent_name": ARCHITECT_NAME})],
        architect=[text("Rancangan diusulkan.")],
    )
    assert ARCHITECT_NAME in turn.authors, turn.authors
    assert turn.transfers == [ARCHITECT_NAME]


async def test_persetujuan_proposal_membuka_transfer_ke_chart() -> None:
    """Giliran persetujuan (``approved_proposal_id``) bukan giliran saran: jangan blokir."""
    pesan = "Ya, sarankan yang paling cocok dan terapkan."
    tanpa_persetujuan = await run_turn(
        pesan,
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME})],
        chart=[text("Chart dibuat.")],
    )
    assert CHART_NAME not in tanpa_persetujuan.authors, tanpa_persetujuan.authors

    dengan_persetujuan = await run_turn(
        pesan,
        root=[call("transfer_to_agent", {"agent_name": CHART_NAME})],
        chart=[text("Chart dibuat.")],
        approved_proposal_id="prop-1",
    )
    assert dengan_persetujuan.transfers == [CHART_NAME]
    assert CHART_NAME in dengan_persetujuan.authors


async def test_klasifikasi_llm_keliru_tidak_membuka_guard_transfer() -> None:
    """``classify_turn`` dengan evidence verbatim dari kalimat larangan tidak boleh
    membuka jalan ke Chart_Designer_Agent pada giliran saran."""
    turn = await run_turn(
        SARAN,
        root=[
            call(
                "classify_turn",
                {
                    "intent": "dashboard_modification",
                    "explicit_change_request": True,
                    "evidence": "Jangan ubah dashboard dulu",
                },
            ),
            call("transfer_to_agent", {"agent_name": CHART_NAME}),
            text("Saran saya: bar chart."),
        ],
        chart=[text("Chart mengeksekusi mutasi.")],
    )
    assert CHART_NAME not in turn.authors, turn.authors
    assert turn.transfers == []


def test_guard_terpasang_di_root() -> None:
    """Wiring: guard BUG A harus ada di Root_Agent (bukan hanya di prompt)."""
    from studio.agents.definitions import build_agents

    from tests.unit.agent_harness import dummy_services, fake_models

    root = build_agents(fake_models(), dummy_services())
    assert root.name == ROOT_NAME
    assert root.before_tool_callback is not None


def test_model_harness_tidak_bocor_aksi() -> None:
    """Sanity harness: skrip yang tidak dikonsumsi berarti agent tidak pernah menjawab."""
    chart = FakeModel.of(text("chart ran"))
    assert chart.remaining == 1


# ---------------------------------------------------------------------------
# Rute Root: permintaan SATU item tidak boleh ke Dashboard_Architect
# ---------------------------------------------------------------------------


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def _route_guard():
    return make_root_route_guard(
        architect_agent=ARCHITECT_NAME, chart_agent=CHART_NAME, insight_agent=INSIGHT_NAME
    )


def _ctx(message: str):
    from types import SimpleNamespace

    state: dict = {}
    begin_turn(state, user_message=message, invocation_id="inv-route")
    return SimpleNamespace(
        state=state,
        invocation_id="inv-route",
        user_content=SimpleNamespace(parts=[SimpleNamespace(text=message)]),
    )


def _transfer(guard, target: str, ctx):
    return guard(_Tool("transfer_to_agent"), {"agent_name": target}, ctx)


def test_satu_chart_tidak_diarahkan_ke_architect():
    out = _transfer(_route_guard(), ARCHITECT_NAME, _ctx(MUTASI))
    assert out is not None and out["code"] == SINGLE_ITEM_CODE
    assert CHART_NAME in out["message"] and out["transferred"] is False


def test_satu_insight_diarahkan_ke_insight():
    out = _transfer(
        _route_guard(),
        ARCHITECT_NAME,
        _ctx("Tambahkan Insight_Card tentang region dengan revenue terbesar."),
    )
    assert out is not None and out["code"] == SINGLE_ITEM_CODE and INSIGHT_NAME in out["message"]


def test_dashboard_utuh_tetap_boleh_ke_architect():
    assert _transfer(_route_guard(), ARCHITECT_NAME, _ctx(RANCANG)) is None


def test_giliran_saran_tidak_disentuh_guard_rute():
    assert _transfer(_route_guard(), ARCHITECT_NAME, _ctx(SARAN)) is None


def test_agent_lain_dan_tool_lain_tidak_disentuh():
    guard, ctx = _route_guard(), _ctx(MUTASI)
    assert _transfer(guard, CHART_NAME, ctx) is None
    assert guard(_Tool("run_sql"), {}, ctx) is None
