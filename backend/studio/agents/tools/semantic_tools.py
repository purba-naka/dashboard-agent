"""Tool Semantic_Model untuk agent (Req 32.9, 33.4, 34.3).

* ``get_semantic_model`` — blok semantik (anggaran karakter, prioritas tetap).
* ``search_semantic(term)`` — cari entri berdasarkan nama/label/sinonim/deskripsi.
* ``find_verified_queries(question, k)`` — Verified_Query confirmed & valid teratas.
* ``present_semantic_draft`` — kartu "Pemahaman data" di Chat_Panel (``semantic.draft``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from google.adk.tools import ToolContext

from studio.agents.tools.context import CHAT_EVENTS_KEY, ToolServices, chat_event, workspace_id_of
from studio.agents.tools.guard import llm_output, ok_result
from studio.core.semantic_context import DEFAULT_SEMANTIC_BUDGET, build_semantic_block, project_entry
from studio.core.verified_queries import VerifiedCandidate, find_verified, is_valid_sql, tokenize

__all__ = ["SEMANTIC_TOOL_NAMES", "make_semantic_tools", "no_sample_tables", "verified_candidates"]

SEMANTIC_TOOL_NAMES: tuple[str, ...] = (
    "get_semantic_model",
    "search_semantic",
    "find_verified_queries",
    "present_semantic_draft",
)
_SEARCH_LIMIT = 10


async def no_sample_tables(repos: Any, ws_id: str) -> set[str]:
    return {d.table_name for d in await repos.datasets.list_by_workspace(ws_id) if d.privacy_no_samples}


async def verified_candidates(services: ToolServices, ws_id: str) -> list[VerifiedCandidate]:
    """Verified_Query Workspace beserta validitas saat ini (Req 34.4)."""
    repos = services.repos
    records = await repos.semantic.list(ws_id, kind="verified_query")
    if not records:
        return []
    tables = await services.engine.workspace_tables(ws_id)
    out = []
    for r in records:
        sql = str(r.body.get("sql", ""))
        out.append(
            VerifiedCandidate(
                id=r.id,
                question=str(r.body.get("question", "")),
                sql=sql,
                status=r.status,
                valid=is_valid_sql(sql, tables.schemas, tables.relations),
                confirmed_at=r.decided_at,
                query_id=r.body.get("query_id"),
                item_id=r.body.get("item_id"),
            )
        )
    return out


def _search_text(body: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("name", "label", "term", "table", "column", "description", "text", "question"):
        value = body.get(key)
        if isinstance(value, str):
            parts.append(value)
    parts.extend(str(s) for s in body.get("synonyms") or [])
    return " ".join(parts)


def make_semantic_tools(services: ToolServices) -> dict[str, Callable[..., Any]]:
    repos = services.repos
    guard = llm_output(max_sample_rows=services.sample_rows)

    @guard
    async def get_semantic_model(tool_context: ToolContext, scope: str = "architect") -> dict[str, Any]:
        """Model semantik Workspace (metrik, kolom, istilah, instruksi) untuk cakupan agent.

        Args:
            scope: root, query, architect, chart, atau insight.
        """
        ws_id = workspace_id_of(tool_context)
        meta = await repos.semantic_meta.get(ws_id)
        entries = await repos.semantic.list(ws_id)
        block = build_semantic_block(
            entries, scope, DEFAULT_SEMANTIC_BUDGET, await no_sample_tables(repos, ws_id)
        )
        return ok_result(
            domain=meta.domain,
            assumptions=meta.assumptions,
            semantic=block.text,
            truncated=block.truncated,
        )

    @guard
    async def search_semantic(term: str, tool_context: ToolContext) -> dict[str, Any]:
        """Cari entri model semantik (metrik, kolom, istilah, instruksi) berdasarkan kata kunci.

        Args:
            term: kata kunci, mis. "omzet", "margin", "cabang".
        """
        ws_id = workspace_id_of(tool_context)
        wanted = tokenize(term)
        private = await no_sample_tables(repos, ws_id)
        scored = []
        for entry in await repos.semantic.list(ws_id, status=["candidate", "confirmed"]):
            if entry.kind == "verified_query":
                continue
            tokens = tokenize(_search_text(entry.body))
            hits = len(wanted & tokens)
            if hits:
                scored.append((-hits, entry.entry_key, entry))
        scored.sort(key=lambda t: (t[0], t[1]))
        matches = [project_entry(e, "query", private) for _, _, e in scored[:_SEARCH_LIMIT]]
        return ok_result(term=term, count=len(matches), matches=matches)

    @guard
    async def find_verified_queries(
        question: str, tool_context: ToolContext, k: int = 3
    ) -> dict[str, Any]:
        """Contoh SQL terverifikasi untuk pertanyaan serupa (pakai sebagai acuan, bukan disalin buta).

        Args:
            question: pertanyaan pengguna.
            k: jumlah maksimum hasil (1–5).
        """
        ws_id = workspace_id_of(tool_context)
        limit = max(1, min(int(k or 3), 5))
        found = find_verified(await verified_candidates(services, ws_id), question, limit)
        return ok_result(
            count=len(found),
            examples=[{"question": e.question, "sql": e.sql, "score": round(s, 3)} for e, s in found],
        )

    @guard
    async def present_semantic_draft(tool_context: ToolContext, summary: str = "") -> dict[str, Any]:
        """Tampilkan kartu "Pemahaman data" (draft model semantik) untuk dikonfirmasi pengguna.

        Args:
            summary: ringkasan singkat pemahaman Anda atas data (opsional).
        """
        ws_id = workspace_id_of(tool_context)
        meta = await repos.semantic_meta.get(ws_id)
        run = await repos.draft_runs.latest(ws_id)
        entries = await repos.semantic.list(ws_id, status=["candidate", "confirmed"])
        metrics = [
            {
                "id": e.id,
                "name": e.body.get("name"),
                "label": e.body.get("label"),
                "expr": e.body.get("expr"),
                "status": e.status,
            }
            for e in entries
            if e.kind == "metric"
        ]
        columns = [
            {
                "id": e.id,
                "table": e.body.get("table"),
                "column": e.body.get("column"),
                "label": e.body.get("label"),
                "description": e.body.get("description"),
                "status": e.status,
            }
            for e in entries
            if e.kind == "column" and str(e.body.get("description") or "").strip()
        ]
        data = {
            "run_id": run.id if run else None,
            "domain": meta.domain,
            "summary": summary,
            "metrics": metrics,
            "columns_highlight": columns,
            "assumptions": meta.assumptions,
        }
        return ok_result(
            domain=meta.domain,
            metric_count=len(metrics),
            message="Kartu pemahaman data ditampilkan; tunggu pengguna mengonfirmasi atau mengoreksi.",
            **{CHAT_EVENTS_KEY: [chat_event("semantic.draft", data)]},
        )

    tools = {
        "get_semantic_model": get_semantic_model,
        "search_semantic": search_semantic,
        "find_verified_queries": find_verified_queries,
        "present_semantic_draft": present_semantic_draft,
    }
    assert tuple(tools) == SEMANTIC_TOOL_NAMES
    return tools
