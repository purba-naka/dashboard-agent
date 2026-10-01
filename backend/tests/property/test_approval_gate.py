"""Feature: dashboard-studio-agent, Property 26: Approval gate untuk tool mutasi.

Untuk pesan pengguna acak, state giliran acak (termasuk sisa state giliran
sebelumnya milik invocation lain), proposal acak (pending milik sesi, milik sesi
lain, sudah dipakai, kedaluwarsa, tidak dikenal, atau tidak ada), serta rangkaian
panggilan ``classify_turn`` acak (``explicit_change_request`` dan ``evidence``
acak: substring pesan, teks acak, spasi, ``None``), ``temp:mutation_allowed``
bernilai benar jika dan hanya jika:

* giliran membawa ``proposal_id`` pending yang valid milik sesi ini, atau
* ada ``classify_turn(explicit_change_request=True, evidence)`` dengan evidence
  ≥ 3 karakter, tidak hanya spasi, dan substring verbatim pesan pengguna giliran ini.

Tool mutasi disimulasikan sesuai kontrak ``turn_policy`` (cek gate lebih dulu,
lalu ``DashboardStore.apply``) di atas ``DashboardStore`` + ``ProposalRepo``
SQLite sungguhan: saat ditolak, hasilnya ``APPROVAL_REQUIRED`` dan versi,
content, serta riwayat Patch_Event Dashboard tidak berubah.

Satu harness SQLite dipakai untuk seluruh contoh (fixture ``module``) agar
100 contoh tetap cepat; setiap contoh memakai sesi chat dan proposal baru.

**Validates: Requirements 21.3, 21.4**
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.agents.turn_policy import (
    APPROVAL_REQUIRED,
    MUTATING_TOOLS,
    MUTATION_ALLOWED_KEY,
    ApprovalRequiredError,
    begin_turn,
    bind_tool_context,
    is_mutation_allowed,
    mutation_gate_error,
    record_turn_classification,
    require_mutation_allowed,
    resolve_approval,
)
from studio.core.models import SetTitleCommand
from tests.property.test_dashboard_store_versions import StoreHarness

PREV_INVOCATION = "inv-prev"
CUR_INVOCATION = "inv-cur"

PROPOSAL_KINDS = st.sampled_from(
    ["none", "pending_own", "pending_other", "already_used", "expired", "unknown"]
)
MESSAGES = st.text(max_size=60)
TOOLS = st.sampled_from(sorted(MUTATING_TOOLS))


@dataclass
class GateHarness:
    store_h: StoreHarness
    workspace_id: str
    counter: Iterator[int]

    @property
    def repos(self) -> Any:
        return self.store_h.store.repos

    def new_session(self) -> str:
        n = next(self.counter)
        return self.store_h.run(self.repos.chat_sessions.create(self.workspace_id, f"Sesi {n}")).id

    def make_proposal(self, kind: str, own_session: str) -> str | None:
        proposals = self.repos.proposals
        run = self.store_h.run
        if kind == "none":
            return None
        if kind == "unknown":
            return f"prop_missing_{next(self.counter)}"
        if kind == "pending_other":
            return run(proposals.create(self.new_session(), "usulan sesi lain")).id
        pid = run(proposals.create(own_session, "usulan")).id
        if kind == "already_used":
            assert run(proposals.approve(pid, session_id=own_session))
        elif kind == "expired":
            assert run(proposals.expire_pending(own_session)) == 1
        return pid


@pytest.fixture(scope="module")
def gate() -> Iterator[GateHarness]:
    h = StoreHarness()
    try:
        ws = h.run(h.store.repos.workspaces.create("Approval gate"))
        yield GateHarness(h, ws.id, itertools.count())
    finally:
        h.close()


@st.composite
def evidence_for(draw: st.DrawFn, message: str) -> str | None:
    """Evidence: substring pesan (termasuk pendek/kosong), teks acak, spasi, atau None."""
    choice = draw(st.sampled_from(["substring", "substring", "random", "blank", "none", "whole"]))
    if choice == "substring":
        i = draw(st.integers(0, len(message)))
        j = draw(st.integers(i, len(message)))
        return message[i:j]
    if choice == "random":
        return draw(st.text(max_size=8))
    if choice == "blank":
        return draw(st.sampled_from(["", " ", "   ", "\t \n", "    "]))
    if choice == "whole":
        return message
    return None


def _valid_evidence_oracle(explicit: bool, evidence: str | None, message: str) -> bool:
    return (
        explicit
        and evidence is not None
        and len(evidence) >= 3
        and evidence.strip() != ""
        and evidence in message
    )


def _gated_mutation(gate: GateHarness, state: Any, tool: str, title: str) -> dict[str, Any]:
    """Tool mutasi sesuai kontrak ``turn_policy``: gate dicek SEBELUM Dashboard disentuh."""
    err = mutation_gate_error(state, tool)
    if err is not None:
        return err
    h = gate.store_h
    snap = h.snapshot()
    h.run(h.store.apply(h.dashboard_id, SetTitleCommand(title=title), snap.version, "agent"))
    return {"ok": True}


@settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    data=st.data(),
    message=MESSAGES,
    proposal_kind=PROPOSAL_KINDS,
    runner_began_turn=st.booleans(),
    leftover_allowed=st.booleans(),
    tool=TOOLS,
)
def test_mutation_allowed_iff_valid_proposal_or_explicit_evidence(
    gate: GateHarness,
    data: st.DataObject,
    message: str,
    proposal_kind: str,
    runner_began_turn: bool,
    leftover_allowed: bool,
    tool: str,
) -> None:
    """Feature: dashboard-studio-agent, Property 26: Approval gate untuk tool mutasi.

    **Validates: Requirements 21.3, 21.4**
    """
    h = gate.store_h

    # Sisa state giliran sebelumnya (invocation lain), mungkin dengan gate terbuka.
    state: dict[str, Any] = {}
    begin_turn(
        state,
        user_message="pesan giliran lalu: tambahkan chart",
        approved_proposal_id="prop_old" if leftover_allowed else None,
        invocation_id=PREV_INVOCATION,
    )
    assert is_mutation_allowed(state) is leftover_allowed

    # Validasi proposal oleh runner (pending milik sesi → approved).
    session_id = gate.new_session()
    proposal_id = gate.make_proposal(proposal_kind, session_id)
    approved = h.run(resolve_approval(h.store.repos.proposals, proposal_id, session_id))
    assert (approved is not None) == (proposal_kind == "pending_own")

    if runner_began_turn:
        delta = begin_turn(
            user_message=message, approved_proposal_id=approved, invocation_id=CUR_INVOCATION
        )
        state.update(delta)  # setara `run_async(state_delta=delta)`
        proposal_ok = approved is not None
    else:
        # Tanpa begin_turn, persetujuan tidak pernah sampai ke state (fail-closed).
        proposal_ok = False

    ctx = SimpleNamespace(
        state=state,
        invocation_id=CUR_INVOCATION,
        user_content=SimpleNamespace(parts=[SimpleNamespace(text=message)]),
    )
    state = bind_tool_context(ctx)
    # Sisa giliran lalu tidak pernah membuka gate giliran ini.
    assert is_mutation_allowed(state) is proposal_ok

    calls = data.draw(
        st.lists(st.tuples(st.booleans(), evidence_for(message)), max_size=3), label="classify_calls"
    )
    explicit_ok = False
    for explicit, evidence in calls:
        result = record_turn_classification(
            state, intent="edit_dashboard", explicit_change_request=explicit, evidence=evidence
        )
        valid = _valid_evidence_oracle(explicit, evidence, message)
        explicit_ok = explicit_ok or valid
        assert result.evidence_valid is valid
        assert result.mutation_allowed is (proposal_ok or explicit_ok)

    expected = proposal_ok or explicit_ok
    assert is_mutation_allowed(state) is expected
    assert (state.get(MUTATION_ALLOWED_KEY) is True) is expected

    before = h.snapshot()
    history_before = h.run(h.store.history(h.dashboard_id))
    title = f"Judul {next(gate.counter)}"

    result = _gated_mutation(gate, state, tool, title)
    after = h.snapshot()

    if expected:
        assert result == {"ok": True}
        require_mutation_allowed(state, tool)  # tidak raise
        assert after.version == before.version + 1
        assert after.content.title == title
    else:
        assert result["ok"] is False
        assert result["error"]["code"] == APPROVAL_REQUIRED
        assert result["error"]["details"] == {"tool": tool}
        with pytest.raises(ApprovalRequiredError) as exc:
            require_mutation_allowed(state, tool)
        assert exc.value.code == APPROVAL_REQUIRED
        assert exc.value.http_status == 403
        # Dashboard tidak berubah.
        assert after.version == before.version
        assert after.content.model_dump(mode="json") == before.content.model_dump(mode="json")
        assert h.run(h.store.history(h.dashboard_id)) == history_before
