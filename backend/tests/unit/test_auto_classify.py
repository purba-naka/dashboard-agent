"""Gate terbuka otomatis hanya untuk pesan perintah eksplisit (Req 21.3 jalur b, tanpa LLM)."""

import pytest

from studio.agents.turn_policy import begin_turn, is_mutation_allowed, mutation_gate_error


def _gate(message: str):
    state: dict = {}
    begin_turn(state, user_message=message, invocation_id="i")
    return mutation_gate_error(state, "add_chart"), state


@pytest.mark.parametrize(
    "msg",
    ["coba buat chart dan insight untuk wilayah sumatera", "lanjutkan", "ya boleh", "eksekusi",
     "visualisasikan ini", "Tambahkan juga chart revenue per channel"],
)
def test_perintah_eksplisit_membuka_gate(msg):
    err, state = _gate(msg)
    assert err is None and is_mutation_allowed(state)


@pytest.mark.parametrize(
    "msg",
    ["halo", "provinsi mana yang ntp-nya tertinggi?", "jangan ubah dashboard dulu, buat saran saja",
     "menurutmu chart apa yang cocok", "bagaimana cara buat chart?", ""],
)
def test_bukan_perintah_tetap_tertutup(msg):
    err, state = _gate(msg)
    assert err is not None and err["error"]["code"] == "APPROVAL_REQUIRED"
    assert not is_mutation_allowed(state)


def test_judul_sesi_dari_pesan_pertama():
    from studio.api.chat import _title_from

    assert _title_from("  halo\n  dunia ") == "halo dunia"
    assert _title_from("") == "Percakapan"
    long = _title_from("x" * 100)
    assert len(long) == 40 and long.endswith("\u2026")
