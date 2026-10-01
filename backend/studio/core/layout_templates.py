"""Layout_Template: penempatan otomatis Blueprint_Slot di grid 12 kolom (murni; Req 37.4, 37.13).

``place_slots(slots, existing_layout)``:

* slot ber-layout dipertahankan apa adanya;
* slot tanpa layout dikelompokkan per ``section`` dengan urutan
  ``kpi_row, trend, breakdown, composition, distribution, other, detail``
  (urutan input dipertahankan dalam kelompok) dan ditempatkan mulai dari bawah
  semua item yang ada dan slot ber-layout;
* ``kpi_row``: maks 6 per baris, lebar ``12 // n`` (sisa ke slot terakhir), tinggi 2;
* ``trend``: lebar 8 tinggi 6, dengan slot non-KPI berikutnya di sampingnya
  (lebar 4); bila tidak ada pendamping, lebar 12;
* ``breakdown``/``composition``/``distribution``/``other``: berpasangan lebar 6
  tinggi 6; slot ganjil terakhir lebar 12;
* ``detail``: lebar 12 tinggi 6;
* setiap baris diperiksa terhadap rect yang sudah terisi dan digeser ke bawah
  sampai bebas, sehingga hasil tidak pernah tumpang tindih.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from studio.core.models import GRID_COLUMNS, BlueprintSlot, LayoutRect

__all__ = [
    "SECTION_ORDER",
    "KPI_ROW_MAX",
    "KPI_HEIGHT",
    "CHART_HEIGHT",
    "rects_overlap",
    "place_slots",
]

SECTION_ORDER: tuple[str, ...] = (
    "kpi_row",
    "trend",
    "breakdown",
    "composition",
    "distribution",
    "other",
    "detail",
)
KPI_ROW_MAX = 6
KPI_HEIGHT = 2
CHART_HEIGHT = 6
_PAIRED = frozenset({"breakdown", "composition", "distribution", "other"})


def rects_overlap(a: LayoutRect, b: LayoutRect) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def _bottom(rects: Iterable[LayoutRect]) -> int:
    return max((r.y + r.h for r in rects), default=0)


class _Grid:
    def __init__(self, occupied: Iterable[LayoutRect]) -> None:
        self.occupied: list[LayoutRect] = list(occupied)

    def place_row(self, y: int, row: Sequence[tuple[int, int, int]]) -> tuple[int, list[LayoutRect]]:
        """Tempatkan satu baris ``[(x, w, h)]`` pada ``y`` terkecil ≥ ``y`` yang bebas."""
        while True:
            rects = [LayoutRect(x=x, y=y, w=w, h=h) for x, w, h in row]
            clash = [o for r in rects for o in self.occupied if rects_overlap(r, o)]
            if not clash:
                self.occupied.extend(rects)
                return y + max(r.h for r in rects), rects
            y = min(o.y + o.h for o in clash)


def _rows_for(section: str, slots: list[BlueprintSlot]) -> list[list[tuple[BlueprintSlot, int, int, int]]]:
    rows: list[list[tuple[BlueprintSlot, int, int, int]]] = []
    if section == "kpi_row":
        for i in range(0, len(slots), KPI_ROW_MAX):
            chunk = slots[i : i + KPI_ROW_MAX]
            w = GRID_COLUMNS // len(chunk)
            row = []
            for j, slot in enumerate(chunk):
                width = w + (GRID_COLUMNS - w * len(chunk) if j == len(chunk) - 1 else 0)
                row.append((slot, j * w, width, KPI_HEIGHT))
            rows.append(row)
    elif section == "detail":
        rows = [[(s, 0, GRID_COLUMNS, CHART_HEIGHT)] for s in slots]
    else:  # pasangan lebar 6
        for i in range(0, len(slots), 2):
            pair = slots[i : i + 2]
            if len(pair) == 2:
                rows.append([(pair[0], 0, 6, CHART_HEIGHT), (pair[1], 6, 6, CHART_HEIGHT)])
            else:
                rows.append([(pair[0], 0, GRID_COLUMNS, CHART_HEIGHT)])
    return rows


def place_slots(
    slots: Sequence[BlueprintSlot], existing_layout: Mapping[str, LayoutRect] | Iterable[LayoutRect] = ()
) -> dict[str, LayoutRect]:
    """Layout final ``{slot_id: rect}`` untuk semua slot (lihat docstring modul)."""
    existing = list(existing_layout.values()) if isinstance(existing_layout, Mapping) else list(existing_layout)
    fixed = {s.slot_id: s.layout for s in slots if s.layout is not None}
    grid = _Grid([*existing, *fixed.values()])
    y = _bottom(grid.occupied)

    groups: dict[str, list[BlueprintSlot]] = {name: [] for name in SECTION_ORDER}
    for slot in slots:
        if slot.layout is None:
            groups.get(slot.section, groups["other"]).append(slot)

    result: dict[str, LayoutRect] = dict(fixed)  # type: ignore[arg-type]

    def emit(row: list[tuple[BlueprintSlot, int, int, int]]) -> None:
        nonlocal y
        y, rects = grid.place_row(y, [(x, w, h) for _, x, w, h in row])
        for (slot, *_), rect in zip(row, rects):
            result[slot.slot_id] = rect

    for row in _rows_for("kpi_row", groups["kpi_row"]):
        emit(row)

    # Tren: lebar 8 + pendamping lebar 4 dari kelompok berpasangan berikutnya.
    companions = [s for name in ("breakdown", "composition", "distribution", "other") for s in groups[name]]
    for trend in groups["trend"]:
        if companions:
            side = companions.pop(0)
            emit([(trend, 0, 8, CHART_HEIGHT), (side, 8, 4, CHART_HEIGHT)])
        else:
            emit([(trend, 0, GRID_COLUMNS, CHART_HEIGHT)])

    used = set(result)
    for name in ("breakdown", "composition", "distribution", "other"):
        remaining = [s for s in groups[name] if s.slot_id not in used]
        for row in _rows_for(name, remaining):
            emit(row)
    for row in _rows_for("detail", groups["detail"]):
        emit(row)

    # Urutan kunci mengikuti urutan input slot.
    return {s.slot_id: result[s.slot_id] for s in slots}
