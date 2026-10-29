"""Kerangka canvas dari Dashboard_Blueprint (pengganti ``place_slots`` untuk OpenUI, D8/D11).

Blueprint_Builder menulis kerangka container yang merujuk nama slot
(``slot_<slot_id>``) sebelum slot dibangun. Slot_Builder lalu cukup
mendefinisikan statement bernama itu; rujukan yang belum terdefinisi tampil
kosong (peringatan ``UNRESOLVED_REF``) sampai slot selesai.

Aturan tata letak (setara Layout_Template lama):

- ``kpi_row`` → satu ``HContainer`` berisi semua KPI.
- ``trend`` → lebar 2/3, dipasangkan dengan slot non-detail berikutnya (1/3).
- slot lain dipasangkan berdua dengan lebar sama; sisa ganjil penuh.
- ``detail`` → penuh, di bagian bawah.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from studio.core.openui.program import Call, Ref, statement_line


def slot_statement_name(slot_id: str) -> str:
    return f"slot_{slot_id}"


def blueprint_skeleton(
    slots: Sequence[Mapping[str, Any]],
    existing_root_children: Sequence[str] = (),
) -> str:
    """Patch OpenUI Lang: baris container + ``root`` yang memuat slot Blueprint."""
    lines: list[str] = []
    rows: list[Ref] = [Ref(n) for n in existing_root_children]
    prefix = f"bp{len(existing_root_children)}"

    def add_row(name: str, members: list[str], weights: list[int] | None) -> None:
        if len(members) == 1:
            rows.append(Ref(members[0]))
            return
        lines.append(statement_line(name, Call("HContainer", ([Ref(m) for m in members], weights))))
        rows.append(Ref(name))

    kpis = [slot_statement_name(s["slot_id"]) for s in slots if s.get("section") == "kpi_row" or s.get("visual") == "kpi"]
    if kpis:
        add_row(f"{prefix}_kpis", kpis, None)

    body = [s for s in slots if slot_statement_name(s["slot_id"]) not in kpis and s.get("section") != "detail"]
    details = [s for s in slots if s.get("section") == "detail" and slot_statement_name(s["slot_id"]) not in kpis]
    i = 0
    row_no = 1
    while i < len(body):
        first = body[i]
        pair = body[i + 1] if i + 1 < len(body) else None
        names = [slot_statement_name(first["slot_id"])]
        weights: list[int] | None = None
        if pair is not None:
            names.append(slot_statement_name(pair["slot_id"]))
            weights = [8, 4] if first.get("section") == "trend" else [6, 6]
            i += 2
        else:
            i += 1
        add_row(f"{prefix}_row{row_no}", names, weights)
        row_no += 1
    for s in details:
        rows.append(Ref(slot_statement_name(s["slot_id"])))

    lines.insert(0, statement_line("root", Call("VContainer", (rows,))))
    return "\n".join(lines)


__all__ = ["blueprint_skeleton", "slot_statement_name"]
