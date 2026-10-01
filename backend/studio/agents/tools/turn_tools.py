"""Agent_Tools giliran (Root_Agent): klasifikasi intent, usulan perubahan, dan penyajian.

Semua tool dibuat oleh :func:`make_turn_tools` (menutup satu
:class:`~studio.agents.tools.context.ToolServices`) dan dibungkus
:func:`~studio.agents.tools.guard.llm_output`. Tidak ada tool di modul ini yang
mengubah Dashboard, sehingga tidak melewati approval gate.

=================================  =============================================
Tool                               Perilaku
=================================  =============================================
``classify_turn``                  catat intent giliran (Req 8.2) dan buka
                                   approval gate bila ada permintaan eksplisit
                                   dengan ``evidence`` = kutipan verbatim pesan
                                   pengguna (≥ 3 karakter) (Req 21.3, 21.4)
``propose_changes``                simpan Proposal ``pending`` sesi (proposal
                                   pending lama kedaluwarsa) lalu emit
                                   ``approval.request`` (Req 21.2)
``present_profile_summary``        emit ``profile.summary`` (Req 21.1)
``present_relation_candidates``    emit ``relation.candidates`` (Req 7.3, 21.1)
=================================  =============================================

Event chat dikembalikan di key ``chat_events`` hasil tool
(``tools/context.py``: :data:`~studio.agents.tools.context.CHAT_EVENTS_KEY`) dan
diteruskan runner (18.10) ke stream chat.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.tools import ToolContext

from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    ToolServices,
    chat_event,
    resolve_dataset,
    session_id_of,
    workspace_id_of,
)
from studio.agents.tools.guard import llm_output, ok_result
from studio.agents.turn_policy import (
    MIN_EVIDENCE_LENGTH,
    bind_tool_context,
    record_turn_classification,
)
from studio.api.errors import StudioError
from studio.api.schemas import RelationOut

__all__ = [
    "INTENTS",
    "MAX_THEMES",
    "TURN_TOOL_NAMES",
    "make_turn_tools",
]

#: Intent pesan pengguna (Req 8.2).
INTENTS: tuple[str, ...] = (
    "profiling",
    "query_analysis",
    "chart_design",
    "insight",
    "dashboard_modification",
    "general_question",
)

TURN_TOOL_NAMES: tuple[str, ...] = (
    "classify_turn",
    "propose_changes",
    "present_profile_summary",
    "present_relation_candidates",
)

MAX_THEMES = 10

_TRUE_STRINGS = frozenset({"true", "1", "yes", "ya"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def _required_text(value: Any, name: str) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    if not text.strip():
        raise StudioError(
            "INVALID_ARGUMENT", f"{name} tidak boleh kosong.", {"argument": name}, http_status=422
        )
    return text.strip()


def _themes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise StudioError(
            "INVALID_ARGUMENT", "themes harus berupa daftar teks.", {"argument": "themes"},
            http_status=422,
        )
    out: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text and text not in out:
            out.append(text)
    return out[:MAX_THEMES]


def make_turn_tools(services: ToolServices) -> dict[str, Callable[..., Any]]:
    """Buat tool giliran yang terikat ke ``services``; kunci dict = nama tool."""
    repos = services.repos
    guard = llm_output(max_sample_rows=services.sample_rows)

    @guard
    async def classify_turn(
        intent: str,
        explicit_change_request: bool,
        tool_context: ToolContext,
        evidence: str | None = None,
    ) -> dict[str, Any]:
        """Klasifikasikan pesan pengguna giliran ini. Panggil pertama kali setiap giliran.

        Args:
            intent: salah satu dari profiling, query_analysis, chart_design, insight,
                dashboard_modification, general_question.
            explicit_change_request: true HANYA bila pengguna secara eksplisit meminta
                atau menyetujui perubahan Dashboard pada pesan ini.
            evidence: kutipan verbatim (minimal 3 karakter) dari pesan pengguna yang
                menunjukkan permintaan tersebut; wajib bila explicit_change_request true.

        ``mutation_allowed`` menyatakan apakah tool pengubah Dashboard boleh dipanggil
        pada giliran ini. Bila false, tawarkan perubahan lewat propose_changes.
        """
        normalized = str(intent or "").strip().lower()
        if normalized not in INTENTS:
            raise StudioError(
                "INVALID_INTENT",
                f"Intent '{intent}' tidak dikenal; pilih salah satu: {', '.join(INTENTS)}.",
                {"intent": intent, "valid_intents": list(INTENTS)},
                http_status=422,
            )
        state = bind_tool_context(tool_context)
        explicit = _as_bool(explicit_change_request)
        result = record_turn_classification(
            state,
            intent=normalized,
            explicit_change_request=explicit,
            evidence=evidence if isinstance(evidence, str) else None,
        )
        payload = result.to_dict()
        if explicit and not result.evidence_valid:
            payload["note"] = (
                "evidence tidak valid: harus kutipan verbatim dari pesan pengguna giliran ini "
                f"(minimal {MIN_EVIDENCE_LENGTH} karakter, bukan hanya spasi). Tanpa itu, "
                "perubahan Dashboard harus ditawarkan lewat propose_changes."
            )
        elif not result.mutation_allowed:
            payload["note"] = (
                "Tool pengubah Dashboard tidak diizinkan pada giliran ini; tawarkan perubahan "
                "lewat propose_changes bila perlu."
            )
        return ok_result(**payload)

    @guard
    async def propose_changes(
        summary: str, themes: list[str], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Tawarkan perubahan/draft Dashboard kepada pengguna untuk disetujui.

        Args:
            summary: ringkasan perubahan yang diusulkan (bahasa pengguna).
            themes: daftar tema analisis yang disarankan (maks 10).

        Pengguna melihat kartu persetujuan; JANGAN memanggil tool pengubah Dashboard
        sebelum pengguna menyetujui (persetujuan datang sebagai pesan berikutnya).
        """
        text = _required_text(summary, "summary")
        theme_list = _themes(themes)
        session_id = session_id_of(tool_context)
        # Hanya usulan terbaru yang dapat disetujui.
        await repos.proposals.expire_pending(session_id)
        proposal = await repos.proposals.create(session_id, text)
        data = {"proposal_id": proposal.id, "summary": text, "themes": theme_list}
        return ok_result(
            proposal_id=proposal.id,
            summary=text,
            themes=theme_list,
            status=proposal.status,
            message="Usulan ditampilkan ke pengguna; tunggu persetujuan sebelum mengubah Dashboard.",
            **{CHAT_EVENTS_KEY: [chat_event("approval.request", data)]},
        )

    @guard
    async def present_profile_summary(
        dataset: str, summary: str, tool_context: ToolContext
    ) -> dict[str, Any]:
        """Tampilkan ringkasan profil satu Dataset di Chat_Panel.

        Args:
            dataset: nama tabel atau dataset_id.
            summary: ringkasan profil (jumlah baris/kolom, peran kolom, kualitas data)
                berdasarkan statistik get_dataset_profile; jangan mengarang angka.
        """
        ws_id = workspace_id_of(tool_context)
        text = _required_text(summary, "summary")
        record = await resolve_dataset(repos, ws_id, dataset)
        data = {"dataset_id": record.id, "summary": text}
        return ok_result(
            dataset_id=record.id,
            table_name=record.table_name,
            **{CHAT_EVENTS_KEY: [chat_event("profile.summary", data)]},
        )

    @guard
    async def present_relation_candidates(
        tool_context: ToolContext, dataset: str | None = None
    ) -> dict[str, Any]:
        """Tampilkan Relation_Candidate Workspace sebagai kartu Konfirmasi/Tolak.

        Args:
            dataset: opsional nama tabel/dataset_id; hanya kandidat yang melibatkan
                Dataset ini yang ditampilkan.

        Kandidat dihitung lebih dulu oleh compute_relation_candidates.
        """
        ws_id = workspace_id_of(tool_context)
        records = await repos.relations.list(ws_id, status="candidate")
        if dataset and dataset.strip():
            ds_id = (await resolve_dataset(repos, ws_id, dataset)).id
            records = [r for r in records if ds_id in (r.from_dataset_id, r.to_dataset_id)]
        relations = [RelationOut.from_record(r).model_dump(mode="json") for r in records]
        candidates = [
            {
                "relation_id": r.id,
                "from": f"{r.from_table}.{r.from_column}",
                "to": f"{r.to_table}.{r.to_column}",
                "cardinality": r.cardinality,
                "overlap_pct": r.overlap_pct,
            }
            for r in records
        ]
        if not relations:
            return ok_result(
                count=0,
                candidates=[],
                message="Tidak ada Relation_Candidate yang menunggu konfirmasi.",
            )
        return ok_result(
            count=len(relations),
            candidates=candidates,
            message="Kandidat relasi ditampilkan; tunggu pengguna mengonfirmasi atau menolak.",
            **{CHAT_EVENTS_KEY: [chat_event("relation.candidates", relations)]},
        )

    tools = {
        "classify_turn": classify_turn,
        "propose_changes": propose_changes,
        "present_profile_summary": present_profile_summary,
        "present_relation_candidates": present_relation_candidates,
    }
    assert tuple(tools) == TURN_TOOL_NAMES
    return tools
