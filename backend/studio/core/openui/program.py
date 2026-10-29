"""Utilitas murni program canvas OpenUI Lang (tanpa I/O).

Statement disimpan per nama di ``DashboardContent.statements``. Fungsi di sini
membaca hasil analisis sidecar: props bernama dengan rujukan ``{"$ref": nama}``
dan argumen literal ``Query``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from studio.core.models import CanvasQuery, CanvasStatement

ROOT = "root"

#: Tool ``Query`` yang diizinkan (D7) dan tool wajib per komponen data.
QUERY_TOOLS: frozenset[str] = frozenset({"render_query", "render_kpi"})
DATA_TOOL: dict[str, str] = {
    "LineChart": "render_query",
    "BarChart": "render_query",
    "PieChart": "render_query",
    "ScatterChart": "render_query",
    "Heatmap": "render_query",
    "Waterfall": "render_query",
    "Table": "render_query",
    "Matrix": "render_query",
    "Slicer": "render_query",
    "Kpi": "render_kpi",
}
LAYOUT_COMPONENTS: frozenset[str] = frozenset({"VContainer", "HContainer", "Section", "Tabs"})
CHART_COMPONENTS: frozenset[str] = frozenset(
    {"LineChart", "BarChart", "PieChart", "ScatterChart", "Heatmap", "Waterfall"}
)
#: Komponen sistem yang tidak boleh ditulis agent maupun user (hanya migrasi).
SYSTEM_COMPONENTS: frozenset[str] = frozenset({"Unsupported"})

#: Lokasi prop berisi nama kolom per komponen; ``[]`` = daftar kolom.
COLUMN_PROPS: dict[str, tuple[str, ...]] = {
    "LineChart": ("x", "y[]", "options.crossFilter"),
    "BarChart": ("x", "y[]", "options.lines[]", "options.crossFilter"),
    "PieChart": ("category", "value", "options.crossFilter"),
    "ScatterChart": ("x", "y", "options.series", "options.crossFilter"),
    "Heatmap": ("x", "y", "value", "options.crossFilter"),
    "Waterfall": ("category", "value", "options.crossFilter"),
    "Table": ("columns[]",),
    "Matrix": ("rows", "columns", "value"),
    "Slicer": ("column",),
}
#: Argumen ``render_kpi`` berisi nama kolom.
KPI_COLUMN_ARGS: tuple[str, ...] = ("value", "compare")

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == {"$ref"}


def statement_from_sidecar(info: Mapping[str, Any]) -> CanvasStatement:
    query = info.get("query")
    return CanvasStatement(
        name=info["id"],
        source=info["raw"],
        kind=info["kind"],
        component=info.get("component"),
        refs=list(info.get("refs") or []),
        props=dict(info.get("props") or {}),
        query=CanvasQuery(tool=query.get("tool"), args=query.get("args")) if query else None,
    )


def program_text(statements: Mapping[str, CanvasStatement]) -> str:
    """Teks program; ``root`` selalu di depan (aturan streaming OpenUI)."""
    ordered = [s for n, s in statements.items() if n == ROOT] + [
        s for n, s in statements.items() if n != ROOT
    ]
    return "\n".join(s.source for s in ordered)


def iter_columns(stmt: CanvasStatement) -> Iterator[tuple[str, str]]:
    """``(path, nama_kolom)`` untuk setiap prop kolom komponen."""
    for path in COLUMN_PROPS.get(stmt.component or "", ()):
        is_list = path.endswith("[]")
        keys = path.removesuffix("[]").split(".")
        value: Any = stmt.props
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None:
            continue
        values = value if is_list and isinstance(value, list) else [value]
        for i, col in enumerate(values):
            yield (f"{path[:-2]}[{i}]" if is_list else path), col


def data_ref(stmt: CanvasStatement) -> Any:
    """Nilai prop ``data`` komponen (rujukan atau literal)."""
    return stmt.props.get("data")


def query_args(stmt: CanvasStatement) -> dict[str, Any]:
    args = stmt.query.args if stmt.query else None
    return args if isinstance(args, dict) else {}


def source_query_statement(
    statements: Mapping[str, CanvasStatement], stmt: CanvasStatement
) -> CanvasStatement | None:
    """Statement ``Query`` yang menjadi ``data`` komponen ``stmt``."""
    ref = data_ref(stmt)
    if not is_ref(ref):
        return None
    target = statements.get(ref["$ref"])
    return target if target is not None and target.kind == "query" else None


def statement_query_id(
    statements: Mapping[str, CanvasStatement], stmt: CanvasStatement
) -> str | None:
    """``query_id`` sumber statement (Query, komponen data, atau Insight)."""
    if stmt.kind == "query":
        qid = query_args(stmt).get("query")
        return qid if isinstance(qid, str) else None
    if stmt.component == "Insight":
        qid = stmt.props.get("query")
        return qid if isinstance(qid, str) else None
    source = source_query_statement(statements, stmt)
    return statement_query_id(statements, source) if source is not None else None


def title_of(stmt: CanvasStatement) -> str:
    title = stmt.props.get("title")
    return title if isinstance(title, str) and title else stmt.name


# ---------------------------------------------------------------------------
# Serializer nilai -> OpenUI Lang (dipakai converter & edit deterministik)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ref:
    """Rujukan ke statement lain saat menulis OpenUI Lang."""

    name: str


@dataclass(frozen=True)
class Call:
    """Pemanggilan komponen inline / ``Query``."""

    name: str
    args: tuple[Any, ...]


def to_lang(value: Any) -> str:
    if isinstance(value, Ref):
        return value.name
    if isinstance(value, Call):
        args = list(value.args)
        while args and args[-1] is None:  # argumen opsional di ujung boleh dihilangkan
            args.pop()
        return f"{value.name}({', '.join(to_lang(a) for a in args)})"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(to_lang(v) for v in value) + "]"
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            if v is None:
                continue
            key = k if _KEY.match(k) else json.dumps(k)
            parts.append(f"{key}: {to_lang(v)}")
        return "{" + ", ".join(parts) + "}"
    raise TypeError(f"Nilai tidak bisa diserialisasi ke OpenUI Lang: {type(value).__name__}")


def statement_line(name: str, call: Call) -> str:
    return f"{name} = {to_lang(call)}"


def safe_name(raw: str, prefix: str = "s") -> str:
    """Nama statement valid (huruf kecil di depan; huruf besar = token Type)."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw).lower()
    if not cleaned or not re.match(r"[a-z_]", cleaned[0]):
        cleaned = f"{prefix}_{cleaned}"
    return cleaned


__all__ = [
    "ROOT",
    "QUERY_TOOLS",
    "DATA_TOOL",
    "LAYOUT_COMPONENTS",
    "CHART_COMPONENTS",
    "SYSTEM_COMPONENTS",
    "COLUMN_PROPS",
    "KPI_COLUMN_ARGS",
    "Ref",
    "Call",
    "is_ref",
    "statement_from_sidecar",
    "program_text",
    "iter_columns",
    "data_ref",
    "query_args",
    "source_query_statement",
    "statement_query_id",
    "title_of",
    "to_lang",
    "statement_line",
    "safe_name",
]
