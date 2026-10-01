"""Feature: dashboard-studio-agent, Property 25: Patch baru mengosongkan redo.

Untuk riwayat acak dengan minimal satu undo yang belum di-redo, menerapkan satu
command ``normal`` baru membuat redo stack kosong sehingga redo berikutnya
mengembalikan ``NOTHING_TO_REDO`` tanpa mengubah state.

Harness (``StoreHarness``/``store_safe``) dipakai ulang dari test Property 20;
``max_examples=50`` karena setiap contoh membuka DB SQLite sementara.

**Validates: Requirements 19.3**
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.core.history import NothingToRedo
from tests.property.strategies import valid_commands
from tests.property.test_dashboard_store_versions import SOURCES, StoreHarness, store_safe


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=st.data(), n_applied=st.integers(1, 5), source=SOURCES)
def test_new_normal_patch_clears_redo(data: st.DataObject, n_applied: int, source: str) -> None:
    """Feature: dashboard-studio-agent, Property 25: Patch baru mengosongkan redo.

    **Validates: Requirements 19.3**
    """
    h = StoreHarness()
    try:
        store, dash_id = h.store, h.dashboard_id

        def apply_valid(src: str, label: str) -> None:
            snap = h.snapshot()
            command = store_safe(data.draw(valid_commands(snap.content), label=label))
            h.run(store.apply(dash_id, command, snap.version, src))

        for _ in range(n_applied):
            apply_valid(data.draw(SOURCES, label="setup_source"), "setup_cmd")
        n_undo = data.draw(st.integers(1, n_applied), label="n_undo")
        for _ in range(n_undo):
            h.run(store.undo(dash_id, base_version=h.snapshot().version))
        # Redo sebagian, sisakan minimal satu undo yang belum di-redo.
        n_redo = data.draw(st.integers(0, n_undo - 1), label="n_redo")
        for _ in range(n_redo):
            h.run(store.redo(dash_id, base_version=h.snapshot().version))
        assert h.snapshot().can_redo

        apply_valid(source, "new_cmd")

        before = h.snapshot()
        assert not before.can_redo
        assert before.can_undo
        history_before = h.run(store.history(dash_id))
        assert history_before[-1].kind == "normal"

        for base in (before.version, None):
            with pytest.raises(NothingToRedo) as exc:
                h.run(store.redo(dash_id, base_version=base, source=source))
            assert exc.value.code == "NOTHING_TO_REDO"
            assert exc.value.http_status == 400

        after = h.snapshot()
        assert after.version == before.version
        assert after.content.model_dump(mode="json") == before.content.model_dump(mode="json")
        assert (after.can_undo, after.can_redo) == (True, False)
        assert h.run(store.history(dash_id)) == history_before
    finally:
        h.close()
