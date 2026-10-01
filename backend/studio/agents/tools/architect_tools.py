"""Tool Dashboard_Architect_Agent dan pembangunan Blueprint (Req 37, 39).

==========================  =====================================================
Tool                        Perilaku
==========================  =====================================================
``propose_dashboard_plan``  validasi + Layout_Template → proposal ``blueprint`` →
                            ``approval.request {kind: "blueprint", blueprint}``;
                            Blueprint tidak valid → ``BLUEPRINT_INVALID`` tanpa
                            kartu (Req 37.1–37.5)
``review_dashboard``        temuan Design_Rules → ``review.findings`` (Req 39.1)
``next_blueprint_slot``     slot ``pending`` berikutnya → ``building``; retry
                            direset (Req 37.7–37.9); semua selesai → ``done``
``mark_slot_done``          tandai slot ``done``/``failed`` (Req 37.10)
==========================  =====================================================

Persetujuan Blueprint diproses runner (``activate_blueprint``): proposal yang
disetujui disimpan sebagai Blueprint aktif berisi slot terpilih saja.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from google.adk.tools import ToolContext
from pydantic import ValidationError

from studio.agents.tools.context import (
    CHAT_EVENTS_KEY,
    STATE_DASHBOARD_ID,
    STATE_LAST_QUERY_ID,
    ToolServices,
    chat_event,
    session_id_of,
    workspace_id_of,
)
from studio.agents.tools.dashboard_tools import blueprint_progress
from studio.agents.tools.guard import llm_output, ok_result
from studio.agents.turn_policy import (
    BLUEPRINT_ACTIVE_KEY,
    BLUEPRINT_CURRENT_SLOT_KEY,
    bind_tool_context,
    is_mutation_allowed,
    reset_retries,
)
from studio.api.errors import StudioError
from studio.core.blueprint import BlueprintInvalid, finalize_blueprint, select_slots
from studio.core.design_rules import QueryInfo, review
from studio.core.models import DashboardBlueprint, SetGlobalFiltersCommand

__all__ = [
    "ARCHITECT_TOOL_NAMES",
    "BLUEPRINT_TOOL_NAMES",
    "activate_blueprint",
    "advance_blueprint",
    "make_architect_tools",
    "review_findings",
    "skip_remaining_slots",
]

ARCHITECT_TOOL_NAMES: tuple[str, ...] = ("propose_dashboard_plan", "review_dashboard")
BLUEPRINT_TOOL_NAMES: tuple[str, ...] = ("next_blueprint_slot", "mark_slot_done")


async def _dashboard_id(services: ToolServices, ws_id: str, state: Any, *, create: bool) -> str | None:
    repos = services.repos
    bound = state.get(STATE_DASHBOARD_ID)
    if isinstance(bound, str) and bound:
        record = await repos.dashboards.get_or_none(bound)
        if record is not None and record.workspace_id == ws_id:
            return bound
    dashboards = await repos.dashboards.list_by_workspace(ws_id)
    if dashboards:
        chosen = max(dashboards, key=lambda d: (d.updated_at, d.created_at, d.id)).id
    elif create and services.dashboard_store is not None:
        chosen = (await services.dashboard_store.create_dashboard(ws_id, "Dashboard")).id
    else:
        return None
    state[STATE_DASHBOARD_ID] = chosen
    return chosen


async def _vocabulary(services: ToolServices, ws_id: str) -> tuple[set[str], set[str]]:
    """``(nama metrik non-rejected, nama kolom & tabel.kolom)`` untuk validasi Blueprint."""
    repos = services.repos
    metrics = {
        str(e.body.get("name"))
        for e in await repos.semantic.list(ws_id, kind="metric", status=["candidate", "confirmed"])
    }
    columns: set[str] = set()
    for d in await repos.datasets.list_by_workspace(ws_id):
        for c in d.schema:
            columns.add(c.name)
            columns.add(f"{d.table_name}.{c.name}")
    return metrics, columns


async def activate_blueprint(
    services: ToolServices,
    *,
    workspace_id: str,
    proposal: Any,
    selected_slot_ids: Iterable[str] | None,
    state: Any,
) -> str | None:
    """Simpan Blueprint aktif (slot terpilih) dari proposal ``blueprint`` yang disetujui (Req 37.6).

    Mengembalikan id Blueprint atau ``None`` bila proposal bukan Blueprint/payload invalid.
    """
    if getattr(proposal, "kind", None) != "blueprint" or not proposal.payload:
        return None
    payload = dict(proposal.payload)
    try:
        bp = DashboardBlueprint.model_validate(payload["blueprint"])
        selected = list(selected_slot_ids) if selected_slot_ids else None
        bp = select_slots(bp, selected)
    except (ValidationError, KeyError, BlueprintInvalid):
        return None
    dashboard_id = payload.get("dashboard_id") or await _dashboard_id(
        services, workspace_id, state, create=True
    )
    record = await services.repos.blueprints.create(
        dashboard_id, bp.model_dump(mode="json"), proposal_id=proposal.id
    )
    state[STATE_DASHBOARD_ID] = dashboard_id
    state[BLUEPRINT_ACTIVE_KEY] = record.id
    state[BLUEPRINT_CURRENT_SLOT_KEY] = None
    return record.id


async def skip_remaining_slots(services: ToolServices, blueprint_id: str) -> list[str]:
    """Run dihentikan: slot ``pending``/``building`` → ``skipped`` (Req 37.11)."""
    repos = services.repos
    record = await repos.blueprints.get_or_none(blueprint_id)
    if record is None or record.status != "active":
        return []
    skipped = [sid for sid, s in record.slot_status.items() if s.get("status") in ("pending", "building")]
    for sid in skipped:
        await repos.blueprints.update_slot(blueprint_id, sid, "skipped")
    await repos.blueprints.finish(blueprint_id, "stopped")
    return skipped


async def review_findings(services: ToolServices, ws_id: str, state: Any) -> dict[str, Any]:
    """Temuan Design_Rules Dashboard aktif + event ``review.findings`` (Req 39.1)."""
    repos = services.repos
    dashboard_id = await _dashboard_id(services, ws_id, state, create=False)
    if dashboard_id is None:
        return ok_result(findings=[], message="Workspace belum memiliki Dashboard.")
    record = await repos.dashboards.get(dashboard_id)
    content = record.content
    query_ids = {
        item.query_id if item.kind == "insight" else item.spec.query_id  # type: ignore[union-attr]
        for item in content.items.values()
    }
    infos: dict[str, QueryInfo] = {}
    for qid, q in (await repos.queries.get_many(query_ids)).items():
        names = [c.name for c in q.output_schema]
        distinct = {n: len({row[i] for row in q.rows if i < len(row)}) for i, n in enumerate(names)}
        lineage = {
            k: (v["table"], v["column"]) if isinstance(v, Mapping) else None for k, v in q.lineage.items()
        }
        infos[qid] = QueryInfo(tuple(q.tables_used), lineage, distinct)
    datasets = {d.id: d.table_name for d in await repos.datasets.list_by_workspace(ws_id)}
    times = {
        (datasets[ds_id], c.name)
        for ds_id, prof in (await repos.profiles.list_by_workspace(ws_id)).items()
        if ds_id in datasets
        for c in prof.columns
        if c.role == "time"
    }
    findings = [f.to_json() for f in review(content, infos, times)]
    return ok_result(
        findings=findings,
        brief=content.brief.model_dump(mode="json") if content.brief else None,
        **{CHAT_EVENTS_KEY: [chat_event("review.findings", {"findings": findings})]},
    )


async def advance_blueprint(services: ToolServices, state: Any, invocation_id: str | None) -> dict[str, Any]:
    """Slot ``pending`` berikutnya → ``building`` (Req 37.7–37.9); habis → ``done`` + filter bawaan.

    Slot sebelumnya yang masih ``building`` ditandai ``failed`` agar loop tetap maju.
    """
    repos = services.repos
    bp_id = state.get(BLUEPRINT_ACTIVE_KEY)
    record = await repos.blueprints.get_or_none(bp_id) if isinstance(bp_id, str) else None
    if record is None or record.status != "active":
        raise StudioError(
            "NO_ACTIVE_BLUEPRINT",
            "Tidak ada Blueprint aktif; usulkan rancangan dengan propose_dashboard_plan dan "
            "tunggu persetujuan pengguna.",
            http_status=422,
        )
    current = state.get(BLUEPRINT_CURRENT_SLOT_KEY)
    if isinstance(current, Mapping):
        sid = str(current.get("slot_id"))
        if record.slot_status.get(sid, {}).get("status") == "building":
            await repos.blueprints.update_slot(
                record.id, sid, "failed", error={"code": "NOT_BUILT", "message": "slot tidak selesai"}
            )
            record = await repos.blueprints.get(record.id)
    slots = record.blueprint.get("slots", [])
    pending = [s for s in slots if record.slot_status.get(s["slot_id"], {}).get("status") == "pending"]
    if not pending:
        await repos.blueprints.finish(record.id, "completed")
        state[BLUEPRINT_CURRENT_SLOT_KEY] = None
        state[BLUEPRINT_ACTIVE_KEY] = None
        failed = [{"slot_id": sid, **s} for sid, s in record.slot_status.items() if s.get("status") == "failed"]
        events: list[dict[str, Any]] = []
        filters = record.blueprint.get("default_filters", [])
        applied = False
        filter_error = None
        store = services.dashboard_store
        if filters and store is not None and is_mutation_allowed(state):
            # Filter bawaan yang disetujui bersama Blueprint (Req 37.12).
            try:
                dash = await repos.dashboards.get(record.dashboard_id)
                event = await store.apply(
                    record.dashboard_id,
                    SetGlobalFiltersCommand.model_validate({"filters": filters}),
                    dash.version,
                    "agent",
                    actor_run_id=invocation_id,
                )
                applied = True
                events.append(chat_event("patch.applied", event.model_dump(mode="json")))
            except StudioError as exc:
                filter_error = {"code": exc.code, "message": exc.message}
        return ok_result(
            done=True,
            default_filters=filters,
            default_filters_applied=applied,
            default_filters_error=filter_error,
            failed_slots=failed,
            message="Semua slot diproses. Jalankan review_dashboard, lalu sampaikan ringkasan "
            "termasuk slot yang gagal.",
            **({CHAT_EVENTS_KEY: events} if events else {}),
        )
    slot = pending[0]
    await repos.blueprints.update_slot(record.id, slot["slot_id"], "building")
    state[BLUEPRINT_CURRENT_SLOT_KEY] = dict(slot)
    # Query slot sebelumnya tidak boleh jadi fallback add_* tanpa query_id.
    state[STATE_LAST_QUERY_ID] = None
    reset_retries(state)
    return ok_result(
        done=False,
        slot=slot,
        remaining=len(pending) - 1,
        brief=record.blueprint.get("brief"),
        **{
            CHAT_EVENTS_KEY: [
                chat_event(
                    "blueprint.progress",
                    {"blueprint_id": record.id, "slot_id": slot["slot_id"], "status": "building"},
                )
            ]
        },
    )


def make_architect_tools(services: ToolServices) -> dict[str, Callable[..., Any]]:
    repos = services.repos
    guard = llm_output(max_sample_rows=services.sample_rows)

    @guard
    async def propose_dashboard_plan(
        summary: str, blueprint: dict[str, Any], tool_context: ToolContext
    ) -> dict[str, Any]:
        """Usulkan Dashboard_Blueprint utuh untuk disetujui pengguna (per slot).

        Args:
            summary: ringkasan rancangan + asumsi, dalam bahasa pengguna.
            blueprint: {"brief": {purpose, audience, key_questions[], kpis[{metric,
                compare}], sections[], time_grain, assumptions[]}, "slots": [{slot_id
                (a-z0-9_), section (kpi_row|trend|breakdown|composition|distribution|
                detail|other), purpose, visual (kpi|line|bar|pie|scatter|heatmap|
                waterfall|insight), metrics[] (nama Business_Metric atau kolom),
                dimension?, layout? {x,y,w,h}, cross_filter_column?}], "default_filters": []}.
                Biarkan layout kosong agar Layout_Template menempatkan slot otomatis.

        Blueprint tidak valid → error berisi slot dan aturan yang dilanggar; perbaiki
        lalu panggil lagi. JANGAN membangun slot sebelum pengguna menyetujui.
        """
        ws_id = workspace_id_of(tool_context)
        text = str(summary or "").strip()
        if not text:
            raise StudioError("INVALID_ARGUMENT", "summary tidak boleh kosong.", http_status=422)
        try:
            bp = DashboardBlueprint.model_validate(blueprint)
        except ValidationError as exc:
            raise StudioError(
                "BLUEPRINT_INVALID",
                "Struktur Blueprint tidak valid.",
                {"errors": exc.errors(include_url=False, include_context=False, include_input=False)},
                http_status=422,
            ) from exc
        dashboard_id = await _dashboard_id(services, ws_id, tool_context.state, create=False)
        existing = {}
        if dashboard_id is not None:
            existing = dict((await repos.dashboards.get(dashboard_id)).content.layout)
        metrics, columns = await _vocabulary(services, ws_id)
        final = finalize_blueprint(bp, existing, metrics, columns)  # BlueprintInvalid → error

        session_id = session_id_of(tool_context)
        await repos.proposals.expire_pending(session_id)
        bp_json = final.model_dump(mode="json")
        proposal = await repos.proposals.create(
            session_id,
            text,
            kind="blueprint",
            payload={"blueprint": bp_json, "dashboard_id": dashboard_id},
        )
        data = {
            "proposal_id": proposal.id,
            "kind": "blueprint",
            "summary": text,
            "themes": [s.purpose for s in final.slots][:10],
            "blueprint": bp_json,
        }
        return ok_result(
            proposal_id=proposal.id,
            slots=[{"slot_id": s.slot_id, "section": s.section, "visual": s.visual} for s in final.slots],
            message="Rancangan ditampilkan; tunggu pengguna menyetujui (bisa per slot) sebelum membangun.",
            **{CHAT_EVENTS_KEY: [chat_event("approval.request", data)]},
        )

    @guard
    async def review_dashboard(tool_context: ToolContext) -> dict[str, Any]:
        """Review Dashboard aktif terhadap prinsip desain BI (tanpa mengubah apa pun).

        Mengembalikan temuan {code, severity, item_ids, message, suggestion}. Tambahkan
        penilaian Anda apakah pertanyaan bisnis kunci di Design_Brief sudah terjawab.
        """
        return await review_findings(services, workspace_id_of(tool_context), tool_context.state)

    # -- pembangunan Blueprint ----------------------------------------------------

    @guard
    async def next_blueprint_slot(tool_context: ToolContext) -> dict[str, Any]:
        """Ambil Blueprint_Slot berikutnya yang harus dibangun (setelah pengguna menyetujui).

        Bangun slot: minta Query_Agent membuat query, lalu Chart_Designer_Agent memanggil
        add_chart/add_kpi (atau Insight_Agent add_insight) dengan ``slot_id`` ini; layout
        slot dipakai otomatis. Bila gagal setelah retry, panggil mark_slot_done dengan
        error. ``done: true`` = semua slot selesai; terapkan default_filters lalu review.
        """
        state = bind_tool_context(tool_context)
        return await advance_blueprint(services, state, getattr(tool_context, "invocation_id", None))

    @guard
    async def mark_slot_done(
        slot_id: str,
        tool_context: ToolContext,
        item_id: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Tandai Blueprint_Slot selesai (``item_id``) atau gagal (``error``).

        Tidak perlu dipanggil bila add_chart/add_kpi/add_insight sudah dipanggil dengan
        ``slot_id`` dan sukses (slot ditandai otomatis).

        Args:
            slot_id: id slot.
            item_id: id item yang dibuat untuk slot ini.
            error: alasan kegagalan bila slot tidak dapat dibangun.
        """
        state = bind_tool_context(tool_context)
        failed = bool(error) or not item_id
        events = await blueprint_progress(
            repos,
            tool_context,
            str(slot_id),
            "failed" if failed else "done",
            item_id=None if failed else item_id,
            error={"message": error or "item tidak dibuat"} if failed else None,
        )
        if not events:
            raise StudioError("NO_ACTIVE_BLUEPRINT", "Tidak ada Blueprint aktif.", http_status=422)
        if state.get(BLUEPRINT_CURRENT_SLOT_KEY) is None:
            reset_retries(state)
        return ok_result(slot_id=slot_id, status="failed" if failed else "done", **{CHAT_EVENTS_KEY: events})

    tools = {
        "propose_dashboard_plan": propose_dashboard_plan,
        "review_dashboard": review_dashboard,
        "next_blueprint_slot": next_blueprint_slot,
        "mark_slot_done": mark_slot_done,
    }
    assert tuple(tools) == (*ARCHITECT_TOOL_NAMES, *BLUEPRINT_TOOL_NAMES)
    return tools
