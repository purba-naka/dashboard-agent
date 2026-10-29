"""Definisi ``LlmAgent`` ADK dan perakitannya (Req 8.1, 35.1).

``build_agents(models, services)`` mengembalikan Root_Agent dengan
``sub_agents=[profiler, architect, query, chart, insight, blueprint_builder]``.
``Blueprint_Builder_Agent`` (non-LLM) meloop slot Blueprint dan memanggil
``Slot_Builder_Agent`` per slot (model ``chart``, tanpa riwayat percakapan). Model per agent
diambil dari Model_Gateway (``model_gateway.build_models`` → ``app.state.models``);
tools dari factory di ``tools/``; ``after_tool_callback`` menolak hasil > 200
baris (``guard.after_tool_callback``); setiap agent memakai InstructionProvider
``context.make_instruction`` yang menyisipkan konteks data & semantik sesuai
cakupannya (Req 33.1).

Query_Agent punya DUA instance (nama ADK sama, ``Query_Agent``):

* ``query`` — mode ``chat``, sub-agent Root_Agent: target ``transfer_to_agent``
  untuk pertanyaan data murni; ia kembali ke Root (satu-satunya target transfer).
* ``query_for_chart`` — mode ``single_turn``, sub-agent Chart_Designer_Agent,
  sehingga ADK mengeksposnya sebagai TOOL ``Query_Agent`` milik Chart. Saat
  dipanggil sebagai tool, kendali kembali ke Chart_Designer_Agent secara
  struktural: tidak ada ``transfer_to_agent`` yang bisa salah dipilih LLM dan
  tidak ada hop transfer tambahan. Ini menggantikan instruksi "setelah run_sql,
  transfer KEMBALI ke agent pemanggil" yang dulu bergantung pada pilihan LLM
  (LLM tidak tahu siapa pemanggilnya).

Kunci agent pada mapping ``models`` mengikuti ``core.models_config.AGENT_ENV``;
``architect`` jatuh ke model ``root`` bila tidak tersedia (mis. test lama).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.adk.agents import LlmAgent

from studio.agents.blueprint_builder import BUILDER_NAME, SLOT_BUILDER_NAME, BlueprintBuilderAgent
from studio.agents.context import make_instruction
from studio.agents.tools.architect_tools import make_architect_tools
from studio.agents.tools.data_tools import make_data_tools
from studio.agents.tools.dashboard_tools import make_dashboard_tools
from studio.agents.tools.guard import after_tool_callback
from studio.agents.tools.knowledge_tools import make_knowledge_tools
from studio.agents.tools.semantic_tools import make_semantic_tools
from studio.agents.tools.transfer_guard import (
    make_no_query_recovery,
    make_root_route_guard,
    make_root_transfer_guard,
)
from studio.agents.tools.turn_tools import make_turn_tools

if TYPE_CHECKING:
    from collections.abc import Mapping

    from google.adk.models.lite_llm import LiteLlm

    from studio.agents.tools.context import ToolServices

# Nama agent ADK (huruf/underscore; dipakai untuk transfer antar-agent).
ROOT_NAME = "Root_Agent"
PROFILER_NAME = "Data_Profiler_Agent"
ARCHITECT_NAME = "Dashboard_Architect_Agent"
QUERY_NAME = "Query_Agent"
CHART_NAME = "Chart_Designer_Agent"
INSIGHT_NAME = "Insight_Agent"

#: Deskripsi agent (dipakai ADK sebagai deskripsi TOOL bila agent dipanggil inline).
QUERY_DESCRIPTION = (
    "Menulis dan menjalankan SQL untuk pertanyaan data. Dipanggil lewat transfer: "
    "kembalikan jawaban lengkap lalu kendali kembali ke Root_Agent."
)
QUERY_TOOL_DESCRIPTION = (
    "Subrutin SQL: tulis dan jalankan SATU query untuk kebutuhan chart/KPI, lalu kembalikan "
    "query_id, kolom hasil, dan ringkasan angka. Panggil bila belum ada query yang cocok; "
    "kendali kembali ke Anda (Chart_Designer_Agent) setelah tool ini selesai."
)


def _pick(tools: dict[str, object], names: tuple[str, ...]) -> list[object]:
    return [tools[name] for name in names]


def build_agents(
    models: Mapping[str, LiteLlm], services: ToolServices
) -> LlmAgent:
    """Rakit Root_Agent lengkap dengan sub-agent. Kembalikan Root."""
    data_tools = make_data_tools(services)
    dashboard_tools = make_dashboard_tools(services)
    turn_tools = make_turn_tools(services)
    semantic_tools = make_semantic_tools(services)
    knowledge_tools = make_knowledge_tools(services)
    architect_tools = make_architect_tools(services)

    profiler = LlmAgent(
        name=PROFILER_NAME,
        model=models["profiler"],
        instruction=make_instruction("profiler", services),
        tools=_pick(
            data_tools,
            ("get_dataset_profile", "set_column_roles", "compute_relation_candidates"),
        ),
        after_tool_callback=after_tool_callback,
    )
    architect = LlmAgent(
        name=ARCHITECT_NAME,
        model=models.get("architect", models["root"]),
        instruction=make_instruction("architect", services),
        tools=[
            knowledge_tools["get_bi_knowledge"],
            semantic_tools["get_semantic_model"],
            semantic_tools["search_semantic"],
            dashboard_tools["get_dashboard_state"],
            architect_tools["propose_dashboard_plan"],
            dashboard_tools["update_brief"],
            architect_tools["review_dashboard"],
            turn_tools["propose_changes"],
        ],
        after_tool_callback=after_tool_callback,
    )
    query_tools = [
        *_pick(data_tools, ("list_tables", "run_sql")),
        semantic_tools["search_semantic"],
        semantic_tools["find_verified_queries"],
    ]
    query = LlmAgent(
        name=QUERY_NAME,
        model=models["query"],
        description=QUERY_DESCRIPTION,
        instruction=make_instruction("query", services),
        tools=list(query_tools),
        # Pertanyaan data murni: satu-satunya target transfer adalah Root_Agent
        # (tak ada agent lain yang lebih tepat untuk "kembalikan hasil").
        disallow_transfer_to_peers=True,
        after_tool_callback=after_tool_callback,
    )
    # Query_Agent sebagai TOOL Chart (agent-as-tool ADK, ``mode="single_turn"``).
    # ADK mengekspos sub-agent ``single_turn`` sebagai tool milik induk dan
    # menjalankannya inline (``Context.run_node``): event agent/tool-nya tetap
    # mengalir ke stream SSE, state sesi (mis. ``query:last_id``) tetap dibagi,
    # dan kendali kembali ke Chart_Designer_Agent TANPA ``transfer_to_agent``.
    query_for_chart = LlmAgent(
        name=QUERY_NAME,
        model=models["query"],
        description=QUERY_TOOL_DESCRIPTION,
        instruction=make_instruction("query", services),
        tools=list(query_tools),
        mode="single_turn",
        # Tanpa dua flag ini ADK tetap menawarkan ``transfer_to_agent`` ke parent
        # meski agent-nya single_turn → jalur "pilih agent" masih ada.
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        after_tool_callback=after_tool_callback,
    )
    # Canvas OpenUI (D5, D11): satu jalur tulis `apply_canvas_patch` per peran.
    openui = getattr(services, "canvas_engine", "legacy") == "openui"
    if openui:
        architect.tools.append(dashboard_tools["apply_canvas_patch:layout"])
    chart_writers = (
        [dashboard_tools["get_dashboard_state"], dashboard_tools["apply_canvas_patch:data"]]
        if openui
        else [
            dashboard_tools["add_chart_from_template"],
            dashboard_tools["add_chart"],
            dashboard_tools["update_chart"],
            dashboard_tools["remove_chart"],
            dashboard_tools["update_layout"],
            dashboard_tools["tidy_layout"],
            dashboard_tools["add_kpi"],
            dashboard_tools["update_kpi"],
        ]
    )
    chart = LlmAgent(
        name=CHART_NAME,
        model=models["chart"],
        instruction=make_instruction("chart", services),
        tools=[data_tools["get_query_schema"], *chart_writers],
        # Query butuh SQL → tool ``Query_Agent`` (sub-agent single_turn di atas).
        sub_agents=[query_for_chart],
        # Satu-satunya target transfer: Root_Agent. Tanpa ini ``Query_Agent``
        # (versi chat, peer) juga muncul sebagai target transfer sehingga LLM
        # bisa "minta query" lewat transfer alih-alih tool — persis ambiguitas
        # yang membuat pengembalian ke pemanggil tak deterministik.
        disallow_transfer_to_peers=True,
        # BUG B: NO_QUERY dari get_query_schema/tool mutasi tidak boleh mengakhiri
        # giliran buntu; hasilnya diganti versi yang menyebut tool ``Query_Agent``.
        after_tool_callback=make_no_query_recovery(retry_tool=QUERY_NAME),
    )
    insight_writers = (
        [dashboard_tools["get_dashboard_state"], dashboard_tools["apply_canvas_patch:insight"]]
        if openui
        else [dashboard_tools["add_insight"], dashboard_tools["update_insight"]]
    )
    # Instance Query terpisah untuk Insight: sama seperti ``query_for_chart``, kendali
    # harus kembali ke Insight TANPA ``transfer_to_agent`` (trace nyata:
    # Insight→Query→run_sql lalu giliran berakhir tanpa ``add_insight``).
    query_for_insight = LlmAgent(
        name=QUERY_NAME,
        model=models["query"],
        description=QUERY_TOOL_DESCRIPTION,
        instruction=make_instruction("query", services),
        tools=list(query_tools),
        mode="single_turn",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        after_tool_callback=after_tool_callback,
    )
    insight = LlmAgent(
        name=INSIGHT_NAME,
        model=models["insight"],
        instruction=make_instruction("insight", services),
        tools=[
            data_tools["get_query_result"],
            data_tools["run_sql"],
            *insight_writers,
        ],
        sub_agents=[query_for_insight],
        disallow_transfer_to_peers=True,
        after_tool_callback=make_no_query_recovery(retry_tool=QUERY_NAME),
    )
    slot_builder = LlmAgent(
        name=SLOT_BUILDER_NAME,
        # ponytail: pakai model chart (tanpa env baru). Tambah key "slot_builder" di
        # AGENT_ENV bila SQL slot butuh model lebih kuat.
        model=models["chart"],
        instruction=make_instruction("slot_builder", services),
        include_contents="none",
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        tools=[
            *_pick(data_tools, ("list_tables", "run_sql", "get_query_result")),
            semantic_tools["search_semantic"],
            *(
                [dashboard_tools["apply_canvas_patch:slot"]]
                if openui
                else [dashboard_tools["add_chart_from_template"], dashboard_tools["add_chart"], dashboard_tools["add_kpi"], dashboard_tools["add_insight"]]
            ),
            architect_tools["mark_slot_done"],
        ],
        # BUG B: Slot_Builder tidak punya tool ``Query_Agent`` (dan dilarang transfer),
        # jadi NO_QUERY dipulihkan dengan instruksi menjalankan ``run_sql`` sendiri.
        after_tool_callback=make_no_query_recovery(retry_tool=None),
    )
    builder = BlueprintBuilderAgent(services=services, slot_builder=slot_builder)
    root = LlmAgent(
        name=ROOT_NAME,
        model=models["root"],
        instruction=make_instruction("root", services),
        tools=[
            turn_tools["classify_turn"],
            dashboard_tools["get_dashboard_state"],
            turn_tools["propose_changes"],
            turn_tools["present_profile_summary"],
            turn_tools["present_relation_candidates"],
            semantic_tools["present_semantic_draft"],
            dashboard_tools["undo_last"],
            dashboard_tools["list_pages"],
            dashboard_tools["create_page"],
            dashboard_tools["switch_page"],
        ],
        sub_agents=[profiler, architect, query, chart, insight, builder],
        # BUG A: giliran saran/opini tidak boleh diserahkan ke agent pelaksana mutasi
        # Dashboard. Guard deterministik; prompt ``root.md`` saja terbukti tidak dipatuhi.
        before_tool_callback=(
            make_root_transfer_guard((CHART_NAME, INSIGHT_NAME, BUILDER_NAME)),
            make_root_route_guard(
                architect_agent=ARCHITECT_NAME, chart_agent=CHART_NAME, insight_agent=INSIGHT_NAME
            ),
        ),
        after_tool_callback=after_tool_callback,
    )
    return root


__all__ = [
    "ARCHITECT_NAME",
    "BUILDER_NAME",
    "CHART_NAME",
    "INSIGHT_NAME",
    "PROFILER_NAME",
    "QUERY_NAME",
    "ROOT_NAME",
    "SLOT_BUILDER_NAME",
    "build_agents",
]
