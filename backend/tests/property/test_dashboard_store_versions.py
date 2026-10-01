"""Feature: dashboard-studio-agent, Property 20: Versi Dashboard bertambah tepat satu per patch sukses.

State machine (``hypothesis.stateful.RuleBasedStateMachine``) atas
``DashboardStore`` sungguhan (SQLite sementara per instance mesin): command
valid, command tidak valid, command dengan ``base_version`` basi, undo, dan redo
diacak. Invariant: versi akhir = versi awal + jumlah Patch_Event sukses, setiap
Patch_Event tersimpan ``version = base_version + 1``, dan command yang ditolak
tidak mengubah content maupun versi.

Catatan harness:

- ``RuleBasedStateMachine`` sinkron, jadi store dijalankan pada event loop
  khusus (``asyncio.new_event_loop()``) milik setiap instance mesin; DB SQLite
  dibuat di ``tempfile.mkdtemp`` dan dihapus di ``teardown``.
- Query ``QUERY_SCHEMAS`` disimpan ke ``QueryRepo`` dengan 2 tabel + 1 relasi
  sehingga semua ``insight_type`` (termasuk ``cross_dataset_correlation``) sah.
- Teks insight dibersihkan dari digit agar verifikasi angka insight (Req 14.3)
  tidak menolak command yang dimaksudkan valid; validasi produksi tidak dilemahkan.
- Contoh dibatasi (``max_examples=50``, ``stateful_step_count=15``) karena setiap
  contoh membuka DB dan menjalankan migrasi.

**Validates: Requirements 18.2, 18.8**
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import tempfile
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from studio.core.history import NothingToRedo, NothingToUndo
from studio.core.models import AddInsightCommand, Command, UpdateInsightCommand
from studio.core.patches import InvalidOp, ItemNotFound
from studio.events.bus import EventBus
from studio.store.dashboard_store import DashboardSnapshot, DashboardStore, VersionConflict
from studio.store.db import Database
from studio.store.repos import Repositories
from tests.property.strategies import (
    QUERY_SCHEMAS,
    invalid_commands,
    sequential_ids,
    valid_commands,
)

_T0 = datetime(2025, 1, 1, tzinfo=UTC)
SOURCES = st.sampled_from(["user", "agent"])


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _no_digits(text: str) -> str:
    return "".join(ch for ch in text if not ch.isdigit() and unicodedata.category(ch) != "Nd")


def store_safe(command: Command) -> Command:
    """Hapus digit dari teks insight (lihat docstring modul)."""
    if isinstance(command, AddInsightCommand):
        insight = command.insight.model_copy(update={"text": _no_digits(command.insight.text)})
        return command.model_copy(update={"insight": insight})
    if isinstance(command, UpdateInsightCommand) and command.changes.text is not None:
        changes = command.changes.model_copy(update={"text": _no_digits(command.changes.text)})
        return command.model_copy(update={"changes": changes})
    return command


class StoreHarness:
    """DashboardStore di atas SQLite sementara + event loop khusus."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.tmpdir = tempfile.mkdtemp(prefix="studio_p20_")
        self.db: Database | None = None
        try:
            self.db = self.run(Database(Path(self.tmpdir) / "studio.db").open())
            repos = Repositories(self.db)
            ticks = itertools.count()
            self.store = DashboardStore(
                repos,
                EventBus(),
                id_factory=sequential_ids("id_"),
                clock=lambda: _T0 + timedelta(milliseconds=next(ticks)),
            )
            ws = self.run(repos.workspaces.create("Studio"))
            for qid, schema in QUERY_SCHEMAS.items():
                self.run(
                    repos.queries.create(
                        ws.id,
                        sql="SELECT 1",
                        output_schema=schema,
                        rows=[],
                        tables_used=("t_a", "t_b"),
                        relations_used=("rel_ab",),
                        query_id=qid,
                    )
                )
            self.dashboard_id = self.run(self.store.create_dashboard(ws.id, "Dashboard")).id
        except BaseException:
            self.close()
            raise

    def run(self, coro: Any) -> Any:
        return self.loop.run_until_complete(coro)

    def snapshot(self) -> DashboardSnapshot:
        return self.run(self.store.get(self.dashboard_id))

    def close(self) -> None:
        try:
            if self.db is not None:
                self.run(self.db.close())
        finally:
            self.loop.close()
            shutil.rmtree(self.tmpdir, ignore_errors=True)


def _state(snap: DashboardSnapshot) -> tuple[int, Any, bool, bool]:
    return snap.version, snap.content.model_dump(mode="json"), snap.can_undo, snap.can_redo


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class DashboardVersionMachine(RuleBasedStateMachine):
    """Feature: dashboard-studio-agent, Property 20: Versi Dashboard bertambah tepat satu per patch sukses.

    **Validates: Requirements 18.2, 18.8**
    """

    def __init__(self) -> None:
        super().__init__()
        self.h = StoreHarness()
        self.initial_version = self.h.snapshot().version
        self.successes = 0
        # Model stack undo/redo (kedalaman saja) untuk memprediksi NothingToUndo/Redo.
        self.undo_depth = 0
        self.redo_depth = 0

    def teardown(self) -> None:
        self.h.close()

    @property
    def expected_version(self) -> int:
        return self.initial_version + self.successes

    def _assert_unchanged(self, before: DashboardSnapshot) -> None:
        assert _state(self.h.snapshot()) == _state(before)

    def _record_success(self, event: Any, before: DashboardSnapshot, kind: str) -> None:
        assert event.kind == kind
        assert event.base_version == before.version == self.expected_version
        assert event.version == before.version + 1
        self.successes += 1
        assert self.h.snapshot().version == event.version == self.expected_version

    # -- rules -------------------------------------------------------------

    @rule(data=st.data(), source=SOURCES)
    def apply_valid(self, data: st.DataObject, source: str) -> None:
        before = self.h.snapshot()
        command = store_safe(data.draw(valid_commands(before.content), label="command"))
        event = self.h.run(
            self.h.store.apply(self.h.dashboard_id, command, before.version, source)
        )
        self._record_success(event, before, "normal")
        self.undo_depth += 1
        self.redo_depth = 0

    @rule(data=st.data(), source=SOURCES)
    def apply_invalid(self, data: st.DataObject, source: str) -> None:
        before = self.h.snapshot()
        command = data.draw(invalid_commands(before.content), label="invalid_command")
        with pytest.raises((ItemNotFound, InvalidOp)):
            self.h.run(self.h.store.apply(self.h.dashboard_id, command, before.version, source))
        self._assert_unchanged(before)

    @rule(
        data=st.data(),
        source=SOURCES,
        action=st.sampled_from(["apply_valid", "apply_invalid", "undo", "redo"]),
    )
    def stale_base_version(self, data: st.DataObject, source: str, action: str) -> None:
        before = self.h.snapshot()
        stale = data.draw(
            st.integers(-3, before.version + 5).filter(lambda b: b != before.version),
            label="stale_base_version",
        )
        store, dash_id = self.h.store, self.h.dashboard_id
        if action == "apply_valid":
            command = store_safe(data.draw(valid_commands(before.content), label="command"))
            coro = store.apply(dash_id, command, stale, source)
        elif action == "apply_invalid":
            command = data.draw(invalid_commands(before.content), label="invalid_command")
            coro = store.apply(dash_id, command, stale, source)
        elif action == "undo":
            coro = store.undo(dash_id, base_version=stale, source=source)
        else:
            coro = store.redo(dash_id, base_version=stale, source=source)
        with pytest.raises(VersionConflict) as exc:
            self.h.run(coro)
        assert exc.value.details["current_version"] == before.version
        self._assert_unchanged(before)

    @rule(source=SOURCES, pass_version=st.booleans())
    def undo(self, source: str, pass_version: bool) -> None:
        before = self.h.snapshot()
        base = before.version if pass_version else None
        coro = self.h.store.undo(self.h.dashboard_id, base_version=base, source=source)
        if self.undo_depth == 0:
            with pytest.raises(NothingToUndo):
                self.h.run(coro)
            self._assert_unchanged(before)
            return
        self._record_success(self.h.run(coro), before, "undo")
        self.undo_depth -= 1
        self.redo_depth += 1

    @rule(source=SOURCES, pass_version=st.booleans())
    def redo(self, source: str, pass_version: bool) -> None:
        before = self.h.snapshot()
        base = before.version if pass_version else None
        coro = self.h.store.redo(self.h.dashboard_id, base_version=base, source=source)
        if self.redo_depth == 0:
            with pytest.raises(NothingToRedo):
                self.h.run(coro)
            self._assert_unchanged(before)
            return
        self._record_success(self.h.run(coro), before, "redo")
        self.redo_depth -= 1
        self.undo_depth += 1

    # -- invariants --------------------------------------------------------

    @invariant()
    def version_equals_initial_plus_successes(self) -> None:
        snap = self.h.snapshot()
        assert snap.version == self.expected_version
        assert snap.can_undo == (self.undo_depth > 0)
        assert snap.can_redo == (self.redo_depth > 0)

        events = self.h.run(self.h.store.history(self.h.dashboard_id))
        assert len(events) == self.successes
        assert [e.version for e in events] == list(
            range(self.initial_version + 1, self.expected_version + 1)
        )
        assert all(e.version == e.base_version + 1 for e in events)


DashboardVersionMachine.TestCase.settings = settings(
    max_examples=50,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
TestDashboardVersions = DashboardVersionMachine.TestCase
