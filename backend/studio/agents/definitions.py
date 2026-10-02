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
    query = LlmAgent(
        name=QUERY_NAME,
        model=models["query"],
        instruction=make_instruction("query", services),
        tools=[
            *_pick(data_tools, ("list_tables", "run_sql")),
            semantic_tools["search_semantic"],
            semantic_tools["find_verified_queries"],
        ],
        after_tool_callback=after_tool_callback,
    )
    chart = LlmAgent(
        name=CHART_NAME,
        model=models["chart"],
        instruction=make_instruction("chart", services),
        tools=[
            data_tools["get_query_schema"],
            dashboard_tools["add_chart"],
            dashboard_tools["update_chart"],
            dashboard_tools["remove_chart"],
            dashboard_tools["update_layout"],
            dashboard_tools["add_kpi"],
            dashboard_tools["update_kpi"],
        ],
        after_tool_callback=after_tool_callback,
    )
    insight = LlmAgent(
        name=INSIGHT_NAME,
        model=models["insight"],
        instruction=make_instruction("insight", services),
        tools=[
            data_tools["get_query_result"],
            data_tools["run_sql"],
            dashboard_tools["add_insight"],
            dashboard_tools["update_insight"],
        ],
        after_tool_callback=after_tool_callback,
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
            dashboard_tools["add_chart"],
            dashboard_tools["add_kpi"],
            dashboard_tools["add_insight"],
            architect_tools["mark_slot_done"],
        ],
        after_tool_callback=after_tool_callback,
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
