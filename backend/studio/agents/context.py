"""InstructionProvider per agent: prompt statis + konteks dinamis (Req 20.2, 33.1).

ADK memanggil ``instruction`` (bila callable) sekali per permintaan LLM dengan
``ReadonlyContext``. Semua agent mendapat model semantik sesuai cakupannya. Root
juga mendapat Dataset (via Privacy_Guard), Confirmed_Relation, state Dashboard +
``dashboard_version``, dan edit manual sejak giliran agent terakhir. Agent yang
memutasi Dashboard mendapat ``dashboard_version`` agar tidak perlu
``get_dashboard_state`` dulu.

Hasil dicache per ``(invocation, dashboard_version, slot)``: satu giliran bisa
memanggil LLM belasan kali, konteks hanya dibangun ulang bila Dashboard/slot berubah.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.agents.tools.context import (
    STATE_DASHBOARD_ID,
    STATE_WORKSPACE_ID,
    ToolServices,
)
from studio.agents.tools.dashboard_tools import dashboard_state_for_llm
from studio.events.sse import to_json

if TYPE_CHECKING:
    from studio.store.repos import DashboardRecord

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Baca prompt statis ``prompts/<name>.md``."""
    return (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def _relations_block(relations: list[Any]) -> list[dict[str, str]]:
    return [
        {
            "from": f"{r.from_table}.{r.from_column}",
            "to": f"{r.to_table}.{r.to_column}",
            "cardinality": r.cardinality,
        }
        for r in relations
    ]


def _semantic_terms(entries: list[Any]) -> dict[str, set[str]]:
    """``tabel → token`` dari label/sinonim kolom dan metrik ber-``base_table`` (pemilih tabel)."""
    from studio.core.verified_queries import tokenize

    out: dict[str, set[str]] = {}
    for e in entries:
        if e.status == "rejected" or e.kind not in ("column", "metric"):
            continue
        body = e.body
        table = body.get("table") if e.kind == "column" else body.get("base_table")
        if not table:
            continue
        words = [body.get("label"), body.get("name"), body.get("description"), *(body.get("synonyms") or [])]
        out.setdefault(str(table), set()).update(*(tokenize(str(w)) for w in words if w))
    return out


async def _table_docs(services: ToolServices, workspace_id: str, entries: list[Any] | None = None) -> list[Any]:
    """``TableDoc`` per Dataset dari profil tersimpan (tanpa membaca data)."""
    from studio.core.data_card import TableDoc

    repos = services.repos
    datasets = await repos.datasets.list_by_workspace(workspace_id)
    profiles = await repos.profiles.list_by_workspace(workspace_id)
    terms = _semantic_terms(entries if entries is not None else await repos.semantic.list(workspace_id))
    docs = []
    for ds in datasets:
        profile = profiles.get(ds.id)
        docs.append(
            TableDoc(
                table=ds.table_name,
                row_count=ds.row_count,
                columns=tuple(profile.columns) if profile is not None else (),
                no_samples=services.privacy_for(ds).no_samples,
                terms=frozenset(terms.get(ds.table_name, ())),
            )
        )
    return docs


async def build_data_block(
    services: ToolServices, workspace_id: str, question: str, entries: list[Any] | None = None
) -> str:
    """Data Card tabel relevan untuk ``question`` + katalog sisanya + nilai yang cocok."""
    from studio.core.data_card import catalog_line, select_tables, table_card, value_matches

    docs = await _table_docs(services, workspace_id, entries)
    if not docs:
        return ""
    relations = await services.repos.relations.list_confirmed(workspace_id)
    pairs = [(r.from_table, r.to_table) for r in relations]
    picked = set(select_tables(question, docs, pairs))
    lines = ["Data tabel (statistik nyata Data_Engine; pakai list_tables hanya untuk tabel tanpa Data Card):"]
    lines += [table_card(d) for d in docs if d.table in picked]
    others = [catalog_line(d) for d in docs if d.table not in picked]
    if others:
        lines.append("Tabel lain:")
        lines += others
    if pairs:
        lines.append("Confirmed_Relation (satu-satunya JOIN sah): " + "; ".join(
            f"{r.from_table}.{r.from_column} = {r.to_table}.{r.to_column} ({r.cardinality})" for r in relations
        ))
    matches = value_matches(question, docs)
    if matches:
        lines.append("Nilai dari pertanyaan yang cocok dengan data: " + "; ".join(matches))
    return "\n".join(lines)


async def build_turn_context(
    services: ToolServices,
    *,
    workspace_id: str,
    dashboard_id: str | None,
    session_id: str | None,
) -> str:
    """Blok konteks dinamis (JSON) untuk giliran Root_Agent.

    Root tidak menulis SQL: cukup katalog satu baris per tabel (Data Card lengkap
    ada di konteks Query/Slot_Builder/Insight). Dipakai juga langsung pada test.
    """
    from studio.core.data_card import catalog_line

    repos = services.repos
    tables = [catalog_line(d) for d in await _table_docs(services, workspace_id)]
    relations = await repos.relations.list_confirmed(workspace_id)

    dashboard: dict[str, Any] | None = None
    manual_edits: list[str] = []
    if dashboard_id is not None and services.dashboard_store is not None:
        record = await repos.dashboards.get_or_none(dashboard_id)
        if record is not None and record.workspace_id == workspace_id:
            snapshot = await services.dashboard_store.get(dashboard_id)
            dashboard = dashboard_state_for_llm(snapshot)
            manual_edits = await _manual_edits_since(
                services, record, session_id
            )

    context = {
        "datasets": tables,
        "confirmed_relations": _relations_block(relations),
        "dashboard": dashboard,
        "dashboard_version": dashboard["version"] if dashboard else None,
        "manual_edits_since_last_turn": manual_edits,
    }
    return "Konteks Workspace saat ini (jangan diulang ke pengguna):\n" + to_json(context)


async def _manual_edits_since(
    services: ToolServices, record: DashboardRecord, session_id: str | None
) -> list[str]:
    """Ringkasan edit manual (source=user) sejak ``last_agent_version`` sesi."""
    if session_id is None:
        return []
    session = await services.repos.chat_sessions.get_or_none(session_id)
    if session is None:
        return []
    patches = await services.repos.patches.list_by_source_since(
        record.id, session.last_agent_version, "user"
    )
    return [_patch_summary(p) for p in patches]


def _patch_summary(patch: Any) -> str:
    """Ringkasan singkat satu patch untuk konteks LLM (tanpa konten Dashboard)."""
    command = patch.command if isinstance(patch.command, dict) else None
    kind = command.get("type") if command else patch.event.kind
    details = ""
    if command:
        if isinstance(command.get("title"), str):
            details = f" judul '{command['title']}'"
        elif isinstance(command.get("id"), str):
            details = f" item '{command['id']}'"
    return f"{kind}{details} → versi {patch.version}"


def make_root_instruction(
    services: ToolServices,
) -> Callable[[Any], Awaitable[str]]:
    """InstructionProvider Root: prompt statis + konteks giliran dinamis."""
    return make_instruction("root", services)


# ---------------------------------------------------------------------------
# Konteks semantik per agent (Req 33.1, 33.2, 33.5, 36.5)
# ---------------------------------------------------------------------------

#: ``agent_key → (prompt statis digabung berurutan, cakupan semantik)``.
AGENT_PROMPTS: dict[str, tuple[tuple[str, ...], str]] = {
    "root": (("root",), "root"),
    "query": (("query",), "query"),
    "architect": (("architect",), "architect"),
    "chart": (("chart", "chart_rules"), "chart"),
    "insight": (("insight",), "insight"),
    "profiler": (("profiler",), "root"),
    # Slot_Builder menulis SQL + item: cakupan query (superset kind chart).
    "slot_builder": (("slot_builder", "chart_rules"), "query"),
}

#: Agent yang memutasi Dashboard: butuh ``dashboard_version`` sebagai ``base_version``.
_MUTATING_AGENTS = frozenset({"chart", "insight", "slot_builder"})

#: Agent yang menulis SQL: mendapat Data Card tabel relevan per pertanyaan.
_SQL_AGENTS = frozenset({"query", "slot_builder", "insight"})


async def _dashboard_version(services: ToolServices, state: Any) -> int | None:
    dashboard_id = state.get(STATE_DASHBOARD_ID)
    if not dashboard_id:
        return None
    record = await services.repos.dashboards.get_or_none(dashboard_id)
    if record is None or record.workspace_id != state.get(STATE_WORKSPACE_ID):
        return None
    return record.version


async def _semantic_block(services: ToolServices, workspace_id: str, scope: str, entries: list[Any]) -> str:
    from studio.agents.tools.semantic_tools import no_sample_tables
    from studio.core.semantic_context import DEFAULT_SEMANTIC_BUDGET, build_semantic_block

    repos = services.repos
    budget = int(getattr(services, "semantic_budget", DEFAULT_SEMANTIC_BUDGET) or DEFAULT_SEMANTIC_BUDGET)
    block = build_semantic_block(entries, scope, budget, await no_sample_tables(repos, workspace_id))
    return block.text


async def _user_item_ids(services: ToolServices, dashboard_id: str, current: set[str]) -> list[str]:
    """Item yang ditambahkan pengguna (bukan agent) dan masih ada (Req 35.9)."""
    from studio.core.models import AddItemOp

    patches = await services.repos.patches.list_by_source_since(dashboard_id, 0, "user")
    out: list[str] = []
    for p in patches:
        for op in p.event.ops:
            if isinstance(op, AddItemOp) and op.item.id in current and op.item.id not in out:
                out.append(op.item.id)
    return out


async def _brief_drift(services: ToolServices, dashboard_id: str, content: Any, user_items: list[str]) -> dict[str, Any]:
    """Bagian Brief tanpa item & item pengguna di luar Blueprint (Req 36.5)."""
    brief = content.brief
    if brief is None:
        return {}
    latest = await services.repos.blueprints.latest(dashboard_id)
    item_section: dict[str, str] = {}
    if latest is not None:
        sections = {s["slot_id"]: s.get("section") for s in latest.blueprint.get("slots", [])}
        for sid, status in latest.slot_status.items():
            if status.get("item_id") in content.items:
                item_section[status["item_id"]] = str(sections.get(sid))
    covered = set(item_section.values())
    missing = [s for s in brief.sections if s not in covered] if latest is not None else []
    outside = [i for i in user_items if i not in item_section]
    drift: dict[str, Any] = {}
    if missing:
        drift["sections_without_items"] = missing
    if outside:
        drift["user_items_outside_brief"] = outside
    return drift


async def build_agent_context(
    services: ToolServices,
    agent_key: str,
    state: Any,
    session_id: str | None,
) -> str:
    """Blok konteks dinamis per agent (di luar konteks Workspace Root)."""
    from studio.agents.turn_policy import BLUEPRINT_ACTIVE_KEY, BLUEPRINT_CURRENT_SLOT_KEY, USER_MESSAGE_KEY

    workspace_id = state.get(STATE_WORKSPACE_ID)
    if not workspace_id:
        return ""
    _, scope = AGENT_PROMPTS.get(agent_key, ((agent_key,), "root"))
    repos = services.repos
    parts: list[str] = []

    if agent_key == "root":
        parts.append(
            await build_turn_context(
                services,
                workspace_id=workspace_id,
                dashboard_id=state.get(STATE_DASHBOARD_ID),
                session_id=session_id,
            )
        )

    meta = await repos.semantic_meta.get(workspace_id)
    entries = await repos.semantic.list(workspace_id)
    semantic = await _semantic_block(services, workspace_id, scope, entries)
    extra: dict[str, Any] = {}
    if meta.domain:
        extra["domain"] = meta.domain
    if meta.assumptions:
        extra["semantic_assumptions"] = meta.assumptions

    slot = state.get(BLUEPRINT_CURRENT_SLOT_KEY)
    if slot:
        extra["blueprint_slot_aktif"] = slot
    if agent_key == "root" and state.get(BLUEPRINT_ACTIVE_KEY):
        record = await repos.blueprints.get_or_none(str(state.get(BLUEPRINT_ACTIVE_KEY)))
        if record is not None:
            extra["blueprint_aktif"] = {"id": record.id, "status": record.status, "slot_status": record.slot_status}

    if agent_key in _MUTATING_AGENTS:
        extra["dashboard_version"] = await _dashboard_version(services, state)

    question = (slot or {}).get("purpose") if isinstance(slot, dict) else None
    question = str(question or state.get(USER_MESSAGE_KEY) or "")
    data_block = ""
    if agent_key in _SQL_AGENTS:
        data_block = await build_data_block(services, workspace_id, question, entries)

    if agent_key in ("query", "slot_builder"):
        from studio.agents.tools.semantic_tools import verified_candidates
        from studio.core.verified_queries import find_verified

        if question:
            try:
                found = find_verified(await verified_candidates(services, workspace_id), str(question), 3)
            except Exception:  # noqa: BLE001 — contoh opsional
                found = []
            if found:
                extra["verified_queries"] = [{"question": e.question, "sql": e.sql} for e, _ in found]

    if agent_key == "architect":
        dashboard_id = state.get(STATE_DASHBOARD_ID)
        if dashboard_id and services.dashboard_store is not None:
            record = await repos.dashboards.get_or_none(dashboard_id)
            if record is not None and record.workspace_id == workspace_id:
                snapshot = await services.dashboard_store.get(dashboard_id)
                extra["dashboard"] = dashboard_state_for_llm(snapshot)
                user_items = await _user_item_ids(services, dashboard_id, set(record.content.items))
                extra["user_item_ids"] = user_items
                drift = await _brief_drift(services, dashboard_id, record.content, user_items)
                if drift:
                    extra["brief_drift"] = drift
        from studio.core.data_card import catalog_line

        extra["datasets"] = [catalog_line(d) for d in await _table_docs(services, workspace_id, entries)]

    if extra:
        parts.append("Konteks agent (jangan diulang ke pengguna):\n" + to_json(extra))
    if data_block:
        parts.append(data_block)
    if semantic:
        parts.append(semantic)
    return "\n\n".join(p for p in parts if p)


def make_instruction(
    agent_key: str, services: ToolServices
) -> Callable[[Any], Awaitable[str]]:
    """InstructionProvider generik: prompt statis + konteks data/semantik per agent."""
    from studio.agents.turn_policy import BLUEPRINT_CURRENT_SLOT_KEY

    prompt_names, _ = AGENT_PROMPTS.get(agent_key, ((agent_key,), "root"))
    static = "\n\n".join(load_prompt(n) for n in prompt_names)
    # ponytail: cache satu entri (invocation terakhir) per provider. Giliran paralel
    # dalam satu proses saling menimpa (hanya cache miss, tetap benar). Upgrade: dict
    # kecil ber-LRU bila trace menunjukkan miss tinggi.
    cache: dict[str, Any] = {}

    async def provider(ctx: Any) -> str:
        state = getattr(ctx, "state", {}) or {}
        if not state.get(STATE_WORKSPACE_ID):
            return static
        session_id = getattr(getattr(ctx, "session", None), "id", None) or state.get("session_id")
        try:
            slot = state.get(BLUEPRINT_CURRENT_SLOT_KEY)
            key = (
                getattr(ctx, "invocation_id", None),
                session_id,
                await _dashboard_version(services, state),
                slot.get("slot_id") if isinstance(slot, dict) else None,
            )
            if key[0] is not None and cache.get("key") == key:
                return cache["text"]
            dynamic = await build_agent_context(services, agent_key, state, session_id)
        except Exception:  # noqa: BLE001 — konteks gagal tidak boleh mematikan agent
            import logging

            logging.getLogger(__name__).exception("Konteks agent %s gagal dibangun", agent_key)
            return static
        text = f"{static}\n\n{dynamic}" if dynamic else static
        cache.update(key=key, text=text)
        return text

    return provider


__all__ = [
    "AGENT_PROMPTS",
    "build_agent_context",
    "build_data_block",
    "build_turn_context",
    "load_prompt",
    "make_instruction",
    "make_root_instruction",
]
