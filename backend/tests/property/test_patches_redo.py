"""Feature: dashboard-studio-agent, Property 24: Round-trip apply → undo → redo.

Untuk content Dashboard valid dan command valid, menerapkan command (patch
``normal`` P), lalu undo (patch U yang menerapkan ``P.inverse_ops``), lalu redo
(patch R yang menerapkan ulang ``P.ops``) menghasilkan content yang ekuivalen
dengan content setelah command pertama kali diterapkan. Test ini murni
(``core/patches.py`` + ``core/history.py``), tanpa I/O.

**Validates: Requirements 19.2, 19.5**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from studio.core.history import History, plan_redo, plan_undo, redo_patch_fields, undo_patch_fields
from studio.core.models import Command, DashboardContent, PatchEvent
from studio.core.patches import apply_ops, invert_ops, replay, resolve_command
from tests.property.strategies import contents_with_commands, schema_converter, sequential_ids

_T0 = datetime(2025, 1, 1, tzinfo=UTC)


@given(contents_with_commands(), st.integers(0, 10_000))
def test_apply_undo_redo_restores_applied_content(
    case: tuple[DashboardContent, Command], base_version: int
) -> None:
    """Feature: dashboard-studio-agent, Property 24: Round-trip apply → undo → redo.

    **Validates: Requirements 19.2, 19.5**
    """
    content, command = case
    ops = resolve_command(
        content, command, new_id=sequential_ids(), convert_chart_type=schema_converter
    )
    applied = apply_ops(content, ops)
    applied_json = applied.model_dump(mode="json")

    # Patch normal P (v+1).
    patch = PatchEvent(
        id="p_normal",
        dashboard_id="d1",
        version=base_version + 1,
        base_version=base_version,
        source="agent",
        ops=ops,
        inverse_ops=invert_ops(ops),
        created_at=_T0,
    )
    history = History().record(patch)

    # Undo → patch U (v+2) menargetkan P.
    assert plan_undo(history) == patch.id
    u = undo_patch_fields(history, patch)
    undo = PatchEvent(
        id="p_undo",
        dashboard_id="d1",
        version=patch.version + 1,
        base_version=patch.version,
        source="user",
        created_at=_T0 + timedelta(seconds=1),
        **u,
    )
    after_undo = apply_ops(applied, undo.ops)
    assert after_undo == content
    history = history.record(undo)
    assert not history.can_undo and history.redo == (patch.id,)

    # Redo → patch R (v+3) menerapkan ulang P.
    assert plan_redo(history) == patch.id
    r = redo_patch_fields(history, patch)
    redo = PatchEvent(
        id="p_redo",
        dashboard_id="d1",
        version=undo.version + 1,
        base_version=undo.version,
        source="user",
        created_at=_T0 + timedelta(seconds=2),
        **r,
    )
    after_redo = apply_ops(after_undo, redo.ops)
    history = history.record(redo)

    assert after_redo == applied
    assert after_redo.model_dump(mode="json") == applied_json
    assert redo.version == base_version + 3
    assert history.undo == (redo.id,) and not history.can_redo
    # Replay seluruh Patch_Event dari content awal sampai state setelah redo.
    assert replay(content, [patch, undo, redo]) == applied
    # Undo atas R membatalkan efek yang sama dengan undo atas P.
    assert apply_ops(after_redo, redo.inverse_ops) == content
