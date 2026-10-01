"""Akses service & workspace untuk Agent_Tools.

Tool dibuat oleh factory (``make_data_tools(services)``, ``make_dashboard_tools``
...) yang menutup (closure) satu :class:`ToolServices`. Identitas Workspace dan
Dashboard dibaca dari session state ADK per panggilan tool:

======================  =========================================================
Key session state       Arti (diisi runner 18.10 saat membuat sesi ADK)
======================  =========================================================
``workspace_id``        Workspace pemilik sesi chat (:data:`STATE_WORKSPACE_ID`)
``dashboard_id``        Dashboard aktif Workspace (:data:`STATE_DASHBOARD_ID`)
``query:last_id``       ``query_id`` hasil ``run_sql`` terakhir (:data:`STATE_LAST_QUERY_ID`)
``chart:last_id``       id chart terakhir yang ditambah/diubah agent (:data:`STATE_LAST_CHART_ID`)
``insight:last_id``     id Insight_Card terakhir yang ditambah/diubah agent
                        (:data:`STATE_LAST_INSIGHT_ID`)
======================  =========================================================

Key tanpa prefiks ``temp:`` sehingga dipersist oleh Session_Service bersama sesi.

Event chat dari tool
--------------------

Tool yang harus memunculkan event SSE chat khusus (``approval.request``,
``profile.summary``, ``relation.candidates``) menaruhnya di key
:data:`CHAT_EVENTS_KEY` pada hasil tool: ``[{"event": <tipe>, "data": {...}}]``
(lihat :func:`chat_event`). Runner (18.10) membaca key ini dari function
response ADK dan meneruskannya ke stream chat apa adanya, berurutan, setelah
event ``tool.result``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from studio.api.errors import StudioError
from studio.core.identifiers import make_table_name
from studio.core.privacy import DEFAULT_SAMPLE_ROWS, PrivacySettings
from studio.data.worker import DEFAULT_TIMEOUT_S

if TYPE_CHECKING:
    from studio.data.engine import DataEngine
    from studio.data.profiler import Profiler
    from studio.events.bus import EventBus
    from studio.store.dashboard_store import DashboardStore
    from studio.store.repos import DatasetRecord, Repositories

STATE_WORKSPACE_ID = "workspace_id"
STATE_DASHBOARD_ID = "dashboard_id"
STATE_LAST_QUERY_ID = "query:last_id"
STATE_LAST_CHART_ID = "chart:last_id"
STATE_LAST_INSIGHT_ID = "insight:last_id"
#: Fallback id sesi chat bila ``tool_context.session`` tidak tersedia.
STATE_SESSION_ID = "session_id"

#: Key hasil tool berisi event SSE chat yang diteruskan runner.
CHAT_EVENTS_KEY = "chat_events"

WORKSPACE_NOT_BOUND = "WORKSPACE_NOT_BOUND"
SESSION_NOT_BOUND = "SESSION_NOT_BOUND"


@dataclass(frozen=True)
class ToolServices:
    """Dependensi backend yang dipakai Agent_Tools (satu instance per aplikasi)."""

    repos: Repositories
    engine: DataEngine
    profiler: Profiler
    dashboard_store: DashboardStore | None = None
    bus: EventBus | None = None
    #: Jumlah Sample_Rows per Dataset yang boleh dikirim ke LLM (Req 27.2).
    sample_rows: int = DEFAULT_SAMPLE_ROWS
    query_timeout_s: float = DEFAULT_TIMEOUT_S
    #: Anggaran karakter blok konteks semantik per agent (Req 33.3).
    semantic_budget: int = 12_000

    @classmethod
    def from_app_state(cls, state: Any) -> ToolServices:
        """Bangun dari ``app.state`` FastAPI (lihat ``studio/app.py``)."""
        settings = getattr(state, "settings", None)
        return cls(
            repos=state.repos,
            engine=state.engine,
            profiler=state.profiler,
            dashboard_store=getattr(state, "dashboard_store", None),
            bus=getattr(state, "bus", None),
            query_timeout_s=float(getattr(settings, "query_timeout_s", DEFAULT_TIMEOUT_S)),
            semantic_budget=int(getattr(settings, "semantic_context_budget", 12_000)),
        )

    def privacy_for(self, dataset: Any) -> PrivacySettings:
        """Pengaturan Privacy_Guard untuk satu Dataset (toggle ``privacy_no_samples``)."""
        return PrivacySettings(
            sample_rows=self.sample_rows,
            no_samples=bool(getattr(dataset, "privacy_no_samples", False)),
        )


def workspace_id_of(tool_context: Any) -> str:
    """``workspace_id`` dari session state; ``WORKSPACE_NOT_BOUND`` bila tidak ada."""
    value = tool_context.state.get(STATE_WORKSPACE_ID)
    if not isinstance(value, str) or not value:
        raise StudioError(
            WORKSPACE_NOT_BOUND,
            "Sesi agent tidak terikat ke Workspace (state 'workspace_id' kosong).",
            http_status=500,
        )
    return value


def session_id_of(tool_context: Any) -> str:
    """Id sesi chat (= id sesi ADK) dari ``tool_context.session``; fallback state ``session_id``."""
    session = getattr(tool_context, "session", None)
    value = getattr(session, "id", None) if session is not None else None
    if not isinstance(value, str) or not value:
        value = tool_context.state.get(STATE_SESSION_ID)
    if not isinstance(value, str) or not value:
        raise StudioError(
            SESSION_NOT_BOUND,
            "Tool ini membutuhkan sesi chat aktif (id sesi tidak tersedia).",
            http_status=500,
        )
    return value


async def resolve_dataset(repos: Repositories, workspace_id: str, dataset: str) -> DatasetRecord:
    """Dataset Workspace dari nama tabel SQL atau ``dataset_id``; ``UNKNOWN_DATASET`` bila tidak ada."""
    ref = (dataset or "").strip()
    record = await repos.datasets.get_by_table(workspace_id, ref)
    if record is None and ref:
        # LLM sering menyebut nama file upload ("Sales Q1.xlsx"); normalisasi sama seperti ingestion.
        normalized = make_table_name(ref, ())
        if normalized != ref:
            record = await repos.datasets.get_by_table(workspace_id, normalized)
    if record is None:
        record = await repos.datasets.get_or_none(ref)
    if record is None or record.workspace_id != workspace_id:
        tables = await repos.datasets.list_table_names(workspace_id)
        raise StudioError(
            "UNKNOWN_DATASET",
            f"Dataset '{dataset}' tidak ditemukan di Workspace.",
            {"dataset": dataset, "tables": tables},
            http_status=404,
        )
    return record


def chat_event(event: str, data: Any) -> dict[str, Any]:
    """Satu entri :data:`CHAT_EVENTS_KEY`; ``event`` harus tipe event chat yang dikenal."""
    from studio.events.sse import CHAT_EVENT_TYPES

    if event not in CHAT_EVENT_TYPES:
        raise ValueError(f"Tipe event chat tidak dikenal: {event!r}")
    return {"event": event, "data": data}


__all__ = [
    "CHAT_EVENTS_KEY",
    "SESSION_NOT_BOUND",
    "STATE_DASHBOARD_ID",
    "STATE_LAST_CHART_ID",
    "STATE_LAST_INSIGHT_ID",
    "STATE_LAST_QUERY_ID",
    "STATE_SESSION_ID",
    "STATE_WORKSPACE_ID",
    "WORKSPACE_NOT_BOUND",
    "ToolServices",
    "chat_event",
    "resolve_dataset",
    "session_id_of",
    "workspace_id_of",
]
