"""Feature: dashboard-studio-agent, Property 23: Round-trip apply → undo.

Untuk content Dashboard valid dan command valid, menerapkan ops hasil
``resolve_command`` lalu ops kebalikannya (``invert_ops``) mengembalikan content
yang ekuivalen dengan content sebelum command; sebagai Patch_Event (normal +
undo) versi bertambah tepat 2 dan replay keduanya menghasilkan content awal.

**Validates: Requirements 19.1, 19.4**
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.models import Command, DashboardContent, PatchEvent
from studio.core.patches import (
    InvalidOp,
    ItemNotFound,
    apply_ops,
    invert_ops,
    replay,
    resolve_command,
)
from tests.property.strategies import (
    COMMAND_TYPES,
    available_command_types,
    contents_with_commands,
    dashboard_contents,
    invalid_commands,
    schema_converter,
    sequential_ids,
    valid_commands,
)

_NOW = datetime(2025, 1, 1, tzinfo=UTC)


@given(contents_with_commands(), st.integers(0, 10_000))
def test_apply_then_undo_restores_content(
    case: tuple[DashboardContent, Command], base_version: int
) -> None:
    """Feature: dashboard-studio-agent, Property 23: Round-trip apply → undo.

    **Validates: Requirements 19.1, 19.4**
    """
    content, command = case
    snapshot = content.model_dump(mode="json")

    ops = resolve_command(
        content, command, new_id=sequential_ids(), convert_chart_type=schema_converter
    )
    applied = apply_ops(content, ops)
    inverse = invert_ops(ops)
    restored = apply_ops(applied, inverse)

    assert content.model_dump(mode="json") == snapshot  # apply murni
    assert restored == content
    assert restored.model_dump(mode="json") == snapshot

    # Sebagai Patch_Event: normal (v+1) lalu undo (v+2); replay kembali ke awal.
    patch = PatchEvent(
        id="p_apply",
        dashboard_id="d1",
        version=base_version + 1,
        base_version=base_version,
        source="user",
        ops=ops,
        inverse_ops=inverse,
        created_at=_NOW,
    )
    undo = PatchEvent(
        id="p_undo",
        dashboard_id="d1",
        version=patch.version + 1,
        base_version=patch.version,
        source="user",
        kind="undo",
        target_patch_id=patch.id,
        ops=inverse,
        inverse_ops=ops,
        created_at=_NOW,
    )
    assert undo.version == base_version + 2
    assert replay(content, [patch]) == applied
    assert replay(content, [patch, undo]) == content


@pytest.mark.parametrize("command_type", COMMAND_TYPES)
def test_every_command_type_is_generated(command_type: str) -> None:
    """Sanity: generator mencakup ke-9 jenis command (dipakai ulang Property 20–25).

    Dicek per jenis secara deterministik: sampling acak campuran tidak menjamin
    tiap jenis muncul (distribusi Hypothesis condong ke pilihan "sederhana").
    """
    strategy = (
        dashboard_contents(min_items=3)
        .filter(lambda c: command_type in available_command_types(c))
        .flatmap(lambda c: valid_commands(c, command_types=[command_type]))
    )

    @settings(max_examples=5, deadline=None)
    @given(strategy)
    def check(command: Command) -> None:
        assert command.type == command_type

    check()


@given(dashboard_contents().flatmap(lambda c: st.tuples(st.just(c), invalid_commands(c))))
def test_invalid_commands_are_rejected(case: tuple[DashboardContent, Command]) -> None:
    """Sanity generator ``invalid_commands``: resolve menolak tanpa mengubah content."""
    content, command = case
    snapshot = content.model_dump(mode="json")
    with pytest.raises((ItemNotFound, InvalidOp)):
        resolve_command(
            content, command, new_id=sequential_ids(), convert_chart_type=schema_converter
        )
    assert content.model_dump(mode="json") == snapshot
