"""Feature: dashboard-studio-agent, Property 22: Base version basi ditolak.

Untuk state Dashboard dengan versi ``v`` (dibangun dari riwayat acak) dan
command/undo/redo dengan ``base_version ≠ v`` (lebih lama maupun lebih baru),
Dashboard_Store menolak dengan ``VersionConflict`` (HTTP 409) yang memuat
``current_version = v``; content, versi, stack undo/redo, dan riwayat Patch_Event
tidak berubah.

Harness (``StoreHarness``/``store_safe``) dipakai ulang dari test Property 20;
``max_examples=50`` karena setiap contoh membuka DB SQLite sementara.

**Validates: Requirements 18.5, 20.3**
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.store.dashboard_store import VersionConflict
from tests.property.strategies import invalid_commands, valid_commands
from tests.property.test_dashboard_store_versions import SOURCES, StoreHarness, store_safe

SETUP = st.lists(st.sampled_from(["apply", "apply", "undo", "redo"]), max_size=8)
ATTEMPTS = st.sampled_from(["apply_valid", "apply_invalid", "undo", "redo"])


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data(), setup=SETUP, attempt=ATTEMPTS, source=SOURCES)
def test_stale_base_version_is_rejected(
    data: st.DataObject, setup: list[str], attempt: str, source: str
) -> None:
    """Feature: dashboard-studio-agent, Property 22: Base version basi ditolak.

    **Validates: Requirements 18.5, 20.3**
    """
    h = StoreHarness()
    try:
        store, dash_id = h.store, h.dashboard_id
        for action in setup:
            snap = h.snapshot()
            if action == "apply":
                command = store_safe(data.draw(valid_commands(snap.content), label="setup_cmd"))
                h.run(store.apply(dash_id, command, snap.version, "agent"))
            elif action == "undo" and snap.can_undo:
                h.run(store.undo(dash_id, base_version=snap.version))
            elif action == "redo" and snap.can_redo:
                h.run(store.redo(dash_id, base_version=snap.version))

        before = h.snapshot()
        v = before.version
        history_before = h.run(store.history(dash_id))
        stale = data.draw(st.integers(-5, v + 5).filter(lambda b: b != v), label="base_version")

        if attempt == "apply_valid":
            command = store_safe(data.draw(valid_commands(before.content), label="command"))
            coro = store.apply(dash_id, command, stale, source)
        elif attempt == "apply_invalid":
            command = data.draw(invalid_commands(before.content), label="invalid_command")
            coro = store.apply(dash_id, command, stale, source)
        elif attempt == "undo":
            coro = store.undo(dash_id, base_version=stale, source=source)
        else:
            coro = store.redo(dash_id, base_version=stale, source=source)

        with pytest.raises(VersionConflict) as exc:
            h.run(coro)
        err = exc.value
        assert err.code == "VERSION_CONFLICT"
        assert err.http_status == 409
        assert err.details["current_version"] == v
        assert err.current_version == v

        after = h.snapshot()
        assert after.version == v
        assert after.content.model_dump(mode="json") == before.content.model_dump(mode="json")
        assert (after.can_undo, after.can_redo) == (before.can_undo, before.can_redo)
        assert h.run(store.history(dash_id)) == history_before
    finally:
        h.close()
