"""Validasi patch canvas OpenUI Lang, aturan V1–V7 (docs/plan-openui-canvas.md §3.4).

Masukan adalah hasil ``apply`` sidecar (program setelah merge + diff) dan skema
kolom setiap ``query_id`` yang dirujuk. V8 (angka insight) diperiksa
Dashboard_Store karena butuh bukti query tersimpan.

Semua pelanggaran dikumpulkan lalu dilaporkan sekaligus agar agent dapat
memperbaiki patch dalam satu percobaan ulang.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from studio.api.errors import StudioError
from studio.core.models import MAX_CANVAS_PATCH_BYTES, CanvasStatement
from studio.core.openui.program import (
    DATA_TOOL,
    KPI_COLUMN_ARGS,
    QUERY_TOOLS,
    SYSTEM_COMPONENTS,
    is_ref,
    iter_columns,
    query_args,
    source_query_statement,
    statement_from_sidecar,
)

Severity = Literal["error", "warning"]

#: C6 warisan — string yang dimaksudkan untuk dieksekusi sebagai kode.
_CODE_PATTERN = re.compile(
    r"function\s*\(|=>|javascript\s*:|<\s*script|new\s+Function|eval\s*\(",
    re.IGNORECASE,
)
#: Kode error parser lang-core yang berarti komponen tidak dikenal (V2).
_UNKNOWN = {"unknown-component", "unknown-function"}


@dataclass(frozen=True)
class CanvasIssue:
    code: str
    message: str
    statement: str | None = None
    path: str | None = None
    severity: Severity = "error"


class CanvasPatchError(StudioError):
    """Patch canvas ditolak; ``details.errors`` berisi semua pelanggaran."""

    def __init__(self, issues: Iterable[CanvasIssue]) -> None:
        items = [asdict(i) for i in issues]
        errors = [i for i in items if i["severity"] == "error"]
        first = errors[0] if errors else items[0]
        super().__init__(
            "CANVAS_PATCH_INVALID",
            f"Patch canvas ditolak ({len(errors)} pelanggaran). Pertama: [{first['code']}] "
            f"{first['statement'] or '-'}: {first['message']}",
            {"errors": errors, "warnings": [i for i in items if i["severity"] == "warning"]},
            http_status=422,
        )
        self.issues = items


@dataclass(frozen=True)
class CanvasPlan:
    """Program hasil merge yang lolos validasi."""

    statements: dict[str, CanvasStatement]
    added: list[str]
    replaced: list[str]
    removed: list[str]
    warnings: list[CanvasIssue]

    @property
    def touched(self) -> list[str]:
        return [*self.added, *self.replaced]


def referenced_query_ids(result: Mapping[str, Any]) -> set[str]:
    """Semua ``query_id`` literal pada program hasil merge (untuk prefetch skema)."""
    out: set[str] = set()
    for info in result.get("statements", []):
        if info.get("kind") == "query":
            args = (info.get("query") or {}).get("args")
            if isinstance(args, dict) and isinstance(args.get("query"), str):
                out.add(args["query"])
        elif info.get("component") == "Insight":
            qid = (info.get("props") or {}).get("query")
            if isinstance(qid, str):
                out.add(qid)
    return out


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def validate_canvas_patch(
    result: Mapping[str, Any],
    patch: str,
    *,
    schemas: Mapping[str, Collection[str] | None],
    allowed_components: Collection[str] | None = None,
) -> CanvasPlan:
    """Validasi hasil ``apply`` sidecar; raise :class:`CanvasPatchError` bila ada error.

    ``schemas`` memetakan ``query_id`` → nama kolom output (``None`` = query
    tidak ada atau milik Workspace lain).
    """
    issues: list[CanvasIssue] = []

    def err(code: str, message: str, statement: str | None = None, path: str | None = None) -> None:
        issues.append(CanvasIssue(code, message, statement, path))

    def warn(code: str, message: str, statement: str | None = None) -> None:
        issues.append(CanvasIssue(code, message, statement, severity="warning"))

    # V7 ukuran.
    if len(patch.encode("utf-8")) > MAX_CANVAS_PATCH_BYTES:
        err("TOO_LARGE", f"Patch melebihi {MAX_CANVAS_PATCH_BYTES // 1024} KB.")

    # V1 sintaks.
    for se in result.get("syntax_errors", []):
        err("PARSE_ERROR", f"{se['message']} Baris: {se['raw'][:120]}")

    statements = {info["id"]: statement_from_sidecar(info) for info in result.get("statements", [])}
    touched = set(result.get("added", [])) | set(result.get("replaced", []))

    # V1/V2 validasi skema prop oleh parser resmi.
    for pe in result.get("errors", []):
        if pe.get("severity") == "warning":
            warn("PARSER_WARNING", pe["message"], pe.get("statementId"))
        elif pe["code"] in _UNKNOWN:
            err("UNKNOWN_COMPONENT", pe["message"], pe.get("statementId"), pe.get("path"))
        else:
            err("PARSE_ERROR", f"[{pe['code']}] {pe['message']}", pe.get("statementId"), pe.get("path"))

    # V6 rujukan.
    for name in result.get("unresolved", []):
        warn("UNRESOLVED_REF", f"'{name}' dirujuk tetapi belum didefinisikan; tile kosong sampai diisi.")
    if result.get("has_root"):
        for name in result.get("orphaned", []):
            err("INVALID_REF", f"Statement '{name}' tidak terjangkau dari root; rujuk dari container.", name)
    elif statements:
        warn("NO_ROOT", "Program belum memiliki root = VContainer([...]).")

    allowed = set(allowed_components) if allowed_components is not None else None
    for name, stmt in statements.items():
        is_touched = name in touched
        label = stmt.component or stmt.kind

        # V3 allowlist (hanya statement yang ditulis patch ini).
        if is_touched:
            if stmt.component in SYSTEM_COMPONENTS:
                err("FORBIDDEN_COMPONENT", f"Komponen sistem '{stmt.component}' tidak boleh ditulis.", name)
            elif allowed is not None and stmt.kind in ("component", "query") and label not in allowed:
                err(
                    "FORBIDDEN_COMPONENT",
                    f"Komponen '{label}' di luar wewenang penulis ini (boleh: {', '.join(sorted(allowed))}).",
                    name,
                )
            if stmt.kind in ("state", "value") and name != "root":
                err("FORBIDDEN_COMPONENT", "Hanya statement komponen dan Query yang diizinkan (tanpa $variable).", name)
            # V7 pola kode.
            for s in _strings(stmt.props) if stmt.kind == "component" else _strings(query_args(stmt)):
                if _CODE_PATTERN.search(s):
                    err("CODE_VALUE", "String berpola kode tidak diizinkan.", name)
                    break

        # V4 Query.
        if stmt.kind == "mutation":
            err("UNKNOWN_QUERY", "Mutation tidak didukung di canvas.", name)
        if stmt.kind == "query":
            tool = stmt.query.tool if stmt.query else None
            args = query_args(stmt)
            if tool not in QUERY_TOOLS:
                err("UNKNOWN_QUERY", f"Tool Query '{tool}' tidak dikenal; pakai render_query atau render_kpi.", name)
                continue
            qid = args.get("query")
            if not isinstance(qid, str) or schemas.get(qid) is None:
                err("UNKNOWN_QUERY", f"query_id '{qid}' tidak ditemukan di Workspace ini; jalankan run_query dulu.", name)
                continue
            if tool == "render_kpi":
                cols = schemas[qid] or ()
                for arg in KPI_COLUMN_ARGS:
                    col = args.get(arg)
                    if arg == "value" and not isinstance(col, str):
                        err("UNKNOWN_COLUMN", "render_kpi wajib argumen value (kolom).", name, arg)
                    elif col is not None and col not in cols:
                        err("UNKNOWN_COLUMN", f"Kolom '{col}' tidak ada di hasil query {qid}.", name, arg)

        # V4/V5 komponen data.
        component = stmt.component or ""
        if stmt.kind == "component" and component in DATA_TOOL:
            data = stmt.props.get("data")
            if data is None and component == "Slicer":
                continue
            if not is_ref(data):
                err("INLINE_DATA", "Prop data wajib rujukan ke statement Query, bukan data inline.", name, "data")
                continue
            source = source_query_statement(statements, stmt)
            if source is None:
                if data["$ref"] in statements:
                    err("INLINE_DATA", f"'{data['$ref']}' bukan statement Query.", name, "data")
                continue  # belum didefinisikan: sudah dilaporkan sebagai UNRESOLVED_REF
            tool = source.query.tool if source.query else None
            if tool != DATA_TOOL[component]:
                err("UNKNOWN_QUERY", f"{component} membutuhkan Query(\"{DATA_TOOL[component]}\", ...), bukan {tool}.", name, "data")
                continue
            qid = query_args(source).get("query")
            cols = schemas.get(qid) if isinstance(qid, str) else None
            if cols is None:
                continue  # dilaporkan pada statement Query
            for path, col in iter_columns(stmt):
                if not isinstance(col, str) or col not in cols:
                    err("UNKNOWN_COLUMN", f"Kolom '{col}' tidak ada di hasil query {qid} (kolom: {', '.join(cols)}).", name, path)

        if stmt.kind == "component" and component == "Insight":
            qid = stmt.props.get("query")
            if not isinstance(qid, str) or schemas.get(qid) is None:
                err("UNKNOWN_QUERY", f"Insight merujuk query_id '{qid}' yang tidak ada.", name, "query")

    errors = [i for i in issues if i.severity == "error"]
    if errors:
        raise CanvasPatchError(issues)
    return CanvasPlan(
        statements=statements,
        added=list(result.get("added", [])),
        replaced=list(result.get("replaced", [])),
        removed=list(result.get("removed", [])),
        warnings=[i for i in issues if i.severity == "warning"],
    )


__all__ = [
    "CanvasIssue",
    "CanvasPatchError",
    "CanvasPlan",
    "referenced_query_ids",
    "validate_canvas_patch",
]
