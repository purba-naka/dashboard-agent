"""Logika stack undo/redo Dashboard berbasis patch id (murni & immutable).

State diagram (design "Undo/redo"):

- patch ``normal`` P  → push ``P.id`` ke undo, kosongkan redo (Req 19.3).
- undo                → pop P dari undo, terapkan ``P.inverse_ops`` sebagai patch
                        U (``kind="undo"``, ``target_patch_id=P.id``), push ``P.id``
                        ke redo (Req 19.1). U sendiri tidak masuk stack mana pun.
- redo                → pop P dari redo, terapkan ``P.ops`` sebagai patch R
                        (``kind="redo"``, ``target_patch_id=P.id``), push ``R.id``
                        ke undo (Req 19.2).

Karena ``R.ops == P.ops`` dan ``R.inverse_ops == P.inverse_ops``, meng-undo R
membatalkan efek yang sama dengan meng-undo P; rantai undo/redo berulang tetap
konsisten. Id di stack redo bisa berupa patch ``normal`` maupun ``redo``.

Modul ini tidak melakukan I/O. Dashboard_Store memuat ``History`` dari kolom
``undo_stack_json``/``redo_stack_json`` (list patch id, bawah → atas), memanggil
``plan_undo``/``plan_redo`` untuk mendapat id target, memuat Patch_Event target,
membangun ops lewat ``undo_ops``/``redo_ops``, lalu ``History.record(event)``
setelah patch baru tersimpan.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from studio.api.errors import StudioError
from studio.core.models import Op, PatchEvent

# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class NothingToUndo(StudioError):
    """Stack undo kosong (``POST /dashboards/{id}/undo`` → 400)."""

    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "NOTHING_TO_UNDO",
            "Tidak ada perubahan yang dapat dibatalkan.",
            details,
            http_status=400,
        )


class NothingToRedo(StudioError):
    """Stack redo kosong (``POST /dashboards/{id}/redo`` → 400)."""

    def __init__(self, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            "NOTHING_TO_REDO",
            "Tidak ada perubahan yang dapat diulang.",
            details,
            http_status=400,
        )


class HistoryMismatch(StudioError):
    """Patch undo/redo yang dicatat tidak menargetkan puncak stack (bug internal)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("HISTORY_MISMATCH", message, details, http_status=500)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class History:
    """Stack undo & redo berisi patch id; elemen terakhir adalah puncak stack."""

    undo: tuple[str, ...] = ()
    redo: tuple[str, ...] = ()

    # -- konversi ke/dari kolom JSON ------------------------------------------

    @classmethod
    def from_lists(
        cls, undo: Iterable[str] | None = None, redo: Iterable[str] | None = None
    ) -> History:
        return cls(undo=tuple(undo or ()), redo=tuple(redo or ()))

    def to_lists(self) -> tuple[list[str], list[str]]:
        """``(undo_stack, redo_stack)`` untuk disimpan sebagai JSON."""
        return list(self.undo), list(self.redo)

    # -- query ---------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        return bool(self.undo)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo)

    def peek_undo(self) -> str:
        """Id patch yang akan dibatalkan oleh undo berikutnya."""
        if not self.undo:
            raise NothingToUndo()
        return self.undo[-1]

    def peek_redo(self) -> str:
        """Id patch yang akan diterapkan ulang oleh redo berikutnya."""
        if not self.redo:
            raise NothingToRedo()
        return self.redo[-1]

    # -- transisi (mengembalikan History baru) -------------------------------

    def push_normal(self, patch_id: str) -> History:
        """Patch normal P: push P ke undo, kosongkan redo."""
        return History(undo=(*self.undo, patch_id), redo=())

    def apply_undo(self, target_patch_id: str) -> History:
        """Undo P: pop P dari undo, push P ke redo."""
        top = self.peek_undo()
        if top != target_patch_id:
            raise HistoryMismatch(
                "Patch undo harus menargetkan puncak stack undo.",
                {"expected": top, "target_patch_id": target_patch_id},
            )
        return History(undo=self.undo[:-1], redo=(*self.redo, top))

    def apply_redo(self, target_patch_id: str, redo_patch_id: str) -> History:
        """Redo P sebagai patch R: pop P dari redo, push R ke undo."""
        top = self.peek_redo()
        if top != target_patch_id:
            raise HistoryMismatch(
                "Patch redo harus menargetkan puncak stack redo.",
                {"expected": top, "target_patch_id": target_patch_id},
            )
        return History(undo=(*self.undo, redo_patch_id), redo=self.redo[:-1])

    def record(self, event: PatchEvent) -> History:
        """Transisi sesuai ``event.kind`` setelah Patch_Event berhasil disimpan."""
        if event.kind == "normal":
            return self.push_normal(event.id)
        if event.target_patch_id is None:  # dijaga juga oleh validator PatchEvent
            raise HistoryMismatch(
                f"Patch {event.kind} tanpa target_patch_id.", {"patch_id": event.id}
            )
        if event.kind == "undo":
            return self.apply_undo(event.target_patch_id)
        return self.apply_redo(event.target_patch_id, event.id)


def replay_history(events: Iterable[PatchEvent], start: History | None = None) -> History:
    """Bangun ulang ``History`` dari urutan Patch_Event (untuk audit/pengujian)."""
    history = start or History()
    for event in events:
        history = history.record(event)
    return history


# ---------------------------------------------------------------------------
# Rencana undo/redo
# ---------------------------------------------------------------------------


def plan_undo(history: History) -> str:
    """Id patch target undo berikutnya; ``NothingToUndo`` bila stack kosong."""
    return history.peek_undo()


def plan_redo(history: History) -> str:
    """Id patch target redo berikutnya; ``NothingToRedo`` bila stack kosong."""
    return history.peek_redo()


def undo_ops(target: PatchEvent) -> tuple[list[Op], list[Op]]:
    """``(ops, inverse_ops)`` untuk patch U yang membatalkan ``target``.

    U menerapkan ``target.inverse_ops``; kebalikannya adalah ``target.ops``.
    """
    return list(target.inverse_ops), list(target.ops)


def redo_ops(target: PatchEvent) -> tuple[list[Op], list[Op]]:
    """``(ops, inverse_ops)`` untuk patch R yang menerapkan ulang ``target``."""
    return list(target.ops), list(target.inverse_ops)


def _check_target(expected_id: str, target: PatchEvent) -> None:
    if target.id != expected_id:
        raise HistoryMismatch(
            "Patch_Event target tidak sama dengan puncak stack.",
            {"expected": expected_id, "patch_id": target.id},
        )


def undo_patch_fields(history: History, target: PatchEvent) -> dict[str, Any]:
    """Field ``kind``/``target_patch_id``/``ops``/``inverse_ops`` untuk patch undo."""
    _check_target(plan_undo(history), target)
    ops, inverse = undo_ops(target)
    return {"kind": "undo", "target_patch_id": target.id, "ops": ops, "inverse_ops": inverse}


def redo_patch_fields(history: History, target: PatchEvent) -> dict[str, Any]:
    """Field ``kind``/``target_patch_id``/``ops``/``inverse_ops`` untuk patch redo."""
    _check_target(plan_redo(history), target)
    ops, inverse = redo_ops(target)
    return {"kind": "redo", "target_patch_id": target.id, "ops": ops, "inverse_ops": inverse}


__all__ = [
    "History",
    "NothingToUndo",
    "NothingToRedo",
    "HistoryMismatch",
    "replay_history",
    "plan_undo",
    "plan_redo",
    "undo_ops",
    "redo_ops",
    "undo_patch_fields",
    "redo_patch_fields",
]
