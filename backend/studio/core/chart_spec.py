"""Chart_Spec_Validator dan binding data ECharts (murni; tanpa FastAPI/ADK).

``validate_chart_spec`` menerapkan aturan C1–C7 (design.md) dan menolak spec
dengan ``ChartSpecError(code, path, detail)``:

- C1 ``UNKNOWN_KEY`` / ``INLINE_DATA``: key top-level ``option`` di luar allowlist;
  ``dataset`` dilarang karena disediakan backend.
- C2 ``UNSUPPORTED_SERIES``: ``series[].type`` harus didukung dan konsisten dengan
  ``chart_type``.
- C3 ``INLINE_DATA``: tidak boleh ada ``series[].data``, ``xAxis[].data``,
  ``yAxis[].data`` (Req 13.2).
- C4 ``UNKNOWN_COLUMN``: setiap nama kolom pada ``series[].encode`` (dan
  ``cross_filter_column``) harus ada di skema hasil query (Req 13.3).
- C5 ``AXIS_STRUCTURE``: chart cartesian wajib ``xAxis`` & ``yAxis``; pie tanpa
  sumbu; pie maks 8 kategori dicek saat ``bind_data``.
- C6 ``CODE_VALUE``: hanya tipe JSON; string/key tidak boleh berpola kode (Req 13.4).
- C7 ``TOO_LARGE``: ukuran JSON ≤ 64 KB dan kedalaman ≤ 12.

``bind_data`` menambahkan ``option.dataset = {"dimensions", "source"}`` pada
salinan option (input tidak dimutasi); nilai date/datetime menjadi ISO-8601
(Req 12.3).
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, get_args

from pydantic import ValidationError

from studio.api.errors import StudioError
from studio.core.models import ChartSpec, ChartType, ColumnInfo, QueryResult

__all__ = [
    "ChartSpec",
    "ChartSpecError",
    "ALLOWED_OPTION_KEYS",
    "SUPPORTED_SERIES_TYPES",
    "CARTESIAN_TYPES",
    "MAX_SPEC_BYTES",
    "MAX_SPEC_DEPTH",
    "PIE_MAX_CATEGORIES",
    "validate_chart_spec",
    "bind_data",
]

#: C1 — key top-level ``option`` yang diizinkan.
ALLOWED_OPTION_KEYS: frozenset[str] = frozenset(
    {
        "title",
        "legend",
        "tooltip",
        "grid",
        "xAxis",
        "yAxis",
        "series",
        "color",
        "dataZoom",
        "visualMap",
        "toolbox",
    }
)
#: C2 — tipe series yang didukung (sama dengan ``ChartType``).
SUPPORTED_SERIES_TYPES: frozenset[str] = frozenset(get_args(ChartType))
CARTESIAN_TYPES: frozenset[str] = frozenset({"line", "bar", "scatter", "heatmap"})
#: Chart bar/line boleh mencampur series bar dan line (combo chart).
_COMBO_TYPES: frozenset[str] = frozenset({"line", "bar"})
#: C7 — batas ukuran (byte UTF-8 JSON ringkas) dan kedalaman container.
MAX_SPEC_BYTES = 64 * 1024
MAX_SPEC_DEPTH = 12
#: C5 — batas kategori pie saat render.
PIE_MAX_CATEGORIES = 8

#: C6 — pola string yang dimaksudkan untuk dieksekusi sebagai kode.
_CODE_PATTERN = re.compile(
    r"function\s*\(|=>|javascript\s*:|<\s*script|new\s+Function|eval\s*\(",
    re.IGNORECASE,
)


class ChartSpecError(StudioError):
    """Chart_Spec ditolak validator; ``code`` salah satu kode aturan C1–C7."""

    def __init__(
        self,
        code: str,
        path: str,
        detail: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        details: dict[str, Any] = {"code": code, "path": path, "detail": detail}
        if extra:
            details.update(extra)
        super().__init__(
            code,
            f"Chart_Spec tidak valid ({code}) di {path}: {detail}",
            details,
            http_status=422,
        )
        self.path = path
        self.detail = detail


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _key_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _check_values(spec: Any) -> None:
    """C6 (tipe JSON & pola kode) + kedalaman C7, secara iteratif."""
    stack: list[tuple[Any, str, int]] = [(spec, "", 0)]
    while stack:
        value, path, depth = stack.pop()
        if isinstance(value, (dict, list, tuple)):
            depth += 1
            if depth > MAX_SPEC_DEPTH:
                raise ChartSpecError(
                    "TOO_LARGE",
                    path or "$",
                    f"kedalaman spec melebihi {MAX_SPEC_DEPTH}",
                )
            if isinstance(value, dict):
                for key, child in value.items():
                    if not isinstance(key, str):
                        raise ChartSpecError(
                            "CODE_VALUE", path or "$", f"key non-string {key!r} bukan JSON"
                        )
                    child_path = _key_path(path, key)
                    if _CODE_PATTERN.search(key):
                        raise ChartSpecError(
                            "CODE_VALUE", child_path, "nama key berpola kode tidak diizinkan"
                        )
                    stack.append((child, child_path, depth))
            else:
                for i, child in enumerate(value):
                    stack.append((child, f"{path}[{i}]", depth))
        elif isinstance(value, str):
            if _CODE_PATTERN.search(value):
                raise ChartSpecError(
                    "CODE_VALUE",
                    path or "$",
                    "string berpola kode (fungsi/script) tidak diizinkan",
                )
        elif value is None or isinstance(value, (bool, int)):
            continue
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ChartSpecError("CODE_VALUE", path or "$", "angka non-finite bukan JSON")
        else:
            raise ChartSpecError(
                "CODE_VALUE",
                path or "$",
                f"nilai bertipe {type(value).__name__} bukan tipe JSON",
            )


def _check_size(spec: Any) -> None:
    raw = json.dumps(spec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_SPEC_BYTES:
        raise ChartSpecError(
            "TOO_LARGE",
            "$",
            f"ukuran spec {len(raw)} byte melebihi {MAX_SPEC_BYTES} byte",
        )


def _parse_model(spec: dict[str, Any]) -> ChartSpec:
    try:
        return ChartSpec.model_validate(spec)
    except ValidationError as exc:
        err = exc.errors()[0]
        path = ".".join(str(p) for p in err.get("loc", ())) or "$"
        if err.get("type") == "extra_forbidden":
            raise ChartSpecError("UNKNOWN_KEY", path, "field tidak dikenal") from None
        if path == "chart_type":
            raise ChartSpecError(
                "UNSUPPORTED_SERIES",
                path,
                f"chart_type harus salah satu dari {sorted(SUPPORTED_SERIES_TYPES)}",
            ) from None
        raise ChartSpecError("INVALID_SPEC", path, err.get("msg", "tidak valid")) from None


def _as_list(value: Any) -> list[Any]:
    """ECharts menerima komponen sebagai objek tunggal atau array objek."""
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _check_axes(option: Mapping[str, Any], chart_type: str) -> None:
    if chart_type in CARTESIAN_TYPES:
        for axis in ("xAxis", "yAxis"):
            if axis not in option:
                raise ChartSpecError(
                    "AXIS_STRUCTURE",
                    f"option.{axis}",
                    f"chart {chart_type} wajib memiliki {axis}",
                )
            items = _as_list(option[axis])
            if not items:
                raise ChartSpecError(
                    "AXIS_STRUCTURE", f"option.{axis}", f"{axis} tidak boleh kosong"
                )
            for i, item in enumerate(items):
                item_path = f"option.{axis}[{i}]" if isinstance(option[axis], list) else f"option.{axis}"
                if not isinstance(item, dict):
                    raise ChartSpecError(
                        "AXIS_STRUCTURE", item_path, f"{axis} harus berupa objek"
                    )
                if "data" in item:  # C3
                    raise ChartSpecError(
                        "INLINE_DATA",
                        f"{item_path}.data",
                        "data sumbu tidak boleh inline; kategori diambil dari hasil query",
                    )
    else:  # pie
        for axis in ("xAxis", "yAxis"):
            if axis in option:
                raise ChartSpecError(
                    "AXIS_STRUCTURE", f"option.{axis}", "chart pie tidak boleh memiliki sumbu"
                )


def _check_series(option: Mapping[str, Any], chart_type: str, columns: set[str]) -> None:
    if "series" not in option:
        raise ChartSpecError("UNSUPPORTED_SERIES", "option.series", "series wajib ada")
    raw = option["series"]
    series = _as_list(raw)
    if not series:
        raise ChartSpecError("UNSUPPORTED_SERIES", "option.series", "series tidak boleh kosong")
    allowed = _COMBO_TYPES if chart_type in _COMBO_TYPES else frozenset({chart_type})
    for i, s in enumerate(series):
        path = f"option.series[{i}]" if isinstance(raw, list) else "option.series"
        if not isinstance(s, dict):
            raise ChartSpecError("UNSUPPORTED_SERIES", path, "series harus berupa objek")
        s_type = s.get("type")
        if s_type not in SUPPORTED_SERIES_TYPES:
            raise ChartSpecError(
                "UNSUPPORTED_SERIES",
                f"{path}.type",
                f"tipe series {s_type!r} tidak didukung; gunakan salah satu dari "
                f"{sorted(SUPPORTED_SERIES_TYPES)}",
            )
        if s_type not in allowed:
            raise ChartSpecError(
                "UNSUPPORTED_SERIES",
                f"{path}.type",
                f"tipe series {s_type!r} tidak konsisten dengan chart_type {chart_type!r}",
            )
        if "data" in s:  # C3
            raise ChartSpecError(
                "INLINE_DATA",
                f"{path}.data",
                "data series tidak boleh inline; gunakan encode yang mereferensikan kolom hasil query",
            )
        encode = s.get("encode")
        if not isinstance(encode, dict) or not encode:
            raise ChartSpecError(
                "UNSUPPORTED_SERIES",
                f"{path}.encode",
                "series wajib memiliki encode yang memetakan dimensi ke kolom hasil query",
            )
        for dim, ref in encode.items():
            refs = ref if isinstance(ref, (list, tuple)) else [ref]
            is_list = isinstance(ref, (list, tuple))
            for j, name in enumerate(refs):
                ref_path = f"{path}.encode.{dim}" + (f"[{j}]" if is_list else "")
                if not isinstance(name, str) or name not in columns:
                    raise ChartSpecError(
                        "UNKNOWN_COLUMN",
                        ref_path,
                        f"kolom {name!r} tidak ada pada hasil query; kolom tersedia: "
                        f"{sorted(columns)}",
                        {"column": name},
                    )


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------


def validate_chart_spec(
    spec: Mapping[str, Any] | ChartSpec,
    result_schema: Sequence[ColumnInfo],
) -> ChartSpec:
    """Validasi Chart_Spec terhadap aturan C1–C7 dan skema hasil query.

    Raise ``ChartSpecError`` pada pelanggaran pertama yang ditemukan.
    Mengembalikan ``ChartSpec`` baru (salinan; input tidak dimutasi).
    """
    if isinstance(spec, ChartSpec):
        raw: Any = spec.model_dump(mode="python")
    else:
        raw = spec
    if not isinstance(raw, Mapping):
        raise ChartSpecError("INVALID_SPEC", "$", "Chart_Spec harus berupa objek")
    raw = dict(raw)

    # C6 + kedalaman C7 lebih dulu: aman terhadap nilai non-JSON/rekursif.
    _check_values(raw)
    _check_size(raw)  # C7

    model = _parse_model(copy.deepcopy(raw))
    option = model.option
    chart_type = model.chart_type
    columns = {c.name for c in result_schema}

    # C1
    for key in option:
        if key == "dataset":
            raise ChartSpecError(
                "INLINE_DATA",
                "option.dataset",
                "dataset disediakan backend dari hasil query; hapus key dataset",
            )
        if key not in ALLOWED_OPTION_KEYS:
            raise ChartSpecError(
                "UNKNOWN_KEY",
                f"option.{key}",
                f"key tidak diizinkan; gunakan salah satu dari {sorted(ALLOWED_OPTION_KEYS)}",
            )

    _check_series(option, chart_type, columns)  # C2, C3 (series), C4
    _check_axes(option, chart_type)  # C5, C3 (sumbu)

    if model.cross_filter_column is not None and model.cross_filter_column not in columns:
        raise ChartSpecError(
            "UNKNOWN_COLUMN",
            "cross_filter_column",
            f"kolom {model.cross_filter_column!r} tidak ada pada hasil query; kolom tersedia: "
            f"{sorted(columns)}",
            {"column": model.cross_filter_column},
        )
    return model


#: Pembulatan tampilan default untuk float tanpa format eksplisit (cegah label
#: "130.179999999" akibat presisi float mentah dari hasil agregasi SQL).
_DISPLAY_DECIMALS = 2


def _json_cell(value: Any) -> Any:
    if isinstance(value, date):  # termasuk datetime
        return value.isoformat()
    if isinstance(value, float) and math.isfinite(value):
        return round(value, _DISPLAY_DECIMALS)
    return value


def bind_data(spec: ChartSpec | Mapping[str, Any], result: QueryResult) -> dict[str, Any]:
    """Kembalikan option ECharts siap render dengan ``dataset`` dari hasil query.

    Hanya key ``dataset`` yang ditambahkan; key lain disalin apa adanya.
    Untuk chart pie, lebih dari ``PIE_MAX_CATEGORIES`` kategori ditolak
    (``AXIS_STRUCTURE``) agar agent memakai bar.
    """
    model = spec if isinstance(spec, ChartSpec) else ChartSpec.model_validate(spec)
    option = copy.deepcopy(model.option)
    dimensions = [c.name for c in result.columns]

    if model.chart_type == "pie":
        _check_pie_categories(option, dimensions, result)

    option["dataset"] = {
        "dimensions": dimensions,
        "source": [[_json_cell(cell) for cell in row] for row in result.rows],
    }
    return option


def _check_pie_categories(
    option: Mapping[str, Any], dimensions: list[str], result: QueryResult
) -> None:
    for i, s in enumerate(_as_list(option.get("series", []))):
        if not isinstance(s, dict):
            continue
        name = (s.get("encode") or {}).get("itemName")
        if isinstance(name, (list, tuple)):
            name = name[0] if name else None
        if not isinstance(name, str) or name not in dimensions:
            continue
        idx = dimensions.index(name)
        categories = {_json_cell(row[idx]) for row in result.rows}
        if len(categories) > PIE_MAX_CATEGORIES:
            raise ChartSpecError(
                "AXIS_STRUCTURE",
                f"option.series[{i}].encode.itemName",
                f"chart pie memiliki {len(categories)} kategori (maks {PIE_MAX_CATEGORIES}); "
                "gunakan chart bar",
                {"categories": len(categories)},
            )
