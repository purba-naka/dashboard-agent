"""Konversi tipe chart untuk Canvas_Editor (Req 17.3; murni, tanpa I/O).

``convert_chart_type(spec, new_type, result_schema)`` memetakan ulang
``series[].encode`` sesuai tipe target lalu **selalu** memvalidasi ulang hasilnya
dengan ``validate_chart_spec``:

- bar ↔ line: ``encode``, sumbu, dan properti series dipertahankan; hanya
  ``series[].type`` yang diganti.
- ke pie: ``itemName = x`` (kolom kategori), ``value = y[0]``; ``xAxis``,
  ``yAxis``, ``grid``, dan ``dataZoom`` dihapus.
- pie → bar/line: ``x = itemName``, ``y = value`` dengan sumbu default
  ``xAxis {type: category}`` & ``yAxis {type: value}``.
- ke scatter: butuh dua kolom measure numerik (``integer``/``float``) di skema
  hasil query; kolom yang sudah di-encode diprioritaskan. Tidak ada → error
  ``INCOMPATIBLE_TYPE``.
- ke heatmap: ``x`` = kolom kategori, ``value`` = measure numerik, ``y`` = kolom
  kategorikal lain (non-numerik) dari skema; tidak ada → ``INCOMPATIBLE_TYPE``.
- heatmap → lainnya: ``x`` dan ``value`` dipakai sebagai kategori & measure;
  ``visualMap`` (khusus heatmap) dihapus.
- tipe sama: no-op (spec divalidasi ulang dan dikembalikan sebagai salinan);
  untuk bar/line, series combo campuran diseragamkan ke tipe target.

Key option lain (``title``, ``legend``, ``tooltip``, ``color``, ``toolbox``, ...)
dipertahankan apa adanya. Input tidak pernah dimutasi.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, get_args

from studio.core.chart_spec import ChartSpecError, validate_chart_spec
from studio.core.models import ChartSpec, ChartType, ColumnInfo

__all__ = ["convert_chart_type", "NUMERIC_TYPES"]

#: LogicalType yang dianggap measure numerik.
NUMERIC_TYPES: frozenset[str] = frozenset({"integer", "float"})

_SUPPORTED: frozenset[str] = frozenset(get_args(ChartType))
_COMBO: frozenset[str] = frozenset({"line", "bar"})
#: Key option yang hanya bermakna untuk chart bersumbu.
_AXIS_ONLY_KEYS: tuple[str, ...] = ("xAxis", "yAxis", "grid", "dataZoom")


@dataclass
class _Roles:
    """Peran kolom yang diekstrak dari encode spec sumber."""

    category: str | None = None
    values: list[str] = field(default_factory=list)
    #: Dimensi kedua (``y`` pada heatmap).
    secondary: str | None = None
    #: Nama series pertama (dipertahankan bila hasil hanya satu series).
    name: Any = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _refs(encode: Mapping[str, Any], dim: str) -> list[str]:
    if dim not in encode:
        return []
    return [r for r in _as_list(encode[dim]) if isinstance(r, str)]


def _dedupe(names: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _incompatible(src: str, dst: str, detail: str) -> ChartSpecError:
    return ChartSpecError(
        "INCOMPATIBLE_TYPE",
        "chart_type",
        f"tidak dapat mengubah chart {src} menjadi {dst}: {detail}",
        {"from_type": src, "to_type": dst},
    )


def _axis_type(option: Mapping[str, Any], axis: str) -> Any:
    items = _as_list(option.get(axis)) if axis in option else []
    first = items[0] if items else None
    return first.get("type") if isinstance(first, dict) else None


def _is_horizontal(option: Mapping[str, Any]) -> bool:
    """Bar/line horizontal: sumbu kategori di y, sumbu nilai di x."""
    return _axis_type(option, "yAxis") == "category" and _axis_type(option, "xAxis") in (
        "value",
        "log",
    )


def _extract_roles(option: Mapping[str, Any], chart_type: str) -> _Roles:
    series = [s for s in _as_list(option.get("series")) if isinstance(s, dict)]
    roles = _Roles(name=series[0].get("name") if series else None)
    categories: list[str] = []
    values: list[str] = []
    secondaries: list[str] = []

    if chart_type == "pie":
        cat_dim, val_dim, sec_dim = "itemName", "value", None
    elif chart_type == "heatmap":
        cat_dim, val_dim, sec_dim = "x", "value", "y"
    elif chart_type in _COMBO and _is_horizontal(option):
        cat_dim, val_dim, sec_dim = "y", "x", None
    else:  # bar/line vertikal, scatter
        cat_dim, val_dim, sec_dim = "x", "y", None

    for s in series:
        encode = s.get("encode")
        if not isinstance(encode, dict):
            continue
        categories.extend(_refs(encode, cat_dim))
        values.extend(_refs(encode, val_dim))
        if sec_dim is not None:
            secondaries.extend(_refs(encode, sec_dim))

    roles.category = categories[0] if categories else None
    roles.values = _dedupe(values)
    roles.secondary = secondaries[0] if secondaries else None
    return roles


def _named(series: dict[str, Any], name: Any) -> dict[str, Any]:
    if name is not None:
        series["name"] = name
    return series


# ---------------------------------------------------------------------------
# Builder per tipe target
# ---------------------------------------------------------------------------


def _to_combo(
    option: dict[str, Any], src: str, dst: str, roles: _Roles
) -> dict[str, Any]:
    if src in _COMBO:
        # bar ↔ line: pertahankan encode, sumbu, dan properti series.
        raw = option["series"]
        converted = [
            {**s, "type": dst} if isinstance(s, dict) else s for s in _as_list(raw)
        ]
        option["series"] = converted if isinstance(raw, (list, tuple)) else converted[0]
        return option

    if roles.category is None or not roles.values:
        raise _incompatible(src, dst, "encode sumber tidak memiliki kolom kategori dan nilai")
    if src == "heatmap":
        option.pop("visualMap", None)
    values = roles.values
    single = len(values) == 1
    option["series"] = [
        _named({"type": dst, "encode": {"x": roles.category, "y": v}}, roles.name if single else v)
        for v in values
    ]
    option["xAxis"] = {"type": "category"}
    option["yAxis"] = {"type": "value"}
    return option


def _to_pie(option: dict[str, Any], src: str, roles: _Roles) -> dict[str, Any]:
    if roles.category is None or not roles.values:
        raise _incompatible(src, "pie", "encode sumber tidak memiliki kolom kategori dan nilai")
    for key in _AXIS_ONLY_KEYS:
        option.pop(key, None)
    if src == "heatmap":
        option.pop("visualMap", None)
    option["series"] = [
        _named(
            {"type": "pie", "encode": {"itemName": roles.category, "value": roles.values[0]}},
            roles.name,
        )
    ]
    return option


def _to_scatter(
    option: dict[str, Any], src: str, roles: _Roles, schema: Sequence[ColumnInfo]
) -> dict[str, Any]:
    numeric = {c.name for c in schema if c.type in NUMERIC_TYPES}
    encoded = [roles.category, *roles.values, roles.secondary]
    picks = [n for n in _dedupe([n for n in encoded if n is not None]) if n in numeric]
    for c in schema:  # lengkapi dari measure numerik lain di skema
        if len(picks) >= 2:
            break
        if c.name in numeric and c.name not in picks:
            picks.append(c.name)
    if len(picks) < 2:
        raise _incompatible(
            src, "scatter", "scatter membutuhkan dua kolom measure numerik pada hasil query"
        )
    if src == "heatmap":
        option.pop("visualMap", None)
    option["series"] = [
        _named({"type": "scatter", "encode": {"x": picks[0], "y": picks[1]}}, roles.name)
    ]
    option["xAxis"] = {"type": "value"}
    option["yAxis"] = {"type": "value"}
    return option


def _to_heatmap(
    option: dict[str, Any], src: str, roles: _Roles, schema: Sequence[ColumnInfo]
) -> dict[str, Any]:
    types = {c.name: c.type for c in schema}
    x = roles.category
    if x is None:
        raise _incompatible(src, "heatmap", "encode sumber tidak memiliki kolom kategori")
    value = next(
        (v for v in roles.values if v != x and types.get(v) in NUMERIC_TYPES), None
    ) or next(
        (c.name for c in schema if c.name != x and c.type in NUMERIC_TYPES), None
    )
    if value is None:
        raise _incompatible(src, "heatmap", "heatmap membutuhkan kolom measure numerik")
    y = next(
        (
            c.name
            for c in schema
            if c.name not in (x, value) and c.type not in NUMERIC_TYPES
        ),
        None,
    )
    if y is None:
        raise _incompatible(
            src,
            "heatmap",
            "heatmap membutuhkan dua kolom dimensi berbeda (x dan y) serta satu measure numerik",
        )
    option["series"] = [
        _named({"type": "heatmap", "encode": {"x": x, "y": y, "value": value}}, roles.name)
    ]
    option["xAxis"] = {"type": "category"}
    option["yAxis"] = {"type": "category"}
    option.pop("dataZoom", None)
    option.setdefault(
        "visualMap",
        {
            "type": "continuous",
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": 0,
        },
    )
    return option


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------


def convert_chart_type(
    spec: ChartSpec | Mapping[str, Any],
    new_type: str,
    result_schema: Sequence[ColumnInfo],
) -> ChartSpec:
    """Konversi Chart_Spec ke ``new_type``; hasil selalu lolos ``validate_chart_spec``.

    Raise ``ChartSpecError`` dengan kode ``INCOMPATIBLE_TYPE`` bila skema hasil
    query tidak memenuhi syarat tipe target, ``UNSUPPORTED_SERIES`` bila
    ``new_type`` tidak didukung, atau kode validator lain bila spec sumber
    tidak valid.
    """
    if new_type not in _SUPPORTED:
        raise ChartSpecError(
            "UNSUPPORTED_SERIES",
            "chart_type",
            f"chart_type harus salah satu dari {sorted(_SUPPORTED)}",
        )
    source = validate_chart_spec(spec, result_schema)  # salinan tervalidasi
    src = source.chart_type
    # Tipe sama = no-op, kecuali combo bar/line: series campuran tetap diseragamkan
    # ke tipe target (Property 18: setiap ``series[].type`` == tipe target).
    if src == new_type and src not in _COMBO:
        return source

    option: dict[str, Any] = copy.deepcopy(source.option)
    roles = _extract_roles(option, src)

    if new_type in _COMBO:
        option = _to_combo(option, src, new_type, roles)
    elif new_type == "pie":
        option = _to_pie(option, src, roles)
    elif new_type == "scatter":
        option = _to_scatter(option, src, roles, result_schema)
    else:
        option = _to_heatmap(option, src, roles, result_schema)

    converted = source.model_dump(mode="python")
    converted["chart_type"] = new_type
    converted["option"] = option
    return validate_chart_spec(converted, result_schema)
