"""Resolusi command, penerapan, inversi, dan replay ops Dashboard (murni).

Alur (lihat design "Patch_Event & versioning"):

1. ``resolve_command(content, command)`` mengubah ``Command`` menjadi ops
   *self-contained* yang membawa snapshot ``before``.
2. ``apply_ops(content, ops)`` menerapkan ops secara murni; setiap prasyarat
   diperiksa terhadap state saat ini dan pelanggaran memunculkan ``InvalidOp``.
3. ``invert_ops(ops)`` menghasilkan ops kebalikan (urutan dibalik) sehingga
   ``apply_ops(apply_ops(c, ops), invert_ops(ops)) == c``.
4. ``replay(initial, history)`` merekonstruksi content dari Patch_Event.

Modul ini tidak menyentuh I/O. Konversi tipe chart (``core/chart_convert.py``)
dan pembuat id diinjeksikan oleh pemanggil (Dashboard_Store).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError
from ulid import ULID

from studio.api.errors import StudioError
from studio.core.layout_templates import rects_overlap
from studio.core.models import (
    GRID_COLUMNS,
    AddChartCommand,
    AddInsightCommand,
    AddItemOp,
    ChangeChartTypeCommand,
    ChartItem,
    ChartSpec,
    ChartType,
    Command,
    DashboardContent,
    DashboardItem,
    AddKpiCommand,
    ApplyCanvasPatchCommand,
    CanvasStatement,
    InsightItem,
    SetStatementOp,
    KpiItem,
    LayoutChange,
    LayoutRect,
    Op,
    PatchEvent,
    RemoveItemCommand,
    RemoveItemOp,
    SetBriefCommand,
    SetBriefOp,
    UpdateKpiCommand,
    SetFiltersOp,
    SetGlobalFiltersCommand,
    SetItemOp,
    SetLayoutCommand,
    TidyLayoutCommand,
    SetLayoutOp,
    SetTitleCommand,
    SetTitleOp,
    UpdateChartCommand,
    UpdateInsightCommand,
)

#: Ukuran default item baru bila command tidak menyertakan ``layout``.
DEFAULT_CHART_SIZE: tuple[int, int] = (6, 4)  # (w, h)
DEFAULT_INSIGHT_SIZE: tuple[int, int] = (6, 3)
DEFAULT_KPI_SIZE: tuple[int, int] = (3, 2)

#: ``(spec, chart_type_baru) -> spec_baru``; biasanya ``convert_chart_type`` dari
#: ``core/chart_convert.py`` yang sudah di-bind ke skema hasil query tersimpan.
ChartConverter = Callable[[ChartSpec, ChartType], ChartSpec]
IdFactory = Callable[[], str]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class InvalidOp(StudioError):
    """Prasyarat op/command tidak terpenuhi; tidak ada state yang diubah."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("INVALID_OP", message, details, http_status=422)


class ItemNotFound(StudioError):
    """Command merujuk item yang tidak ada di Dashboard."""

    def __init__(self, item_id: str) -> None:
        super().__init__(
            "NOT_FOUND",
            f"Item '{item_id}' tidak ditemukan di Dashboard.",
            {"item_id": item_id},
            http_status=404,
        )


class ChartConversionUnavailable(StudioError):
    """``change_chart_type`` di-resolve tanpa converter yang diinjeksikan."""

    def __init__(self) -> None:
        super().__init__(
            "CHART_CONVERSION_UNAVAILABLE",
            "change_chart_type membutuhkan parameter convert_chart_type "
            "(core/chart_convert.py) pada resolve_command.",
            http_status=500,
        )


# ---------------------------------------------------------------------------
# Layout default: baris kosong pertama
# ---------------------------------------------------------------------------


def _rows_free(layout: Iterable[LayoutRect], y: int, h: int) -> bool:
    """True bila tidak ada rect yang menempati baris ``[y, y + h)``."""
    return all(r.y + r.h <= y or r.y >= y + h for r in layout)


def first_empty_row_layout(
    layout: Mapping[str, LayoutRect], w: int, h: int
) -> LayoutRect:
    """Rect ``(x=0, w, h)`` pada ``y`` terkecil sehingga baris ``[y, y+h)`` kosong.

    Selalu ada solusi: ``y = max(r.y + r.h)`` (di bawah semua item).
    """
    w = max(1, min(w, GRID_COLUMNS))
    rects = list(layout.values())
    # Kandidat cukup 0 dan tepi bawah/kanan setiap rect; urut y lalu x (first-fit).
    ys = sorted({0, *(r.y + r.h for r in rects)})
    xs = sorted({0, *(r.x + r.w for r in rects)})
    for y in ys:
        for x in xs:
            if x + w > GRID_COLUMNS:
                continue
            cand = LayoutRect(x=x, y=y, w=w, h=h)
            if not any(rects_overlap(cand, r) for r in rects):
                return cand
    raise AssertionError("unreachable: tepi bawah terakhir selalu kosong")

def push_down_collisions(
    layout: Mapping[str, LayoutRect], fixed: Mapping[str, LayoutRect]
) -> list[LayoutChange]:
    """Dorong item yang bertabrakan dengan ``fixed`` ke bawah (compact vertikal).

    ``layout`` = state saat ini TANPA ``fixed``. Hasil: perubahan layout item lain
    sehingga tidak ada tumpang tindih; item ``fixed`` tidak pernah bergeser.
    """
    placed: list[LayoutRect] = list(fixed.values())
    changes: list[LayoutChange] = []
    for item_id, rect in sorted(layout.items(), key=lambda kv: (kv[1].y, kv[1].x)):
        if item_id in fixed:
            continue
        new = rect
        while True:
            clash = [p for p in placed if rects_overlap(new, p)]
            if not clash:
                break
            new = new.model_copy(update={"y": max(p.y + p.h for p in clash)})
        placed.append(new)
        if new != rect:
            changes.append(LayoutChange(id=item_id, before=rect, after=new))
    return changes

def tidy_layout(content: DashboardContent) -> dict[str, LayoutRect]:
    """Susun ulang tanpa celah/tabrakan: KPI, lalu chart, lalu insight; urut (y, x) saat ini.

    Lebar dan tinggi item dipertahankan. Tiap item ditaruh di posisi first-fit paling
    atas-kiri yang muat (shelf packing), sehingga agent tidak perlu menghitung koordinat.
    """
    order = {"kpi": 0, "chart": 1, "insight": 2}
    ids = sorted(
        content.layout,
        key=lambda i: (order.get(content.items[i].kind, 1), content.layout[i].y, content.layout[i].x),
    )
    placed: dict[str, LayoutRect] = {}
    for item_id in ids:
        r = content.layout[item_id]
        placed[item_id] = first_empty_row_layout(placed, r.w, r.h)
    return placed

def _add_with_layout(content: DashboardContent, item: DashboardItem, layout: LayoutRect) -> list[Op]:
    """``add_item`` + dorongan item yang bertabrakan dengan rect baru."""
    pushed = push_down_collisions(content.layout, {item.id: layout})
    ops: list[Op] = [AddItemOp(item=item, layout=layout)]
    if pushed:
        ops.append(SetLayoutOp(changes=pushed))
    return ops


# ---------------------------------------------------------------------------
# resolve_command
# ---------------------------------------------------------------------------


def _default_id() -> str:
    return str(ULID())


def _get_item(content: DashboardContent, item_id: str) -> DashboardItem:
    item = content.items.get(item_id)
    if item is None:
        raise ItemNotFound(item_id)
    return item


def _get_chart(content: DashboardContent, item_id: str) -> ChartItem:
    item = _get_item(content, item_id)
    if not isinstance(item, ChartItem):
        raise InvalidOp(
            f"Item '{item_id}' bukan chart.",
            {"item_id": item_id, "kind": item.kind},
        )
    return item


def _get_insight(content: DashboardContent, item_id: str) -> InsightItem:
    item = _get_item(content, item_id)
    if not isinstance(item, InsightItem):
        raise InvalidOp(
            f"Item '{item_id}' bukan Insight_Card.",
            {"item_id": item_id, "kind": item.kind},
        )
    return item


def _get_kpi(content: DashboardContent, item_id: str) -> KpiItem:
    item = _get_item(content, item_id)
    if not isinstance(item, KpiItem):
        raise InvalidOp(
            f"Item '{item_id}' bukan KPI_Card.",
            {"item_id": item_id, "kind": item.kind},
        )
    return item


def _new_item_id(content: DashboardContent, new_id: IdFactory) -> str:
    item_id = new_id()
    if item_id in content.items:
        raise InvalidOp(f"Id item baru '{item_id}' sudah ada.", {"item_id": item_id})
    return item_id


def resolve_command(
    content: DashboardContent,
    command: Command,
    *,
    new_id: IdFactory | None = None,
    convert_chart_type: ChartConverter | None = None,
) -> list[Op]:
    """Ubah ``command`` menjadi ops self-contained terhadap ``content`` saat ini.

    ``new_id`` membuat id item baru (default ULID). ``convert_chart_type``
    wajib untuk ``change_chart_type``; hasilnya dipakai apa adanya (validasi
    ulang spec adalah tanggung jawab converter / Dashboard_Store).
    """
    make_id = new_id or _default_id

    if isinstance(command, AddChartCommand):
        item_id = _new_item_id(content, make_id)
        item = ChartItem(id=item_id, title=command.title, spec=command.spec)
        layout = command.layout or first_empty_row_layout(
            content.layout, *DEFAULT_CHART_SIZE
        )
        return _add_with_layout(content, item, layout)

    if isinstance(command, AddInsightCommand):
        item_id = _new_item_id(content, make_id)
        item = InsightItem.model_validate(
            {**command.insight.model_dump(), "id": item_id}
        )
        layout = command.layout or first_empty_row_layout(
            content.layout, *DEFAULT_INSIGHT_SIZE
        )
        return _add_with_layout(content, item, layout)

    if isinstance(command, AddKpiCommand):
        item_id = _new_item_id(content, make_id)
        item = KpiItem(id=item_id, title=command.title, spec=command.spec)
        layout = command.layout or first_empty_row_layout(
            content.layout, *DEFAULT_KPI_SIZE
        )
        return _add_with_layout(content, item, layout)

    if isinstance(command, UpdateKpiCommand):
        before = _get_kpi(content, command.id)
        after = KpiItem(
            id=before.id,
            title=command.title if command.title is not None else before.title,
            spec=command.spec,
        )
        return [SetItemOp(id=before.id, before=before, after=after)]

    if isinstance(command, SetBriefCommand):
        return [SetBriefOp(before=content.brief, after=command.brief)]

    if isinstance(command, UpdateChartCommand):
        before = _get_chart(content, command.id)
        after = ChartItem(
            id=before.id,
            title=command.title if command.title is not None else before.title,
            spec=command.spec,
        )
        return [SetItemOp(id=before.id, before=before, after=after)]

    if isinstance(command, ChangeChartTypeCommand):
        before = _get_chart(content, command.id)
        if convert_chart_type is None:
            raise ChartConversionUnavailable()
        new_spec = convert_chart_type(before.spec, command.chart_type)
        after = ChartItem(id=before.id, title=before.title, spec=new_spec)
        return [SetItemOp(id=before.id, before=before, after=after)]

    if isinstance(command, UpdateInsightCommand):
        before = _get_insight(content, command.id)
        updates = {
            name: value
            for name, value in command.changes.model_dump().items()
            if value is not None
        }
        if not updates:
            raise InvalidOp(
                "update_insight tidak berisi perubahan.", {"item_id": command.id}
            )
        after = InsightItem.model_validate({**before.model_dump(), **updates})
        return [SetItemOp(id=before.id, before=before, after=after)]

    if isinstance(command, RemoveItemCommand):
        item = _get_item(content, command.id)
        return [RemoveItemOp(item=item, layout=content.layout[command.id])]

    if isinstance(command, SetLayoutCommand):
        changes = []
        for item_id, rect in command.changes.items():
            if item_id not in content.layout:
                raise ItemNotFound(item_id)
            changes.append(
                LayoutChange(id=item_id, before=content.layout[item_id], after=rect)
            )
        # Item yang dipindah juga bisa saling menabrak (mis. tukar posisi beda ukuran):
        # yang pertama tetap, berikutnya didorong ke bawah.
        placed: list[LayoutRect] = []
        for change in changes:
            rect = change.after
            while clash := [p for p in placed if rects_overlap(rect, p)]:
                rect = rect.model_copy(update={"y": max(p.y + p.h for p in clash)})
            change.after = rect
            placed.append(rect)
        moved = {c.id: c.after for c in changes}
        rest = {k: v for k, v in content.layout.items() if k not in moved}
        changes += push_down_collisions(rest, moved)
        return [SetLayoutOp(changes=changes)]

    if isinstance(command, TidyLayoutCommand):
        changes = [
            LayoutChange(id=i, before=content.layout[i], after=r)
            for i, r in tidy_layout(content).items()
            if r != content.layout[i]
        ]
        if not changes:
            raise StudioError("NO_CHANGES", "Layout sudah rapi.", http_status=422)
        return [SetLayoutOp(changes=changes)]

    if isinstance(command, SetGlobalFiltersCommand):
        return [
            SetFiltersOp(
                before=list(content.global_filters), after=list(command.filters)
            )
        ]

    if isinstance(command, SetTitleCommand):
        return [SetTitleOp(before=content.title, after=command.title)]

    if isinstance(command, ApplyCanvasPatchCommand):
        # Butuh sidecar + query tersimpan (I/O): di-resolve Dashboard_Store.
        raise InvalidOp(
            "apply_canvas_patch di-resolve oleh Dashboard_Store, bukan resolve_command.",
            {"type": command.type},
        )

    raise InvalidOp(
        f"Command tidak dikenal: {type(command).__name__}.",
        {"type": getattr(command, "type", None)},
    )


# ---------------------------------------------------------------------------
# apply_ops
# ---------------------------------------------------------------------------


def apply_ops(content: DashboardContent, ops: Sequence[Op]) -> DashboardContent:
    """Terapkan ``ops`` berurutan secara murni; raise ``InvalidOp`` bila prasyarat gagal.

    ``content`` tidak dimodifikasi. Semua ops divalidasi terhadap state hasil
    ops sebelumnya; bila satu gagal, tidak ada hasil parsial yang dikembalikan.
    """
    title = content.title
    items: dict[str, DashboardItem] = dict(content.items)
    layout: dict[str, LayoutRect] = dict(content.layout)
    filters = list(content.global_filters)
    brief = content.brief
    statements: dict[str, CanvasStatement] = dict(content.statements)

    for index, op in enumerate(ops):
        where = {"op_index": index, "op": op.op}

        if isinstance(op, AddItemOp):
            item_id = op.item.id
            if item_id in items or item_id in layout:
                raise InvalidOp(
                    f"add_item: item '{item_id}' sudah ada.", {**where, "item_id": item_id}
                )
            items[item_id] = op.item
            layout[item_id] = op.layout

        elif isinstance(op, RemoveItemOp):
            item_id = op.item.id
            if item_id not in items:
                raise InvalidOp(
                    f"remove_item: item '{item_id}' tidak ada.",
                    {**where, "item_id": item_id},
                )
            if items[item_id] != op.item or layout.get(item_id) != op.layout:
                raise InvalidOp(
                    f"remove_item: snapshot item '{item_id}' tidak sama dengan state saat ini.",
                    {**where, "item_id": item_id},
                )
            del items[item_id]
            del layout[item_id]

        elif isinstance(op, SetItemOp):
            current = items.get(op.id)
            if current is None:
                raise InvalidOp(
                    f"set_item: item '{op.id}' tidak ada.", {**where, "item_id": op.id}
                )
            if current != op.before:
                raise InvalidOp(
                    f"set_item: before item '{op.id}' tidak sama dengan state saat ini.",
                    {**where, "item_id": op.id},
                )
            items[op.id] = op.after

        elif isinstance(op, SetLayoutOp):
            for change in op.changes:
                current_rect = layout.get(change.id)
                if current_rect is None:
                    raise InvalidOp(
                        f"set_layout: item '{change.id}' tidak ada.",
                        {**where, "item_id": change.id},
                    )
                if current_rect != change.before:
                    raise InvalidOp(
                        f"set_layout: before layout '{change.id}' tidak sama dengan state saat ini.",
                        {**where, "item_id": change.id},
                    )
            for change in op.changes:
                layout[change.id] = change.after

        elif isinstance(op, SetFiltersOp):
            if filters != list(op.before):
                raise InvalidOp(
                    "set_filters: before tidak sama dengan global_filters saat ini.", where
                )
            filters = list(op.after)

        elif isinstance(op, SetTitleOp):
            if title != op.before:
                raise InvalidOp(
                    "set_title: before tidak sama dengan judul saat ini.", where
                )
            title = op.after

        elif isinstance(op, SetBriefOp):
            if brief != op.before:
                raise InvalidOp(
                    "set_brief: before tidak sama dengan brief saat ini.", where
                )
            brief = op.after

        elif isinstance(op, SetStatementOp):
            current_stmt = statements.get(op.name)
            if current_stmt != op.before:
                raise InvalidOp(
                    f"set_statement: before statement '{op.name}' tidak sama dengan state saat ini.",
                    {**where, "name": op.name},
                )
            if op.after is None:
                del statements[op.name]
            else:
                statements[op.name] = op.after

        else:
            raise InvalidOp(f"Op tidak dikenal: {type(op).__name__}.", where)

    try:
        return DashboardContent(
            title=title,
            items=items,
            layout=layout,
            global_filters=filters,
            brief=brief,
            statements=statements,
        )
    except ValidationError as exc:
        raise InvalidOp(
            "Hasil ops melanggar invariant Dashboard.",
            {"errors": exc.errors(include_url=False, include_context=False)},
        ) from exc


# ---------------------------------------------------------------------------
# invert_ops & replay
# ---------------------------------------------------------------------------


def invert_op(op: Op) -> Op:
    """Kebalikan satu op (lihat tabel inversi di design)."""
    if isinstance(op, AddItemOp):
        return RemoveItemOp(item=op.item, layout=op.layout)
    if isinstance(op, RemoveItemOp):
        return AddItemOp(item=op.item, layout=op.layout)
    if isinstance(op, SetItemOp):
        return SetItemOp(id=op.id, before=op.after, after=op.before)
    if isinstance(op, SetLayoutOp):
        return SetLayoutOp(
            changes=[
                LayoutChange(id=c.id, before=c.after, after=c.before)
                for c in op.changes
            ]
        )
    if isinstance(op, SetFiltersOp):
        return SetFiltersOp(before=list(op.after), after=list(op.before))
    if isinstance(op, SetTitleOp):
        return SetTitleOp(before=op.after, after=op.before)
    if isinstance(op, SetBriefOp):
        return SetBriefOp(before=op.after, after=op.before)
    if isinstance(op, SetStatementOp):
        return SetStatementOp(name=op.name, before=op.after, after=op.before)
    raise InvalidOp(f"Op tidak dikenal: {type(op).__name__}.")


def invert_ops(ops: Sequence[Op]) -> list[Op]:
    """Ops kebalikan dengan urutan dibalik."""
    return [invert_op(op) for op in reversed(ops)]


def replay(initial: DashboardContent, history: Iterable[PatchEvent]) -> DashboardContent:
    """Terapkan ``ops`` setiap Patch_Event berurutan ke ``initial``.

    Patch undo/redo sudah membawa ops efektifnya sendiri, jadi cukup
    ``apply_ops`` apa adanya. Versi harus berurutan tanpa celah.
    """
    content = initial
    previous: PatchEvent | None = None
    for event in history:
        if previous is not None and event.base_version != previous.version:
            raise InvalidOp(
                "replay: versi Patch_Event tidak berurutan.",
                {
                    "patch_id": event.id,
                    "expected_base_version": previous.version,
                    "base_version": event.base_version,
                },
            )
        content = apply_ops(content, event.ops)
        previous = event
    return content


__all__ = [
    "DEFAULT_CHART_SIZE",
    "DEFAULT_INSIGHT_SIZE",
    "DEFAULT_KPI_SIZE",
    "ChartConverter",
    "IdFactory",
    "InvalidOp",
    "ItemNotFound",
    "ChartConversionUnavailable",
    "first_empty_row_layout",
    "tidy_layout",
    "push_down_collisions",
    "resolve_command",
    "apply_ops",
    "invert_op",
    "invert_ops",
    "replay",
]
