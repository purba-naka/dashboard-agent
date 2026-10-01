"""Unit test retry counter TurnPolicy (Req 11.3, 11.4, 13.5, 20.4).

Batas percobaan ULANG setelah percobaan pertama: ``sql`` 3, ``chart_spec`` 3,
``version_conflict`` 2. Kegagalan ke-(1 + batas) menghasilkan
``RETRY_EXHAUSTED`` yang menyertakan error terakhir.
"""

from __future__ import annotations

from typing import Any

import pytest

from studio.agents.turn_policy import (
    RETRY_EXHAUSTED,
    RETRY_LIMITS,
    RetryExhaustedError,
    begin_turn,
    bump_retry,
    ensure_retry_available,
    last_retry_error,
    record_success,
    retries_left,
    retry_failures,
)
from studio.api.errors import StudioError

EXPECTED_LIMITS = {"sql": 3, "chart_spec": 3, "version_conflict": 2}


def _new_state() -> dict[str, Any]:
    state: dict[str, Any] = {}
    begin_turn(state, user_message="buat chart penjualan")
    return state


def _error(kind: str, n: int) -> StudioError:
    return StudioError(f"{kind.upper()}_FAILED", f"{kind} gagal #{n}", {"attempt": n})


def test_retry_limits_match_requirements() -> None:
    assert RETRY_LIMITS == EXPECTED_LIMITS


@pytest.mark.parametrize(("kind", "limit"), sorted(EXPECTED_LIMITS.items()))
def test_rejected_after_limit_retries_with_last_error(kind: str, limit: int) -> None:
    state = _new_state()
    assert retries_left(state, kind) == limit

    # Percobaan pertama + (limit - 1) percobaan ulang gagal: masih boleh mencoba lagi.
    for n in range(1, limit + 1):
        ensure_retry_available(state, kind)
        remaining = bump_retry(state, kind, _error(kind, n))
        assert remaining == limit - (n - 1)
        assert retry_failures(state, kind) == n

    # Percobaan ulang ke-`limit` (percobaan total ke-(1 + limit)) gagal → habis.
    ensure_retry_available(state, kind)
    final = _error(kind, limit + 1)
    with pytest.raises(RetryExhaustedError) as exc:
        bump_retry(state, kind, final)

    err = exc.value
    assert err.code == RETRY_EXHAUSTED
    assert err.kind == kind
    assert err.details["kind"] == kind
    assert err.details["attempts"] == limit + 1
    assert err.details["max_retries"] == limit
    assert err.last_error == final.to_dict()
    assert err.details["last_error"] == {
        "code": f"{kind.upper()}_FAILED",
        "message": f"{kind} gagal #{limit + 1}",
        "details": {"attempt": limit + 1},
    }
    assert retries_left(state, kind) == 0

    # Setelah habis, percobaan berikutnya ditolak tanpa eksekusi, tetap membawa error terakhir.
    with pytest.raises(RetryExhaustedError) as again:
        ensure_retry_available(state, kind)
    assert again.value.details["last_error"] == final.to_dict()


def test_counters_are_independent_per_kind() -> None:
    state = _new_state()
    for n in range(1, 4):
        bump_retry(state, "sql", _error("sql", n))

    assert retries_left(state, "sql") == 1
    assert retries_left(state, "chart_spec") == 3
    assert retries_left(state, "version_conflict") == 2
    ensure_retry_available(state, "chart_spec")
    ensure_retry_available(state, "version_conflict")


def test_success_resets_chain() -> None:
    state = _new_state()
    bump_retry(state, "version_conflict", _error("version_conflict", 1))
    bump_retry(state, "version_conflict", _error("version_conflict", 2))

    record_success(state, "version_conflict")

    assert retry_failures(state, "version_conflict") == 0
    assert last_retry_error(state, "version_conflict") is None
    assert retries_left(state, "version_conflict") == 2


def test_new_turn_resets_counters() -> None:
    state = _new_state()
    for n in range(1, 4):
        bump_retry(state, "chart_spec", _error("chart_spec", n))
    with pytest.raises(RetryExhaustedError):
        bump_retry(state, "chart_spec", _error("chart_spec", 4))

    begin_turn(state, user_message="coba lagi")

    ensure_retry_available(state, "chart_spec")
    assert retries_left(state, "chart_spec") == 3
    assert last_retry_error(state, "chart_spec") is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": "SQL_NOT_READ_ONLY", "message": "hanya SELECT"}, {"code": "SQL_NOT_READ_ONLY", "message": "hanya SELECT", "details": {}}),
        (
            {"ok": False, "error": {"code": "VERSION_CONFLICT", "message": "basi", "details": {"current_version": 4}}},
            {"code": "VERSION_CONFLICT", "message": "basi", "details": {"current_version": 4}},
        ),
        (ValueError("boom"), {"code": "ValueError", "message": "boom", "details": {}}),
        ("teks error", {"code": "ERROR", "message": "teks error", "details": {}}),
    ],
)
def test_last_error_is_normalized(error: Any, expected: dict[str, Any]) -> None:
    state = _new_state()
    bump_retry(state, "sql", error)
    assert last_retry_error(state, "sql") == expected


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        bump_retry(_new_state(), "network")
