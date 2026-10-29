"""Jaring pengaman deterministik untuk transfer antar-agent dan ``NO_QUERY``.

ADK menawarkan tool ``transfer_to_agent`` otomatis ke setiap ``LlmAgent`` yang
punya sub-agent, dan isi prompt tidak mengikat model. Dua callback di modul ini
menutup dua kegagalan yang terbukti di trace nyata
(``tests/eval/results/run-final-medium.json``):

* :func:`make_root_transfer_guard` — ``before_tool_callback`` Root_Agent (BUG A).
  Giliran saran/opini (:func:`~studio.agents.turn_policy.is_advice_turn`) tidak
  boleh diserahkan ke agent **pelaksana mutasi** Dashboard (Chart / Insight /
  Blueprint_Builder); ``transfer_to_agent`` ditolak dengan respons tool pengganti
  yang menyuruh Root menjawab sendiri — tidak di-``raise``, sehingga giliran
  selesai dengan jawaban, bukan buntu. Yang tetap boleh pada giliran saran:
  transfer ke agent baca-saja (Query / Profiler) dan ke perencana (Architect),
  yang tidak menyentuh Dashboard. Giliran dengan permintaan perubahan eksplisit
  atau persetujuan Proposal tidak pernah ditahan.
* :func:`make_no_query_recovery` — ``after_tool_callback`` Chart / Slot_Builder
  (BUG B). Bila tool mengembalikan ``NO_QUERY`` (``data_tools.get_query_schema``
  atau ``dashboard_tools.load_query``), hasil tool diganti versi yang memuat
  LANGKAH berikutnya secara eksplisit, sehingga giliran tidak berakhir buntu.
  Catatan desain: pada arsitektur agent-as-tool (``definitions.py``) Query_Agent
  adalah TOOL milik Chart_Designer_Agent dan ``disallow_transfer_to_peers=True`` —
  transfer ke Query_Agent justru akan ditolak ADK, jadi pemulihan tidak boleh
  memaksa transfer; ia menyebut tool yang harus dipanggil. Slot_Builder (yang
  juga dilarang transfer) diberi instruksi menjalankan ``run_sql`` sendiri.
  Pemulihan hanya sekali per giliran agar tidak terjadi ping-pong.

Keduanya tetap menjalankan jaring Privacy_Guard ``> 200 baris``
(:func:`studio.agents.tools.guard.after_tool_callback`) lebih dulu.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from studio.agents.tools.guard import after_tool_callback as row_limit_guard
from studio.agents.turn_policy import (
    NO_QUERY_RECOVERY_KEY,
    is_explicit_change_message,
    USER_MESSAGE_KEY,
    TRANSFER_BLOCKED_KEY,
    bind_tool_context,
    is_advice_turn,
)

__all__ = [
    "BLOCKED_TRANSFER_CODE",
    "SINGLE_ITEM_CODE",
    "make_root_route_guard",
    "NO_QUERY",
    "TRANSFER_TOOL",
    "blocked_transfer_result",
    "make_no_query_recovery",
    "make_root_transfer_guard",
]

TRANSFER_TOOL = "transfer_to_agent"
NO_QUERY = "NO_QUERY"
#: Kode hasil tool pengganti saat rute satu-item diarahkan ulang (bukan error).
SINGLE_ITEM_CODE = "SINGLE_ITEM_ROUTE"

#: Kode hasil tool pengganti saat transfer ditolak guard giliran saran.
BLOCKED_TRANSFER_CODE = "SUGGESTION_TURN_NO_TRANSFER"


def _tool_name(tool: Any) -> str:
    return getattr(tool, "name", None) or getattr(tool, "__name__", "tool")


def _error_code(tool_response: Any) -> str | None:
    """``error.code`` dari hasil tool (``{"ok": False, "error": {...}}``)."""
    if not isinstance(tool_response, Mapping):
        return None
    error = tool_response.get("error")
    code = error.get("code") if isinstance(error, Mapping) else None
    return code if isinstance(code, str) else None


# ---------------------------------------------------------------------------
# BUG A — giliran saran/opini tidak boleh masuk ke agent pelaksana mutasi
# ---------------------------------------------------------------------------


def blocked_transfer_result(target: str, attempt: int = 1) -> dict[str, Any]:
    """Respons tool pengganti untuk transfer yang ditolak (bukan error: ``ok`` True).

    ``ok`` sengaja True: penolakan ini bukan kegagalan sistem, jadi stream chat
    tidak perlu memunculkan frame ``error``; yang penting instruksinya jelas.
    """
    if attempt > 1:
        instruction = (
            "Transfer sudah diblokir berulang kali: BERHENTI memanggil tool dan jawab "
            "pengguna SEKARANG dalam teks."
        )
    else:
        instruction = (
            "Jawab pengguna SENDIRI dalam teks tanpa memanggil tool lain: beri rekomendasi "
            "singkat berdasarkan konteks data yang sudah ada, dan sebutkan data tambahan "
            "bila memang diperlukan."
        )
    return {
        "ok": True,
        "blocked": True,
        "transferred": False,
        "code": BLOCKED_TRANSFER_CODE,
        "message": (
            f"Transfer ke {target} DIBATALKAN sistem: giliran ini pertanyaan saran/pendapat, "
            f"bukan permintaan perubahan Dashboard. Jangan panggil {TRANSFER_TOOL} lagi. "
            f"{instruction}"
        ),
    }


def make_root_transfer_guard(
    blocked_targets: Iterable[str],
) -> Callable[[Any, dict[str, Any], Any], dict[str, Any] | None]:
    """``before_tool_callback`` Root_Agent: tolak transfer ke agent mutasi pada giliran saran.

    ``blocked_targets`` = nama agent yang MENGUBAH Dashboard (Chart, Insight,
    Blueprint_Builder). Agent baca-saja/perencana sengaja tidak termasuk.
    """
    blocked = frozenset(blocked_targets)

    def before_tool_callback(
        tool: Any, args: dict[str, Any], tool_context: Any
    ) -> dict[str, Any] | None:
        if _tool_name(tool) != TRANSFER_TOOL:
            return None
        target = str((args or {}).get("agent_name") or "")
        if target not in blocked:
            return None
        state = bind_tool_context(tool_context)
        if not is_advice_turn(state):
            return None
        attempt = int(state.get(TRANSFER_BLOCKED_KEY) or 0) + 1
        state[TRANSFER_BLOCKED_KEY] = attempt
        return blocked_transfer_result(target, attempt)

    return before_tool_callback


# ---------------------------------------------------------------------------
# BUG B — NO_QUERY tidak boleh membuat giliran buntu
# ---------------------------------------------------------------------------


def make_no_query_recovery(
    *,
    retry_tool: str | None = None,
    inner: Callable[[Any, dict[str, Any], Any, Any], dict[str, Any] | None] = row_limit_guard,
) -> Callable[[Any, dict[str, Any], Any, Any], dict[str, Any] | None]:
    """``after_tool_callback`` Chart/Slot_Builder: ``NO_QUERY`` → langkah berikutnya eksplisit.

    ``retry_tool``: nama tool yang harus dipanggil agent ini untuk membuat query
    (``Query_Agent`` — sub-agent ``single_turn`` yang diekspos ADK sebagai tool
    Chart). ``None`` = agent menjalankan ``run_sql`` sendiri (Slot_Builder).
    """
    def after_tool_callback(
        tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any
    ) -> dict[str, Any] | None:
        # Privacy_Guard dulu: hasil > 200 baris tetap diganti error-nya.
        replaced = inner(tool, args, tool_context, tool_response)
        if replaced is not None:
            return replaced
        if _error_code(tool_response) != NO_QUERY:
            return None

        name = _tool_name(tool)
        state = bind_tool_context(tool_context)
        if state.get(NO_QUERY_RECOVERY_KEY) is not None:
            # Sudah dipulihkan sekali di giliran ini: jangan memicu loop.
            return {
                "ok": False,
                "error": {
                    "code": NO_QUERY,
                    "message": (
                        f"Masih belum ada query untuk {name} meski pemulihan sudah dijalankan "
                        "sekali pada giliran ini. Hentikan percobaan dan sampaikan kepada pengguna "
                        "bahwa query yang dibutuhkan belum berhasil dibuat."
                    ),
                    "details": {"tool": name},
                },
            }
        state[NO_QUERY_RECOVERY_KEY] = name

        next_action = _next_action(retry_tool, state)
        return {
            "ok": False,
            "recovered": True,
            "next_action": next_action,
            "error": {
                "code": NO_QUERY,
                "message": (
                    f"Belum ada query untuk {name}. JANGAN akhiri giliran: {next_action['instruction']} "
                    f"Setelah query tersedia, ulangi {name}."
                ),
                "details": {"tool": name, "next_action": next_action},
            },
        }

    return after_tool_callback


def _next_action(retry_tool: str | None, state: Any) -> dict[str, Any]:
    """Langkah berikutnya untuk agent yang menerima ``NO_QUERY``."""
    if retry_tool:
        return {
            "tool": retry_tool,
            "instruction": (
                f"panggil tool `{retry_tool}` sekarang dengan `request` berisi query yang "
                f"dibutuhkan (tool ini membuat query lalu kendali kembali ke Anda)."
            ),
        }
    return {
        "tool": "run_sql",
        "instruction": (
            "jalankan `run_sql` lebih dulu untuk membuat query yang dibutuhkan, lalu ulangi "
            "tool ini dengan `query_id` hasilnya."
        ),
    }


# ---------------------------------------------------------------------------
# Rute Root: permintaan SATU item tidak boleh ke perancang dashboard
# ---------------------------------------------------------------------------


_CHART_WORDS = ("chart", "grafik", "kpi", "heatmap", "visualisasi")
_INSIGHT_WORDS = ("insight",)
#: Kata yang menandakan permintaan dashboard UTUH (bukan satu item).
#: Kata "dashboard" saja TIDAK cukup: "tambahkan chart X ke dashboard" adalah satu item.
#: Yang menandakan dashboard UTUH adalah kata perencanaan atau kuantitas.
_WHOLE_DASHBOARD_WORDS = (
    "rancang",
    "rancangan",
    "usulkan",
    "usulan",
    "lengkapi",
    "beberapa chart",
    "beberapa kpi",
    "dashboard baru",
    "dashboard lengkap",
    "dashboard utuh",
)


def _wants(message: str, words: tuple[str, ...]) -> bool:
    low = (message or "").lower()
    return any(w in low for w in words)


def make_root_route_guard(
    *,
    architect_agent: str,
    chart_agent: str,
    insight_agent: str,
) -> Callable[[Any, dict[str, Any], Any], dict[str, Any] | None]:
    """``before_tool_callback`` Root_Agent: permintaan SATU chart/KPI/insight tidak ke Architect.

    Gejala (trace nyata ``run-after-evalset-fix.json``): "Tambahkan bar chart total
    revenue per channel" dikirim Root ke Dashboard_Architect_Agent, yang lalu
    memanggil ``propose_dashboard_plan`` dan memunculkan kartu persetujuan —
    pengguna minta satu chart, dapat kartu usulan. ``root.md`` sudah melarang
    ("Permintaan SATU chart/KPI/insight bukan dashboard utuh") tetapi model tetap
    melakukannya, jadi rutenya dikoreksi di kode.

    Yang ditahan hanya transfer ke perancang saat giliran meminta SATU item
    (ada kata chart/KPI/insight, tanpa kata dashboard-utuh) dan pesan berisi
    perintah perubahan eksplisit. Permintaan dashboard utuh, giliran saran
    (tidak eksplisit), dan transfer ke agent lain tidak disentuh.
    """
    architect = architect_agent

    def before_tool_callback(
        tool: Any, args: dict[str, Any], tool_context: Any
    ) -> dict[str, Any] | None:
        if _tool_name(tool) != TRANSFER_TOOL:
            return None
        if str((args or {}).get("agent_name") or "") != architect:
            return None
        message = bind_tool_context(tool_context).get(USER_MESSAGE_KEY)
        if not is_explicit_change_message(message):
            return None
        if _wants(message, _WHOLE_DASHBOARD_WORDS):
            return None
        if _wants(message, _INSIGHT_WORDS):
            target = insight_agent
        elif _wants(message, _CHART_WORDS):
            target = chart_agent
        else:
            return None
        return {
            "ok": True,
            "blocked": True,
            "transferred": False,
            "code": SINGLE_ITEM_CODE,
            "message": (
                f"Rute dikoreksi: pengguna minta SATU item, bukan dashboard utuh. Transfer ke "
                f"{target} sekarang. Dashboard_Architect_Agent hanya untuk merancang dashboard "
                "utuh atau mengusulkan Blueprint. Jangan memanggil propose_dashboard_plan."
            ),
        }

    return before_tool_callback
