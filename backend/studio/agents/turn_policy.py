"""TurnPolicy: approval gate tool mutasi dan retry counter per invocation.

Modul ini murni (tanpa ADK/FastAPI): semua fungsi bekerja di atas objek state
mirip mapping (``dict`` biasa atau ``google.adk.sessions.State`` dari
``tool_context.state``). Adapter tipis untuk ADK ``ToolContext`` ada di bagian
bawah (:func:`bind_tool_context`) dan hanya memakai atribut duck-typed
(``state``, ``invocation_id``, ``user_content``).

Semua key memakai prefiks ``temp:`` ADK: nilainya hidup di state sesi in-memory
selama satu invocation dan tidak pernah dipersist oleh Session_Service.

Kontrak state (untuk integrasi tool 18.6/18.7 dan runner 18.10)
----------------------------------------------------------------

=====================================  ==========================================
Key                                    Arti
=====================================  ==========================================
``temp:turn_invocation_id``            invocation ADK yang memiliki state giliran
                                       ini (diisi :func:`bind_tool_context`)
``temp:turn_user_message``             teks pesan pengguna giliran ini (sumber
                                       pencocokan ``evidence``)
``temp:approved_proposal_id``          ``proposal_id`` yang SUDAH divalidasi
                                       pending → approved oleh runner
``temp:turn_intent``                   intent terakhir dari ``classify_turn``
``temp:turn_evidence``                 evidence valid terakhir (atau ``None``)
``temp:explicit_change_request``       ``True`` bila ada ``classify_turn`` dengan
                                       permintaan eksplisit + evidence valid
``temp:mutation_allowed``              flag approval gate (Req 21.3, 21.4)
``temp:mutation_reason``               ``"proposal"`` / ``"explicit_request"`` /
                                       ``None``
``temp:retry:<kind>:failures``         jumlah kegagalan beruntun jenis ``kind``
``temp:retry:<kind>:last_error``       error terakhir (dict JSON-able)
=====================================  ==========================================

``kind`` ∈ ``sql`` (maks 3 percobaan ulang, Req 11.3/11.4), ``chart_spec``
(maks 3, Req 13.5), ``version_conflict`` (maks 2, Req 20.4).

Alur integrasi
--------------

Runner (18.10), sebelum ``Runner.run_async``::

    pid = await resolve_approval(repos.proposals, req.approval.proposal_id, session_id)
    delta = begin_turn(user_message=req.message, approved_proposal_id=pid)
    runner.run_async(..., new_message=..., state_delta=delta)

``begin_turn`` mengembalikan SEMUA key di atas dalam keadaan awal sehingga sisa
giliran sebelumnya (bila session in-memory dipakai ulang) selalu tertimpa.

Tool (18.6/18.7)::

    state = bind_tool_context(tool_context)
    # classify_turn
    result = record_turn_classification(state, intent=..., explicit_change_request=..., evidence=...)
    # tool mutasi, SEBELUM menyentuh Dashboard
    if (err := mutation_gate_error(state, "add_chart")) is not None:
        return err
    # retry
    ensure_retry_available(state, "sql")          # raise RetryExhaustedError bila sudah habis
    try: ...
    except StudioError as exc:
        remaining = bump_retry(state, "sql", exc)  # raise RetryExhaustedError pada kegagalan ke-(1+3)
        return {"ok": False, "error": exc.to_dict(), "retries_left": remaining}
    record_success(state, "sql")                   # rantai percobaan selesai → counter direset
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from studio.api.errors import StudioError

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

TEMP_PREFIX = "temp:"

TURN_INVOCATION_KEY = "temp:turn_invocation_id"
USER_MESSAGE_KEY = "temp:turn_user_message"
APPROVED_PROPOSAL_KEY = "temp:approved_proposal_id"
TURN_INTENT_KEY = "temp:turn_intent"
TURN_EVIDENCE_KEY = "temp:turn_evidence"
EXPLICIT_REQUEST_KEY = "temp:explicit_change_request"
MUTATION_ALLOWED_KEY = "temp:mutation_allowed"
MUTATION_REASON_KEY = "temp:mutation_reason"

RETRY_KEY_PREFIX = "temp:retry:"

APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
RETRY_EXHAUSTED = "RETRY_EXHAUSTED"

MIN_EVIDENCE_LENGTH = 3

RetryKind = Literal["sql", "chart_spec", "version_conflict"]

#: Jumlah percobaan ULANG maksimum setelah percobaan pertama (total = 1 + N).
RETRY_LIMITS: dict[str, int] = {
    "sql": 3,  # Req 11.3, 11.4
    "chart_spec": 3,  # Req 13.5
    "version_conflict": 2,  # Req 20.4
}

MutationReason = Literal["proposal", "explicit_request"]

#: Nama tool yang mengubah Dashboard dan wajib melewati approval gate (design: Agents).
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "add_chart",
        "update_chart",
        "remove_chart",
        "update_layout",
        "add_insight",
        "update_insight",
        "undo_last",
        "add_kpi",
        "update_kpi",
        "update_brief",
    }
)

#: State sesi Blueprint aktif (id) dan slot yang sedang dibangun (Req 37.6, 37.7).
BLUEPRINT_ACTIVE_KEY = "blueprint:active_id"
BLUEPRINT_CURRENT_SLOT_KEY = "blueprint:current_slot"


def retry_failures_key(kind: str) -> str:
    return f"{RETRY_KEY_PREFIX}{kind}:failures"


def retry_last_error_key(kind: str) -> str:
    return f"{RETRY_KEY_PREFIX}{kind}:last_error"


def _all_turn_keys() -> list[str]:
    keys = [
        TURN_INVOCATION_KEY,
        USER_MESSAGE_KEY,
        APPROVED_PROPOSAL_KEY,
        TURN_INTENT_KEY,
        TURN_EVIDENCE_KEY,
        EXPLICIT_REQUEST_KEY,
        MUTATION_ALLOWED_KEY,
        MUTATION_REASON_KEY,
    ]
    for kind in RETRY_LIMITS:
        keys.append(retry_failures_key(kind))
        keys.append(retry_last_error_key(kind))
    return keys


#: Semua key state yang dikelola modul ini.
TURN_STATE_KEYS: tuple[str, ...] = tuple(_all_turn_keys())


class StateLike(Protocol):
    """Subset antarmuka yang dipenuhi ``dict`` dan ``google.adk.sessions.State``."""

    def get(self, key: str, default: Any = None) -> Any: ...

    def __getitem__(self, key: str) -> Any: ...

    def __setitem__(self, key: str, value: Any) -> None: ...

    def __contains__(self, key: object) -> bool: ...


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class ApprovalRequiredError(StudioError):
    """Tool mutasi dipanggil tanpa permintaan/persetujuan pengguna di giliran ini (Req 21.4)."""

    def __init__(self, tool_name: str | None = None) -> None:
        details: dict[str, Any] = {}
        if tool_name:
            details["tool"] = tool_name
        super().__init__(
            APPROVAL_REQUIRED,
            "Perubahan Dashboard memerlukan permintaan atau persetujuan eksplisit dari pengguna "
            "pada giliran ini. Panggil classify_turn dengan kutipan pesan pengguna sebagai evidence, "
            "atau tawarkan perubahan melalui propose_changes dan tunggu persetujuan.",
            details,
            http_status=403,
        )


class RetryExhaustedError(StudioError):
    """Batas percobaan ulang untuk suatu jenis kegagalan sudah habis (Req 11.4, 13.5, 20.4)."""

    def __init__(self, kind: str, attempts: int, max_retries: int, last_error: dict[str, Any] | None) -> None:
        super().__init__(
            RETRY_EXHAUSTED,
            f"Batas percobaan ulang '{kind}' habis ({max_retries} kali percobaan ulang). "
            "Hentikan percobaan dan sampaikan error terakhir kepada pengguna.",
            {
                "kind": kind,
                "attempts": attempts,
                "max_retries": max_retries,
                "last_error": last_error,
            },
            http_status=422,
        )
        self.kind = kind
        self.last_error = last_error


# ---------------------------------------------------------------------------
# Siklus giliran
# ---------------------------------------------------------------------------


def initial_turn_state(
    *,
    user_message: str | None = None,
    approved_proposal_id: str | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    """Nilai awal semua key giliran; ``temp:mutation_allowed`` benar hanya via proposal."""
    delta: dict[str, Any] = {key: None for key in TURN_STATE_KEYS}
    delta[TURN_INVOCATION_KEY] = invocation_id
    delta[USER_MESSAGE_KEY] = user_message
    delta[APPROVED_PROPOSAL_KEY] = approved_proposal_id or None
    delta[EXPLICIT_REQUEST_KEY] = False
    approved = bool(approved_proposal_id)
    delta[MUTATION_ALLOWED_KEY] = approved
    delta[MUTATION_REASON_KEY] = "proposal" if approved else None
    for kind in RETRY_LIMITS:
        delta[retry_failures_key(kind)] = 0
    return delta


def _apply(state: StateLike, delta: Mapping[str, Any]) -> None:
    for key, value in delta.items():
        state[key] = value


def begin_turn(
    state: StateLike | None = None,
    *,
    user_message: str | None,
    approved_proposal_id: str | None = None,
    invocation_id: str | None = None,
) -> dict[str, Any]:
    """Reset state giliran dan kembalikan delta-nya (untuk ``run_async(state_delta=...)``).

    ``approved_proposal_id`` HARUS sudah divalidasi pemanggil (proposal pending
    milik sesi ini yang berhasil ditransisikan ke ``approved``, lihat
    :func:`resolve_approval`); nilai ``None`` berarti tidak ada persetujuan.
    Bila ``state`` diberikan, delta juga langsung diterapkan ke state tersebut.
    """
    delta = initial_turn_state(
        user_message=user_message,
        approved_proposal_id=approved_proposal_id,
        invocation_id=invocation_id,
    )
    if state is not None:
        _apply(state, delta)
    return delta


def reset_turn(state: StateLike) -> None:
    """Kembalikan state giliran ke keadaan tertutup (tanpa pesan, tanpa persetujuan)."""
    _apply(state, initial_turn_state())


class ProposalApprover(Protocol):
    async def approve(self, proposal_id: str, *, session_id: str | None = None) -> bool: ...


async def resolve_approval(
    proposals: ProposalApprover, proposal_id: str | None, session_id: str | None
) -> str | None:
    """Validasi ``ChatRequest.approval.proposal_id``: pending milik sesi → approved.

    Mengembalikan ``proposal_id`` bila valid (untuk :func:`begin_turn`), ``None``
    bila kosong, bukan milik sesi, sudah dipakai, atau kedaluwarsa.
    """
    if not proposal_id:
        return None
    ok = await proposals.approve(proposal_id, session_id=session_id)
    return proposal_id if ok else None


# ---------------------------------------------------------------------------
# Approval gate
# ---------------------------------------------------------------------------


def is_valid_evidence(evidence: str | None, user_message: str | None) -> bool:
    """Evidence valid bila ≥ 3 karakter, tidak hanya spasi, dan substring verbatim pesan pengguna."""
    if not evidence or not user_message:
        return False
    if len(evidence) < MIN_EVIDENCE_LENGTH or not evidence.strip():
        return False
    return evidence in user_message


@dataclass(frozen=True, slots=True)
class TurnClassification:
    intent: str
    explicit_change_request: bool
    evidence_valid: bool
    mutation_allowed: bool
    reason: MutationReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "explicit_change_request": self.explicit_change_request,
            "evidence_valid": self.evidence_valid,
            "mutation_allowed": self.mutation_allowed,
            "reason": self.reason,
        }


def record_turn_classification(
    state: StateLike,
    *,
    intent: str,
    explicit_change_request: bool,
    evidence: str | None = None,
    user_message: str | None = None,
) -> TurnClassification:
    """Catat hasil ``classify_turn`` dan perbarui approval gate.

    Flag hanya dapat naik dalam satu giliran (OR atas semua panggilan): proposal
    yang disetujui atau panggilan sebelumnya dengan evidence valid tidak dicabut
    oleh panggilan berikutnya. ``user_message`` default dari ``temp:turn_user_message``;
    bila tidak diketahui, evidence dianggap tidak valid (fail-closed).
    """
    message = user_message if user_message is not None else state.get(USER_MESSAGE_KEY)
    explicit = bool(explicit_change_request)
    valid = explicit and is_valid_evidence(evidence, message)

    state[TURN_INTENT_KEY] = intent
    if valid:
        state[TURN_EVIDENCE_KEY] = evidence
        state[EXPLICIT_REQUEST_KEY] = True

    allowed = is_mutation_allowed(state)
    reason: MutationReason | None = state.get(MUTATION_REASON_KEY) if allowed else None
    if valid and not allowed:
        allowed, reason = True, "explicit_request"
        state[MUTATION_ALLOWED_KEY] = True
        state[MUTATION_REASON_KEY] = reason

    return TurnClassification(
        intent=intent,
        explicit_change_request=explicit,
        evidence_valid=valid,
        mutation_allowed=allowed,
        reason=reason,
    )


def is_mutation_allowed(state: StateLike) -> bool:
    """``True`` hanya bila ``temp:mutation_allowed`` bernilai tepat ``True``."""
    return state.get(MUTATION_ALLOWED_KEY) is True


#: Alias ringkas untuk :func:`is_mutation_allowed`.
allow_mutation = is_mutation_allowed


def require_mutation_allowed(state: StateLike, tool_name: str | None = None) -> None:
    """Raise :class:`ApprovalRequiredError` (``APPROVAL_REQUIRED``) bila gate tertutup."""
    if not is_mutation_allowed(state):
        raise ApprovalRequiredError(tool_name)


def mutation_gate_error(state: StateLike, tool_name: str | None = None) -> dict[str, Any] | None:
    """Versi non-raise: ``{"ok": False, "error": {...}}`` bila ditolak, ``None`` bila diizinkan."""
    if is_mutation_allowed(state):
        return None
    return {"ok": False, "error": ApprovalRequiredError(tool_name).to_dict()}


# ---------------------------------------------------------------------------
# Retry counter
# ---------------------------------------------------------------------------


def _check_kind(kind: str) -> int:
    try:
        return RETRY_LIMITS[kind]
    except KeyError:
        raise ValueError(f"Jenis retry tidak dikenal: {kind!r} (valid: {sorted(RETRY_LIMITS)})") from None


def error_to_dict(error: BaseException | Mapping[str, Any] | str | None) -> dict[str, Any] | None:
    """Normalisasi error menjadi dict JSON-able ``{code, message, details}``."""
    if error is None:
        return None
    if isinstance(error, StudioError):
        return error.to_dict()
    if isinstance(error, Mapping):
        inner = error.get("error") if isinstance(error.get("error"), Mapping) else error
        return {
            "code": str(inner.get("code", "ERROR")),
            "message": str(inner.get("message", "")),
            "details": dict(inner.get("details") or {}),
        }
    if isinstance(error, BaseException):
        return {"code": type(error).__name__, "message": str(error), "details": {}}
    return {"code": "ERROR", "message": str(error), "details": {}}


def retry_failures(state: StateLike, kind: str) -> int:
    _check_kind(kind)
    value = state.get(retry_failures_key(kind))
    return value if isinstance(value, int) and value > 0 else 0


def retries_left(state: StateLike, kind: str) -> int:
    """Sisa percobaan ulang yang masih diizinkan (bisa 0)."""
    limit = _check_kind(kind)
    failures = retry_failures(state, kind)
    # Kegagalan pertama adalah percobaan awal; sisanya memakan jatah ulang.
    return max(0, limit - max(0, failures - 1))


def last_retry_error(state: StateLike, kind: str) -> dict[str, Any] | None:
    _check_kind(kind)
    return state.get(retry_last_error_key(kind))


def is_retry_exhausted(state: StateLike, kind: str) -> bool:
    return retry_failures(state, kind) >= _check_kind(kind) + 1


def _exhausted_error(state: StateLike, kind: str) -> RetryExhaustedError:
    return RetryExhaustedError(
        kind,
        attempts=retry_failures(state, kind),
        max_retries=RETRY_LIMITS[kind],
        last_error=last_retry_error(state, kind),
    )


def ensure_retry_available(state: StateLike, kind: str) -> None:
    """Dipanggil di awal tool: tolak tanpa eksekusi bila jatah percobaan sudah habis."""
    if is_retry_exhausted(state, kind):
        raise _exhausted_error(state, kind)


def bump_retry(
    state: StateLike, kind: str, error: BaseException | Mapping[str, Any] | str | None = None
) -> int:
    """Catat satu kegagalan ``kind`` beserta error-nya; kembalikan sisa percobaan ulang.

    Pada kegagalan ke-(1 + batas) — yaitu setelah batas percobaan ulang terpakai —
    raise :class:`RetryExhaustedError` (``RETRY_EXHAUSTED``) yang menyertakan error
    terakhir, sehingga tool dapat langsung mengembalikannya ke agent.
    """
    _check_kind(kind)
    failures = retry_failures(state, kind) + 1
    state[retry_failures_key(kind)] = failures
    state[retry_last_error_key(kind)] = error_to_dict(error)
    if is_retry_exhausted(state, kind):
        raise _exhausted_error(state, kind)
    return retries_left(state, kind)


def record_success(state: StateLike, kind: str) -> None:
    """Percobaan berhasil: rantai retry ``kind`` selesai, counter dan error terakhir direset."""
    _check_kind(kind)
    state[retry_failures_key(kind)] = 0
    state[retry_last_error_key(kind)] = None


def reset_retries(state: StateLike, kinds: Iterable[str] | None = None) -> None:
    for kind in kinds if kinds is not None else RETRY_LIMITS:
        record_success(state, kind)


# ---------------------------------------------------------------------------
# Adapter ADK ToolContext (duck-typed; tanpa import ADK)
# ---------------------------------------------------------------------------


def _content_text(content: Any) -> str | None:
    parts = getattr(content, "parts", None)
    if not parts:
        return None
    texts = [p.text for p in parts if isinstance(getattr(p, "text", None), str)]
    return "".join(texts) if texts else None


def bind_tool_context(tool_context: Any) -> StateLike:
    """Siapkan dan kembalikan ``tool_context.state`` untuk dipakai fungsi di atas.

    * Bila state giliran milik invocation lain (sisa giliran sebelumnya), state
      direset ke keadaan tertutup (fail-closed).
    * Bila ``temp:turn_invocation_id`` kosong, diklaim oleh invocation saat ini
      (nilai dari ``begin_turn`` via ``state_delta`` dipertahankan).
    * Bila pesan pengguna belum ada, diisi dari ``tool_context.user_content``.
    """
    state: StateLike = tool_context.state
    invocation_id = getattr(tool_context, "invocation_id", None)
    if invocation_id:
        owner = state.get(TURN_INVOCATION_KEY)
        if owner and owner != invocation_id:
            reset_turn(state)
            owner = None
        if not owner:
            state[TURN_INVOCATION_KEY] = invocation_id
    if state.get(USER_MESSAGE_KEY) is None:
        text = _content_text(getattr(tool_context, "user_content", None))
        if text is not None:
            state[USER_MESSAGE_KEY] = text
    return state


__all__ = [
    "APPROVAL_REQUIRED",
    "APPROVED_PROPOSAL_KEY",
    "EXPLICIT_REQUEST_KEY",
    "MIN_EVIDENCE_LENGTH",
    "MUTATING_TOOLS",
    "MUTATION_ALLOWED_KEY",
    "MUTATION_REASON_KEY",
    "RETRY_EXHAUSTED",
    "RETRY_KEY_PREFIX",
    "RETRY_LIMITS",
    "TEMP_PREFIX",
    "TURN_EVIDENCE_KEY",
    "TURN_INTENT_KEY",
    "TURN_INVOCATION_KEY",
    "TURN_STATE_KEYS",
    "USER_MESSAGE_KEY",
    "ApprovalRequiredError",
    "ProposalApprover",
    "RetryExhaustedError",
    "RetryKind",
    "StateLike",
    "TurnClassification",
    "allow_mutation",
    "begin_turn",
    "bind_tool_context",
    "bump_retry",
    "ensure_retry_available",
    "error_to_dict",
    "initial_turn_state",
    "is_mutation_allowed",
    "is_retry_exhausted",
    "is_valid_evidence",
    "last_retry_error",
    "mutation_gate_error",
    "record_success",
    "record_turn_classification",
    "require_mutation_allowed",
    "reset_retries",
    "reset_turn",
    "resolve_approval",
    "retries_left",
    "retry_failures",
    "retry_failures_key",
    "retry_last_error_key",
]
