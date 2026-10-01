"""Feature: dashboard-studio-agent, Property 21: Replay riwayat merekonstruksi state.

Untuk urutan acak Patch_Event sukses (command normal, undo, redo; dari agent
maupun pengguna) pada ``DashboardStore`` sungguhan, ``replay(initial, patch
tersimpan)`` menghasilkan content Dashboard saat ini (juga untuk setiap prefiks
riwayat), dan setiap Patch_Event tersimpan memiliki ``source``, ``created_at``,
serta ``version`` yang berurutan tanpa celah.

Harness (``StoreHarness``/``store_safe``) dipakai ulang dari test Property 20;
``max_examples=50`` karena setiap contoh membuka DB SQLite sementara.

**Validates: Requirements 18.4, 18.7**
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.core.models import DashboardContent, PatchEvent
from studio.core.patches import replay
from tests.property.strategies import valid_commands
from tests.property.test_dashboard_store_versions import SOURCES, StoreHarness, store_safe

ACTIONS = st.lists(st.sampled_from(["apply", "apply", "undo", "redo"]), min_size=1, max_size=12)


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data(), actions=ACTIONS)
def test_replay_of_stored_patches_reconstructs_content(
    data: st.DataObject, actions: list[str]
) -> None:
    """Feature: dashboard-studio-agent, Property 21: Replay riwayat merekonstruksi state.

    **Validates: Requirements 18.4, 18.7**
    """
    h = StoreHarness()
    try:
        store, dash_id = h.store, h.dashboard_id
        start = h.snapshot()
        initial: DashboardContent = start.content
        sources: list[str] = []
        contents: list[DashboardContent] = []

        for action in actions:
            snap = h.snapshot()
            source = data.draw(SOURCES, label="source")
            if action == "apply":
                command = store_safe(data.draw(valid_commands(snap.content), label="command"))
                h.run(store.apply(dash_id, command, snap.version, source))
            elif action == "undo":
                if not snap.can_undo:
                    continue
                h.run(store.undo(dash_id, base_version=snap.version, source=source))
            else:
                if not snap.can_redo:
                    continue
                h.run(store.redo(dash_id, base_version=snap.version, source=source))
            sources.append(source)
            contents.append(h.snapshot().content)

        final = h.snapshot()
        events: list[PatchEvent] = h.run(store.history(dash_id))

        # Replay seluruh riwayat (dan setiap prefiksnya) = state tersimpan.
        assert replay(initial, events) == final.content
        assert len(events) == len(contents)
        for k, expected in enumerate(contents, start=1):
            assert replay(initial, events[:k]) == expected

        # Metadata audit: source sesuai pemanggil, versi berurutan tanpa celah.
        assert [e.source for e in events] == sources
        assert [e.version for e in events] == list(
            range(start.version + 1, start.version + len(events) + 1)
        )
        assert all(e.base_version == e.version - 1 for e in events)
        assert all(e.dashboard_id == dash_id for e in events)
        assert all(e.created_at is not None and e.created_at.tzinfo is not None for e in events)
        stamps = [e.created_at for e in events]
        assert stamps == sorted(stamps)
        assert final.version == start.version + len(events)

        # Resync dari versi awal mengembalikan Patch_Event yang sama.
        assert h.run(store.patches_since(dash_id, start.version)) == events
    finally:
        h.close()
