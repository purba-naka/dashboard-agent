"""Validasi dan seleksi Dashboard_Blueprint (murni; Req 37.2, 37.3, 37.6).

``validate_blueprint(bp, existing_layout, known_metrics, known_columns)``
mengembalikan daftar ``BlueprintIssue`` (kosong = valid). ``finalize_blueprint``
menghitung layout final dengan Layout_Template lalu memvalidasi ulang.
``select_slots`` mengambil slot terpilih tanpa menggeser layout slot lain,
sehingga wireframe yang disetujui sama dengan hasil akhir.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from studio.api.errors import StudioError
from studio.core.layout_templates import place_slots, rects_overlap
from studio.core.models import GRID_COLUMNS, DashboardBlueprint, LayoutRect

__all__ = [
    "MAX_SLOTS",
    "BlueprintIssue",
    "BlueprintInvalid",
    "validate_blueprint",
    "finalize_blueprint",
    "select_slots",
]

MAX_SLOTS = 16

#: Tipe visual yang tidak masuk akal di bagian tertentu.
_FORBIDDEN_VISUALS: dict[str, frozenset[str]] = {
    "kpi_row": frozenset({"pie", "scatter", "heatmap", "line", "bar", "waterfall"}),
}


@dataclass(frozen=True)
class BlueprintIssue:
    slot_id: str | None
    rule: str
    detail: str

    def to_json(self) -> dict[str, Any]:
        return {"slot_id": self.slot_id, "rule": self.rule, "detail": self.detail}


class BlueprintInvalid(StudioError):
    def __init__(self, issues: Iterable[BlueprintIssue]) -> None:
        listed = [i.to_json() for i in issues]
        super().__init__(
            "BLUEPRINT_INVALID",
            "Dashboard_Blueprint tidak valid: "
            + "; ".join(f"{i['slot_id'] or '-'}: {i['rule']}" for i in listed),
            {"issues": listed},
            http_status=422,
        )


def _existing(existing_layout: Mapping[str, LayoutRect] | Iterable[LayoutRect]) -> list[LayoutRect]:
    if isinstance(existing_layout, Mapping):
        return list(existing_layout.values())
    return list(existing_layout)


def validate_blueprint(
    bp: DashboardBlueprint,
    existing_layout: Mapping[str, LayoutRect] | Iterable[LayoutRect] = (),
    known_metrics: Collection[str] = (),
    known_columns: Collection[str] = (),
) -> list[BlueprintIssue]:
    """Periksa aturan Req 37.2. ``known_columns`` berisi nama kolom (atau ``tabel.kolom``)."""
    issues: list[BlueprintIssue] = []
    if len(bp.slots) > MAX_SLOTS:
        issues.append(BlueprintIssue(None, "TOO_MANY_SLOTS", f"{len(bp.slots)} slot (maks {MAX_SLOTS})"))

    seen: set[str] = set()
    for slot in bp.slots:
        if slot.slot_id in seen:
            issues.append(BlueprintIssue(slot.slot_id, "DUPLICATE_SLOT_ID", "id slot sudah dipakai"))
        seen.add(slot.slot_id)

    metrics = set(known_metrics)
    columns = set(known_columns)
    for slot in bp.slots:
        for m in slot.metrics:
            if m not in metrics and m not in columns:
                issues.append(BlueprintIssue(slot.slot_id, "UNKNOWN_METRIC", f"metrik/kolom '{m}' tidak dikenal"))
        if slot.dimension is not None and slot.dimension not in columns:
            issues.append(BlueprintIssue(slot.slot_id, "UNKNOWN_DIMENSION", f"dimensi '{slot.dimension}' tidak dikenal"))
        if slot.visual in _FORBIDDEN_VISUALS.get(slot.section, frozenset()):
            issues.append(
                BlueprintIssue(slot.slot_id, "VISUAL_SECTION_MISMATCH", f"visual '{slot.visual}' tidak cocok untuk bagian '{slot.section}'")
            )

    existing = _existing(existing_layout)
    placed = [(s.slot_id, s.layout) for s in bp.slots if s.layout is not None]
    for slot_id, rect in placed:
        assert rect is not None
        if rect.x + rect.w > GRID_COLUMNS:
            issues.append(BlueprintIssue(slot_id, "LAYOUT_OUT_OF_GRID", "layout keluar grid 12 kolom"))
        if any(rects_overlap(rect, other) for other in existing):
            issues.append(BlueprintIssue(slot_id, "LAYOUT_OVERLAP", "tumpang tindih dengan item yang ada"))
    for i, (a_id, a) in enumerate(placed):
        for b_id, b in placed[i + 1 :]:
            if rects_overlap(a, b):  # type: ignore[arg-type]
                issues.append(BlueprintIssue(b_id, "LAYOUT_OVERLAP", f"tumpang tindih dengan slot '{a_id}'"))
    return issues


def finalize_blueprint(
    bp: DashboardBlueprint,
    existing_layout: Mapping[str, LayoutRect] | Iterable[LayoutRect] = (),
    known_metrics: Collection[str] = (),
    known_columns: Collection[str] = (),
) -> DashboardBlueprint:
    """Validasi → isi layout lewat Layout_Template → validasi ulang; raise ``BlueprintInvalid``."""
    issues = validate_blueprint(bp, existing_layout, known_metrics, known_columns)
    if issues:
        raise BlueprintInvalid(issues)
    layouts = place_slots(bp.slots, existing_layout)
    final = bp.model_copy(
        update={"slots": [s.model_copy(update={"layout": layouts[s.slot_id]}) for s in bp.slots]}
    )
    issues = validate_blueprint(final, existing_layout, known_metrics, known_columns)
    if issues:  # pragma: no cover — dijamin Layout_Template
        raise BlueprintInvalid(issues)
    return final


def select_slots(bp: DashboardBlueprint, slot_ids: Iterable[str] | None) -> DashboardBlueprint:
    """Subset slot terpilih (urutan asli, layout tidak berubah). ``None`` = semua."""
    if slot_ids is None:
        return bp
    wanted = set(slot_ids)
    unknown = wanted - {s.slot_id for s in bp.slots}
    if unknown:
        raise BlueprintInvalid([BlueprintIssue(u, "UNKNOWN_SLOT", "slot tidak ada di Blueprint") for u in sorted(unknown)])
    chosen = [s for s in bp.slots if s.slot_id in wanted]
    if not chosen:
        raise BlueprintInvalid([BlueprintIssue(None, "NO_SLOT_SELECTED", "minimal satu slot harus dipilih")])
    return bp.model_copy(update={"slots": chosen})
