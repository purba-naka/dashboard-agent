"""Dashboard_Store: satu-satunya jalur tulis content Dashboard (Req 17.5, 18.1).

Semua perubahan (Agent_Tools maupun Canvas_Editor) melewati
:meth:`DashboardStore.apply` / :meth:`~DashboardStore.undo` /
:meth:`~DashboardStore.redo`, yang masing-masing menghasilkan tepat satu
Patch_Event berversi (``version = base_version + 1``).

Alur ``apply`` (design "Dashboard_Store"), diserialkan per ``dashboard_id``
dengan ``asyncio.Lock``:

1. cek ``base_version == version`` — lebih lama maupun lebih baru ditolak
   dengan :class:`VersionConflict` (``current_version``) (Req 18.5, 20.3);
2. resolve ``Command`` → ops self-contained (``core/patches.py``);
3. validasi: Chart_Spec terhadap ``output_schema`` query tersimpan (query harus
   ada di Workspace yang sama), verifikasi angka insight terhadap bukti +
   ``filters_snapshot`` (``matched_numbers`` diisi ulang), dan
   ``check_insight_type`` dari ``tables_used``/``relations_used`` query;
4. ``apply_ops`` + ``invert_ops``;
5. dalam satu transaksi: insert ``patch_events`` lalu
   ``UPDATE dashboards ... WHERE version = base_version`` beserta stack
   undo/redo (``core/history.py``);
6. setelah commit: publish ``patch.applied`` ke EventBus Workspace (Req 18.3);
7. bila ``source == "user"``: panggil hook ``on_user_edit`` dengan ringkasan
   singkat perubahan (dihubungkan ke sesi ADK, Req 20.1).

Undo/redo sendiri adalah Patch_Event baru (``kind="undo"``/``"redo"``) dan
menaikkan versi (Req 19.1, 19.2); patch ``normal`` mengosongkan redo (Req 19.3).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from studio.api.errors import StudioError
from studio.core.chart_convert import convert_chart_type
from studio.core.chart_spec import validate_chart_spec
from studio.core.history import (
    History,
    plan_redo,
    plan_undo,
    redo_patch_fields,
    undo_patch_fields,
)
from studio.core.models import (
    COMMAND_ADAPTER,
    AddItemOp,
    ChangeChartTypeCommand,
    ChartItem,
    ChartSpec,
    ChartType,
    Command,
    DashboardContent,
    DashboardItem,
    InsightItem,
    KpiItem,
    Op,
    PatchEvent,
    PatchKind,
    PatchSource,
    RemoveItemOp,
    SetBriefOp,
    SetFiltersOp,
    SetItemOp,
    SetLayoutOp,
    SetTitleOp,
    StudioModel,
)
from studio.core.kpi import validate_kpi_spec
from studio.core.numbers import check_insight_type, verify_insight_numbers
from studio.core.patches import apply_ops, invert_ops, resolve_command
from studio.core.status import ItemStatus, dashboard_item_status, item_query_id
from studio.events.bus import EventBus
from studio.store.db import Database
from studio.store.repos import DashboardRecord, QueryRecord, Repositories, new_id

logger = logging.getLogger(__name__)

PATCH_APPLIED_EVENT = "patch.applied"

IdFactory = Callable[[], str]
Clock = Callable[[], datetime]
#: ``(dashboard_id, workspace_id, summary, patch_event)``; boleh sync atau async.
UserEditHook = Callable[[str, str, str, PatchEvent], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# Error & snapshot
# ---------------------------------------------------------------------------


class VersionConflict(StudioError):
    """``base_version`` tidak sama dengan versi Dashboard saat ini (409)."""

    def __init__(self, current_version: int, base_version: int | None = None) -> None:
        details: dict[str, Any] = {"current_version": current_version}
        if base_version is not None:
            details["base_version"] = base_version
        super().__init__(
            "VERSION_CONFLICT",
            "State Dashboard sudah berubah; muat ulang state terbaru lalu coba lagi.",
            details,
            http_status=409,
        )
        self.current_version = current_version


class UnknownQuery(StudioError):
    """Item merujuk query yang tidak ada di Workspace Dashboard (422)."""

    def __init__(self, query_id: str) -> None:
        super().__init__(
            "UNKNOWN_QUERY",
            f"Query '{query_id}' tidak ditemukan di Workspace ini; jalankan query "
            "terlebih dahulu dan gunakan query_id hasilnya.",
            {"query_id": query_id},
            http_status=422,
        )


class DashboardSnapshot(StudioModel):
    """Kontrak ``DashboardSnapshot`` di ``frontend/src/lib/types.ts``."""

    id: str
    title: str
    version: int
    content: DashboardContent
    can_undo: bool
    can_redo: bool
    item_status: dict[str, ItemStatus]


# ---------------------------------------------------------------------------
# Ringkasan perubahan (untuk sesi agent, Req 20.1)
# ---------------------------------------------------------------------------


def _label(item: DashboardItem) -> str:
    kind = {"chart": "chart", "insight": "insight", "kpi": "KPI"}.get(item.kind, item.kind)
    return f"{kind} '{item.title}'"


def _describe_op(op: Op, content: DashboardContent) -> str:
    if isinstance(op, AddItemOp):
        return f"menambahkan {_label(op.item)}"
    if isinstance(op, RemoveItemOp):
        return f"menghapus {_label(op.item)}"
    if isinstance(op, SetItemOp):
        before, after = op.before, op.after
        if (
            isinstance(before, ChartItem)
            and isinstance(after, ChartItem)
            and before.spec.chart_type != after.spec.chart_type
        ):
            return (
                f"mengubah tipe {_label(after)} dari {before.spec.chart_type} "
                f"menjadi {after.spec.chart_type}"
            )
        return f"mengubah {_label(after)}"
    if isinstance(op, SetLayoutOp):
        if len(op.changes) != 1:
            return f"mengatur ulang tata letak {len(op.changes)} item"
        change = op.changes[0]
        item = content.items.get(change.id)
        label = _label(item) if item is not None else f"item '{change.id}'"
        moved = (change.before.x, change.before.y) != (change.after.x, change.after.y)
        resized = (change.before.w, change.before.h) != (change.after.w, change.after.h)
        if moved and resized:
            return f"memindahkan dan mengubah ukuran {label}"
        if resized:
            return f"mengubah ukuran {label}"
        return f"memindahkan {label}"
    if isinstance(op, SetFiltersOp):
        return f"mengubah filter global ({len(op.after)} filter aktif)"
    if isinstance(op, SetTitleOp):
        return f"mengubah judul Dashboard menjadi '{op.after}'"
    if isinstance(op, SetBriefOp):
        return "mengubah Design_Brief" if op.after is not None else "menghapus Design_Brief"
    return f"menerapkan {getattr(op, 'op', 'perubahan')}"


def describe_ops(ops: Sequence[Op], content: DashboardContent) -> str:
    """Deskripsi singkat ops terhadap ``content`` sebelum ops diterapkan."""
    parts = [_describe_op(op, content) for op in ops]
    return "; ".join(parts) if parts else "tidak mengubah apa pun"


def summarize_patch(
    event: PatchEvent,
    content_before: DashboardContent,
    target_ops: Sequence[Op] | None = None,
) -> str:
    """Ringkasan human-readable Patch_Event, mis. ``Pengguna memindahkan chart 'X' (versi 5)``.

    Untuk undo/redo, ``target_ops`` = ops patch target (yang dibatalkan/diulang).
    """
    who = "Pengguna" if event.source == "user" else "Agent"
    if event.kind == "undo":
        # Ops target dijelaskan terhadap content saat ops itu diterapkan; untuk
        # label item cukup content sebelum undo (item target ada di sana).
        what = f"membatalkan (undo) perubahan: {describe_ops(target_ops or [], content_before)}"
    elif event.kind == "redo":
        what = f"mengulang (redo) perubahan: {describe_ops(target_ops or [], content_before)}"
    else:
        what = describe_ops(event.ops, content_before)
    return f"{who} {what} (versi {event.version})"


# ---------------------------------------------------------------------------
# DashboardStore
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DashboardStore:
    """Dashboard berversi dengan Patch_Event, undo/redo, dan siaran SSE."""

    def __init__(
        self,
        db: Database | Repositories,
        bus: EventBus | None,
        *,
        id_factory: IdFactory | None = None,
        clock: Clock | None = None,
        on_user_edit: UserEditHook | None = None,
    ) -> None:
        self.repos = db if isinstance(db, Repositories) else Repositories(db)
        self.db: Database = self.repos.db
        self.bus = bus
        self._new_id: IdFactory = id_factory or new_id
        self._clock: Clock = clock or _utc_now
        #: Hook edit manual; dapat dipasang belakangan (wiring ke sesi ADK).
        self.on_user_edit: UserEditHook | None = on_user_edit
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ util
    def _lock(self, dashboard_id: str) -> asyncio.Lock:
        lock = self._locks.get(dashboard_id)
        if lock is None:
            lock = self._locks[dashboard_id] = asyncio.Lock()
        return lock

    @staticmethod
    def _check_version(record: DashboardRecord, base_version: int | None) -> None:
        if base_version is not None and base_version != record.version:
            raise VersionConflict(record.version, base_version)

    @staticmethod
    def _parse_command(command: Command | Mapping[str, Any]) -> Command:
        if isinstance(command, BaseModel):
            return command  # type: ignore[return-value]
        try:
            return COMMAND_ADAPTER.validate_python(command)
        except ValidationError as exc:
            raise StudioError(
                "VALIDATION_ERROR",
                "Command tidak valid.",
                {"errors": exc.errors(include_url=False, include_context=False)},
                http_status=422,
            ) from exc

    async def _query(
        self, workspace_id: str, query_id: str, cache: dict[str, QueryRecord]
    ) -> QueryRecord:
        record = cache.get(query_id)
        if record is None:
            record = await self.repos.queries.get_or_none(query_id)
            if record is None or record.workspace_id != workspace_id:
                raise UnknownQuery(query_id)
            cache[query_id] = record
        return record

    # ------------------------------------------------------------- validasi
    async def _validate_item(
        self, workspace_id: str, item: DashboardItem, cache: dict[str, QueryRecord]
    ) -> DashboardItem:
        """Validasi item hasil command; insight dikembalikan dengan ``matched_numbers`` baru."""
        query = await self._query(workspace_id, item_query_id(item), cache)
        if isinstance(item, ChartItem):
            validate_chart_spec(item.spec, query.output_schema)
            return item
        if isinstance(item, KpiItem):
            validate_kpi_spec(item.spec, query.output_schema)
            return item
        assert isinstance(item, InsightItem)
        check_insight_type(item.insight_type, query.tables_used, query.relations_used)
        matches = verify_insight_numbers(item.text, item.evidence, item.filters_snapshot)
        return item.model_copy(update={"matched_numbers": matches})

    async def _validate_ops(
        self, workspace_id: str, ops: Sequence[Op], cache: dict[str, QueryRecord]
    ) -> list[Op]:
        out: list[Op] = []
        for op in ops:
            if isinstance(op, AddItemOp):
                item = await self._validate_item(workspace_id, op.item, cache)
                out.append(op if item is op.item else AddItemOp(item=item, layout=op.layout))
            elif isinstance(op, SetItemOp):
                item = await self._validate_item(workspace_id, op.after, cache)
                out.append(
                    op if item is op.after else SetItemOp(id=op.id, before=op.before, after=item)
                )
            else:
                out.append(op)
        return out

    async def _resolve(
        self,
        record: DashboardRecord,
        command: Command,
        cache: dict[str, QueryRecord],
    ) -> list[Op]:
        converter = None
        if isinstance(command, ChangeChartTypeCommand):
            current = record.content.items.get(command.id)
            if isinstance(current, ChartItem):
                query = await self._query(record.workspace_id, current.spec.query_id, cache)
                schema = query.output_schema

                def converter(spec: ChartSpec, new_type: ChartType) -> ChartSpec:
                    return convert_chart_type(spec, new_type, schema)

        return resolve_command(
            record.content, command, new_id=self._new_id, convert_chart_type=converter
        )

    # --------------------------------------------------------------- commit
    async def _commit(
        self,
        record: DashboardRecord,
        *,
        source: PatchSource,
        kind: PatchKind,
        ops: list[Op],
        inverse_ops: list[Op],
        target_patch_id: str | None,
        command: Command | None,
        actor_run_id: str | None,
        target_ops: Sequence[Op] | None = None,
    ) -> PatchEvent:
        """Terapkan ops, simpan atomik, lalu publish + hook (dipanggil di bawah lock)."""
        new_content = apply_ops(record.content, ops)
        event = PatchEvent(
            id=self._new_id(),
            dashboard_id=record.id,
            version=record.version + 1,
            base_version=record.version,
            source=source,
            kind=kind,
            target_patch_id=target_patch_id,
            ops=ops,
            inverse_ops=inverse_ops,
            created_at=self._clock(),
        )
        history = History.from_lists(record.undo_stack, record.redo_stack).record(event)
        undo_stack, redo_stack = history.to_lists()

        async with self.db.transaction():
            stored = await self.repos.patches.insert(
                event, command=command, actor_run_id=actor_run_id
            )
            updated = await self.repos.dashboards.update(
                record.id,
                expected_version=record.version,
                new_version=event.version,
                content=new_content,
                undo_stack=undo_stack,
                redo_stack=redo_stack,
            )
            if updated != 1:
                # Penulis lain (di luar Dashboard_Store ini) mendahului; rollback.
                current = await self.repos.dashboards.get_version(record.id)
                raise VersionConflict(current, record.version)
        event = stored.event

        self._publish(record.workspace_id, event)
        if source == "user":
            summary = summarize_patch(event, record.content, target_ops)
            await self._notify_user_edit(record, summary, event)
        return event

    def _publish(self, workspace_id: str, event: PatchEvent) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(workspace_id, PATCH_APPLIED_EVENT, event.model_dump(mode="json"))
        except Exception:  # patch sudah ter-commit; klien resync via patches_since
            logger.exception("Gagal mempublikasikan patch.applied %s", event.id)

    async def _notify_user_edit(
        self, record: DashboardRecord, summary: str, event: PatchEvent
    ) -> None:
        hook = self.on_user_edit
        if hook is None:
            return
        try:
            result = hook(record.id, record.workspace_id, summary, event)
            if inspect.isawaitable(result):
                await result
        except Exception:  # hook tidak boleh membatalkan patch yang sudah ter-commit
            logger.exception("Hook on_user_edit gagal untuk patch %s", event.id)

    # ------------------------------------------------------------ API publik
    async def create_dashboard(self, workspace_id: str, title: str) -> DashboardSnapshot:
        """Buat Dashboard kosong versi 0 (``POST /workspaces/{ws}/dashboards``)."""
        record = await self.repos.dashboards.create(
            workspace_id, title, dashboard_id=self._new_id()
        )
        return await self._snapshot(record)

    async def get(self, dashboard_id: str) -> DashboardSnapshot:
        """Snapshot Dashboard + ``can_undo``/``can_redo`` + status turunan item."""
        record = await self.repos.dashboards.get(dashboard_id)
        return await self._snapshot(record)

    async def _snapshot(self, record: DashboardRecord) -> DashboardSnapshot:
        content = record.content
        query_ids = {item_query_id(item) for item in content.items.values()}
        queries = {
            qid: q
            for qid, q in (await self.repos.queries.get_many(query_ids)).items()
            if q.workspace_id == record.workspace_id
        }
        confirmed = await self.repos.relations.confirmed_ids(record.workspace_id)
        versions = await self.repos.datasets.data_versions(record.workspace_id)
        return DashboardSnapshot(
            id=record.id,
            title=content.title,
            version=record.version,
            content=content,
            can_undo=bool(record.undo_stack),
            can_redo=bool(record.redo_stack),
            item_status=dashboard_item_status(content, queries, confirmed, versions),
        )

    async def apply(
        self,
        dashboard_id: str,
        command: Command | Mapping[str, Any],
        base_version: int,
        source: PatchSource,
        actor_run_id: str | None = None,
    ) -> PatchEvent:
        """Terapkan ``command`` sebagai satu Patch_Event ``normal`` (Req 18.2)."""
        parsed = self._parse_command(command)
        async with self._lock(dashboard_id):
            record = await self.repos.dashboards.get(dashboard_id)
            if base_version != record.version:
                raise VersionConflict(record.version, base_version)
            cache: dict[str, QueryRecord] = {}
            ops = await self._resolve(record, parsed, cache)
            ops = await self._validate_ops(record.workspace_id, ops, cache)
            return await self._commit(
                record,
                source=source,
                kind="normal",
                ops=ops,
                inverse_ops=invert_ops(ops),
                target_patch_id=None,
                command=parsed,
                actor_run_id=actor_run_id,
            )

    async def undo(
        self,
        dashboard_id: str,
        base_version: int | None = None,
        source: PatchSource = "user",
        actor_run_id: str | None = None,
    ) -> PatchEvent:
        """Batalkan patch terakhir yang belum dibatalkan sebagai patch ``undo`` (Req 19.1)."""
        async with self._lock(dashboard_id):
            record = await self.repos.dashboards.get(dashboard_id)
            self._check_version(record, base_version)
            history = History.from_lists(record.undo_stack, record.redo_stack)
            target = (await self.repos.patches.get(plan_undo(history))).event
            fields = undo_patch_fields(history, target)
            return await self._commit(
                record,
                source=source,
                kind="undo",
                ops=fields["ops"],
                inverse_ops=fields["inverse_ops"],
                target_patch_id=fields["target_patch_id"],
                command=None,
                actor_run_id=actor_run_id,
                target_ops=target.ops,
            )

    async def redo(
        self,
        dashboard_id: str,
        base_version: int | None = None,
        source: PatchSource = "user",
        actor_run_id: str | None = None,
    ) -> PatchEvent:
        """Terapkan ulang patch yang terakhir dibatalkan sebagai patch ``redo`` (Req 19.2)."""
        async with self._lock(dashboard_id):
            record = await self.repos.dashboards.get(dashboard_id)
            self._check_version(record, base_version)
            history = History.from_lists(record.undo_stack, record.redo_stack)
            target = (await self.repos.patches.get(plan_redo(history))).event
            fields = redo_patch_fields(history, target)
            return await self._commit(
                record,
                source=source,
                kind="redo",
                ops=fields["ops"],
                inverse_ops=fields["inverse_ops"],
                target_patch_id=fields["target_patch_id"],
                command=None,
                actor_run_id=actor_run_id,
                target_ops=target.ops,
            )

    async def patches_since(self, dashboard_id: str, version: int) -> list[PatchEvent]:
        """Patch_Event dengan ``version > version``, urut naik (resync SSE, Req 16.5)."""
        await self.repos.dashboards.get_version(dashboard_id)  # 404 bila tidak ada
        records = await self.repos.patches.list_since(dashboard_id, version)
        return [r.event for r in records]

    async def history(self, dashboard_id: str) -> list[PatchEvent]:
        """Seluruh Patch_Event Dashboard (audit/replay)."""
        await self.repos.dashboards.get_version(dashboard_id)
        return [r.event for r in await self.repos.patches.list_by_dashboard(dashboard_id)]


__all__ = [
    "PATCH_APPLIED_EVENT",
    "VersionConflict",
    "UnknownQuery",
    "DashboardSnapshot",
    "DashboardStore",
    "UserEditHook",
    "describe_ops",
    "summarize_patch",
]
