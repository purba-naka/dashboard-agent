"""Privacy_Guard untuk Agent_Tools: bentuk return tool & batas baris ke LLM.

Dua lapis penegakan (design: Privacy_Guard, Req 27.1–27.6):

1. :func:`llm_output` — dekorator setiap tool yang dipakai LLM:

   * exception tidak pernah bocor ke LLM: ``StudioError`` (termasuk
     ``RetryExhaustedError``/``ApprovalRequiredError``) → ``{"ok": False,
     "error": {code, message, details}}``; exception lain → ``TOOL_ERROR``;
   * return wajib ``dict`` dengan ``ok: bool``;
   * setiap list di bawah key baris (``rows``/``records``) ≤ 200 elemen
     (keluaran ``truncate_result``) dan ``sample_rows`` ≤ batas Sample_Rows;
   * setiap dict yang memuat ``column_profiles``/``sample_rows`` (konteks
     Dataset) hanya boleh berisi key keluaran ``build_dataset_context``;
   * payload dinormalisasi ``json_safe``.

   Pelanggaran → ``{"ok": False, "error": {"code": "PRIVACY_GUARD_VIOLATION"}}``
   (payload asli dibuang, dicatat di log).

2. :func:`after_tool_callback` — ``LlmAgent(after_tool_callback=...)`` ADK
   (signature ``(tool, args, tool_context, tool_response)``, dipanggil dengan
   keyword): jaring pengaman untuk SEMUA tool (termasuk yang tidak memakai
   dekorator). Hasil yang memuat > 200 baris ditolak dengan mengganti respons
   menjadi error ``RESULT_TOO_LARGE``; selain itu mengembalikan ``None``
   (respons asli dipakai).

Helper :func:`error_result` membungkus error menjadi dict tool beserta akuntansi
retry TurnPolicy (``sql``/``chart_spec``/``version_conflict``).
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable, Iterator, Mapping
from typing import Any, TypeVar, overload

from studio.agents.turn_policy import (
    RetryExhaustedError,
    StateLike,
    bump_retry,
    ensure_retry_available,
    error_to_dict,
)
from studio.api.errors import StudioError
from studio.core.privacy import DEFAULT_SAMPLE_ROWS, MAX_RESULT_ROWS, json_safe

log = logging.getLogger(__name__)

PRIVACY_GUARD_VIOLATION = "PRIVACY_GUARD_VIOLATION"
RESULT_TOO_LARGE = "RESULT_TOO_LARGE"
TOOL_ERROR = "TOOL_ERROR"

#: Key yang nilainya berupa baris data hasil query (keluaran ``truncate_result``).
ROW_KEYS: frozenset[str] = frozenset({"rows", "records"})
SAMPLE_KEY = "sample_rows"
#: Key yang boleh ada pada konteks Dataset (keluaran ``build_dataset_context``).
DATASET_CONTEXT_KEYS: frozenset[str] = frozenset(
    {"table_name", "schema", "column_profiles", SAMPLE_KEY}
)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Hasil error
# ---------------------------------------------------------------------------


def error_result(
    error: BaseException | Mapping[str, Any] | str,
    *,
    state: StateLike | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Error → ``{"ok": False, "error": {...}}`` (+ ``retries_left`` bila ``kind`` diberikan).

    Dengan ``state`` dan ``kind``, kegagalan dicatat di retry counter TurnPolicy;
    bila batas habis dikembalikan ``RETRY_EXHAUSTED`` (beserta error terakhir)
    dengan ``retries_left = 0``.
    """
    if isinstance(error, RetryExhaustedError):
        return {"ok": False, "error": error.to_dict(), "retries_left": 0}
    err = error_to_dict(error) or {"code": TOOL_ERROR, "message": "", "details": {}}
    if state is None or kind is None:
        return {"ok": False, "error": err}
    try:
        remaining = bump_retry(state, kind, err)
    except RetryExhaustedError as exhausted:
        return {"ok": False, "error": exhausted.to_dict(), "retries_left": 0}
    return {"ok": False, "error": err, "retries_left": remaining}


def exhausted_result(state: StateLike, kind: str) -> dict[str, Any] | None:
    """``RETRY_EXHAUSTED`` bila jatah percobaan ``kind`` sudah habis, selain itu ``None``."""
    try:
        ensure_retry_available(state, kind)
    except RetryExhaustedError as exc:
        return {"ok": False, "error": exc.to_dict(), "retries_left": 0}
    return None


def ok_result(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


__all__ = [
    "DATASET_CONTEXT_KEYS",
    "PRIVACY_GUARD_VIOLATION",
    "RESULT_TOO_LARGE",
    "ROW_KEYS",
    "TOOL_ERROR",
    "PrivacyViolation",
    "after_tool_callback",
    "check_llm_payload",
    "error_result",
    "exhausted_result",
    "find_row_limit_violations",
    "llm_output",
    "ok_result",
]


# ---------------------------------------------------------------------------
# Pemeriksaan payload
# ---------------------------------------------------------------------------


class PrivacyViolation(ValueError):
    """Payload tool melanggar batas Privacy_Guard."""


def _walk(value: Any, path: str = "$") -> Iterator[tuple[str, Any, Any]]:
    """Yield ``(path, key, value)`` untuk setiap entri mapping secara rekursif."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            yield child_path, key, child
            yield from _walk(child, child_path)
    elif isinstance(value, (list, tuple)):
        for i, child in enumerate(value):
            yield from _walk(child, f"{path}[{i}]")


def find_row_limit_violations(
    payload: Any,
    *,
    max_rows: int = MAX_RESULT_ROWS,
    max_sample_rows: int = MAX_RESULT_ROWS,
) -> list[str]:
    """Daftar pelanggaran batas baris (``rows``/``records`` > ``max_rows``,
    ``sample_rows`` > ``max_sample_rows``)."""
    problems: list[str] = []
    for path, key, value in _walk(payload):
        if not isinstance(value, (list, tuple)):
            continue
        if key in ROW_KEYS and len(value) > max_rows:
            problems.append(f"{path}: {len(value)} baris > {max_rows}")
        elif key == SAMPLE_KEY and len(value) > max_sample_rows:
            problems.append(f"{path}: {len(value)} sample rows > {max_sample_rows}")
    return problems


def _is_dataset_context(value: Mapping[str, Any]) -> bool:
    return "column_profiles" in value or SAMPLE_KEY in value


def check_llm_payload(
    payload: Any, *, max_sample_rows: int = DEFAULT_SAMPLE_ROWS
) -> dict[str, Any]:
    """Validasi & normalisasi return tool; raise :class:`PrivacyViolation` bila melanggar."""
    if not isinstance(payload, Mapping):
        raise PrivacyViolation(f"return tool harus dict, didapat {type(payload).__name__}")
    if not isinstance(payload.get("ok"), bool):
        raise PrivacyViolation("return tool wajib memiliki key 'ok' bertipe bool")
    problems = find_row_limit_violations(payload, max_sample_rows=max_sample_rows)
    for path, _key, value in _walk(payload):
        if isinstance(value, Mapping) and _is_dataset_context(value):
            extra = sorted(set(map(str, value)) - DATASET_CONTEXT_KEYS)
            if extra:
                problems.append(
                    f"{path}: konteks Dataset hanya boleh berisi {sorted(DATASET_CONTEXT_KEYS)}"
                    f" (key tambahan: {extra})"
                )
    if problems:
        raise PrivacyViolation("; ".join(problems))
    return json_safe(dict(payload))


def _violation_result(tool_name: str, exc: PrivacyViolation) -> dict[str, Any]:
    log.error("Privacy_Guard menolak return tool %s: %s", tool_name, exc)
    return {
        "ok": False,
        "error": {
            "code": PRIVACY_GUARD_VIOLATION,
            "message": "Hasil tool ditolak Privacy_Guard karena melanggar batas data ke LLM.",
            "details": {"tool": tool_name, "reason": str(exc)},
        },
    }


def _finalize(tool_name: str, result: Any, max_sample_rows: int) -> dict[str, Any]:
    try:
        return check_llm_payload(result, max_sample_rows=max_sample_rows)
    except PrivacyViolation as exc:
        return _violation_result(tool_name, exc)


def _exception_result(tool_name: str, exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, StudioError):
        return json_safe(error_result(exc))
    log.exception("Tool %s gagal", tool_name)
    return {
        "ok": False,
        "error": {
            "code": TOOL_ERROR,
            "message": f"Tool {tool_name} gagal: {type(exc).__name__}: {exc}",
            "details": {"tool": tool_name},
        },
    }


# ---------------------------------------------------------------------------
# Dekorator
# ---------------------------------------------------------------------------


@overload
def llm_output(func: F, /) -> F: ...


@overload
def llm_output(*, max_sample_rows: int = DEFAULT_SAMPLE_ROWS) -> Callable[[F], F]: ...


def llm_output(
    func: F | None = None, /, *, max_sample_rows: int = DEFAULT_SAMPLE_ROWS
) -> F | Callable[[F], F]:
    """Dekorator tool LLM (sync atau async); signature asli dipertahankan untuk ADK.

    Pemakaian: ``@llm_output`` atau ``@llm_output(max_sample_rows=n)``.
    """

    def decorate(fn: F) -> F:
        name = getattr(fn, "__name__", "tool")
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - tidak pernah raise ke LLM
                    return _exception_result(name, exc)
                return _finalize(name, result, max_sample_rows)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                return _exception_result(name, exc)
            return _finalize(name, result, max_sample_rows)

        return sync_wrapper  # type: ignore[return-value]

    if func is not None:
        return decorate(func)
    return decorate


# ---------------------------------------------------------------------------
# Callback ADK
# ---------------------------------------------------------------------------


def after_tool_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    tool_response: Any,
) -> dict[str, Any] | None:
    """``after_tool_callback`` ADK: tolak hasil tool yang memuat > 200 baris (Req 27.6).

    Mengembalikan dict pengganti (error ``RESULT_TOO_LARGE``) bila ada list baris
    (``rows``/``records``/``sample_rows``) melebihi 200 elemen; ``None`` bila aman.
    """
    problems = find_row_limit_violations(tool_response)
    if not problems:
        return None
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", "tool")
    log.error("after_tool_callback menolak hasil tool %s: %s", tool_name, "; ".join(problems))
    return {
        "ok": False,
        "error": {
            "code": RESULT_TOO_LARGE,
            "message": (
                f"Hasil tool {tool_name} memuat lebih dari {MAX_RESULT_ROWS} baris dan tidak "
                "dikirim ke LLM. Gunakan agregasi atau LIMIT agar hasil lebih kecil."
            ),
            "details": {"tool": tool_name, "max_rows": MAX_RESULT_ROWS, "violations": problems},
        },
    }
