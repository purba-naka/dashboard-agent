"""Strategi Hypothesis bersama untuk Chart_Spec (Property 15, 16, 17, 18).

Generator:

- ``result_schemas()`` — skema hasil query acak (``list[ColumnInfo]``) dengan
  nama kolom unik; minimal ``min_dimensions`` kolom kategorikal (non-numerik)
  dan ``min_measures`` kolom numerik.
- ``query_results(schema)`` — ``QueryResult`` yang barisnya cocok dengan tipe
  kolom ``schema`` (sel boleh ``None``).
- ``chart_specs(schema)`` — Chart_Spec **valid** (``dict`` JSON-like) untuk
  ``schema``: series ber-``encode`` ke kolom skema, tanpa data inline, struktur
  sumbu sesuai tipe chart, dan tanpa string berpola kode.

Mutator (mengembalikan strategi; input tidak pernah dimutasi):

- ``inject_inline_data(spec)`` — sisipkan data inline pada ``series[].data``,
  ``xAxis/yAxis[].data`` (chart cartesian), atau ``option.dataset``.
- ``inject_unknown_column(spec, schema)`` — ``(spec, nama_kolom)`` dengan satu
  referensi kolom yang tidak ada di skema.
- ``inject_code_string(spec)`` — sisipkan string berpola kode di posisi acak
  (nilai, key, atau elemen list) di mana pun dalam spec.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime
from typing import Any

from hypothesis import strategies as st

from studio.core.models import ColumnInfo, QueryResult

__all__ = [
    "NUMERIC_TYPES",
    "CATEGORICAL_TYPES",
    "CARTESIAN",
    "CODE_PATTERN",
    "safe_text",
    "column_names",
    "query_ids",
    "result_schemas",
    "query_results",
    "chart_specs",
    "code_strings",
    "inject_inline_data",
    "inject_unknown_column",
    "inject_code_string",
]

NUMERIC_TYPES: tuple[str, ...] = ("integer", "float")
CATEGORICAL_TYPES: tuple[str, ...] = ("string", "date", "datetime", "boolean")
ALL_CHART_TYPES: tuple[str, ...] = ("line", "bar", "pie", "scatter", "heatmap")
CARTESIAN: frozenset[str] = frozenset({"line", "bar", "scatter", "heatmap"})

#: Pola kode sesuai tabel C6 design.md (dipakai untuk menyaring teks aman).
CODE_PATTERN = re.compile(
    r"function\s*\(|=>|javascript:|<script|new Function|eval\(", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Primitif
# ---------------------------------------------------------------------------

# Tanpa "(", "=", "<", ":" sehingga hampir tidak mungkin membentuk pola kode;
# sisa kasus ("new Function") disaring eksplisit.
_SAFE_ALPHABET = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    whitelist_characters=" -_.,%/{}#&'",
)
safe_text = st.text(_SAFE_ALPHABET, min_size=0, max_size=24).filter(
    lambda s: not CODE_PATTERN.search(s)
)
_nonempty_text = st.text(_SAFE_ALPHABET, min_size=1, max_size=24).filter(
    lambda s: not CODE_PATTERN.search(s)
)
#: Template formatter ECharts (bukan kode; diizinkan C6).
_FORMATTERS = st.sampled_from(["{b}: {c}", "{b} ({d}%)", "{a}<br/>{b}: {c}", "{c}", "{b}"])

column_names = st.from_regex(r"[a-z][a-z0-9_]{0,11}", fullmatch=True)
query_ids = st.from_regex(r"q_[0-9a-f]{8,12}", fullmatch=True)
_hex_colors = st.from_regex(r"#[0-9a-f]{6}", fullmatch=True)


def _is_numeric(col: ColumnInfo) -> bool:
    return col.type in NUMERIC_TYPES


# ---------------------------------------------------------------------------
# Skema & hasil query
# ---------------------------------------------------------------------------


@st.composite
def result_schemas(
    draw: st.DrawFn,
    min_dimensions: int = 1,
    min_measures: int = 1,
    max_dimensions: int = 3,
    max_measures: int = 3,
) -> list[ColumnInfo]:
    """Skema hasil query acak dengan nama kolom unik dan urutan acak."""
    n_dim = draw(st.integers(min_dimensions, max(min_dimensions, max_dimensions)))
    n_mea = draw(st.integers(min_measures, max(min_measures, max_measures)))
    names = draw(st.lists(column_names, min_size=n_dim + n_mea, max_size=n_dim + n_mea, unique=True))
    types = [draw(st.sampled_from(CATEGORICAL_TYPES)) for _ in range(n_dim)] + [
        draw(st.sampled_from(NUMERIC_TYPES)) for _ in range(n_mea)
    ]
    cols = [ColumnInfo(name=n, type=t) for n, t in zip(names, types)]  # type: ignore[arg-type]
    return draw(st.permutations(cols))


_CELL_STRATEGIES: dict[str, st.SearchStrategy[Any]] = {
    "integer": st.integers(-(10**9), 10**9),
    "float": st.floats(-1e9, 1e9, allow_nan=False, allow_infinity=False),
    "string": safe_text,
    "boolean": st.booleans(),
    "date": st.dates(min_value=date(2000, 1, 1), max_value=date(2035, 12, 31)),
    "datetime": st.datetimes(
        min_value=datetime(2000, 1, 1), max_value=datetime(2035, 12, 31, 23, 59, 59)
    ),
}


@st.composite
def query_results(
    draw: st.DrawFn,
    schema: list[ColumnInfo],
    query_id: str | None = None,
    min_rows: int = 0,
    max_rows: int = 20,
) -> QueryResult:
    """``QueryResult`` dengan sel bertipe sesuai ``schema`` (boleh ``None``)."""
    qid = query_id if query_id is not None else draw(query_ids)
    row_strategy = st.tuples(
        *(st.one_of(st.none(), _CELL_STRATEGIES[c.type]) for c in schema)
    ).map(list)
    rows = draw(st.lists(row_strategy, min_size=min_rows, max_size=max_rows))
    return QueryResult(
        query_id=qid,
        columns=[ColumnInfo(name=c.name, type=c.type) for c in schema],
        rows=rows,
        row_count=len(rows),
    )


# ---------------------------------------------------------------------------
# Chart_Spec valid
# ---------------------------------------------------------------------------


def feasible_chart_types(schema: list[ColumnInfo]) -> list[str]:
    """Tipe chart yang masuk akal untuk ``schema`` (butuh >= 1 kolom)."""
    numeric = [c for c in schema if _is_numeric(c)]
    categorical = [c for c in schema if not _is_numeric(c)]
    out = ["line", "bar", "pie"] if schema else []
    if len(numeric) >= 2:
        out.append("scatter")
    if len(categorical) >= 2 and numeric:
        out.append("heatmap")
    return out


def _maybe_list(draw: st.DrawFn, obj: dict[str, Any]) -> Any:
    """ECharts menerima komponen sebagai objek tunggal atau array objek."""
    return [obj] if draw(st.booleans()) else obj


def _axis(draw: st.DrawFn, axis_type: str) -> dict[str, Any]:
    axis: dict[str, Any] = {"type": axis_type}
    if draw(st.booleans()):
        axis["name"] = draw(safe_text)
    if draw(st.booleans()):
        axis["axisLabel"] = {"rotate": draw(st.integers(0, 90))}
    return axis


def _series_extras(draw: st.DrawFn, series: dict[str, Any]) -> dict[str, Any]:
    if draw(st.booleans()):
        series["name"] = draw(_nonempty_text)
    if draw(st.booleans()):
        series["label"] = {"show": draw(st.booleans()), "formatter": draw(_FORMATTERS)}
    return series


@st.composite
def chart_specs(
    draw: st.DrawFn,
    schema: list[ColumnInfo],
    chart_types: list[str] | tuple[str, ...] | None = None,
    query_id: str | None = None,
) -> dict[str, Any]:
    """Chart_Spec valid (dict JSON-like) untuk ``schema``.

    ``chart_types`` membatasi tipe yang dibangkitkan (diiris dengan tipe yang
    layak untuk skema). ``schema`` minimal berisi satu kolom.
    """
    feasible = feasible_chart_types(schema)
    if chart_types is not None:
        feasible = [t for t in feasible if t in chart_types]
    if not feasible:
        raise ValueError(f"tidak ada chart_type layak untuk skema {schema!r}")
    chart_type = draw(st.sampled_from(feasible))
    qid = query_id if query_id is not None else draw(query_ids)

    names = [c.name for c in schema]
    numeric = [c.name for c in schema if _is_numeric(c)] or names
    categorical = [c.name for c in schema if not _is_numeric(c)] or names

    option: dict[str, Any] = {}
    if chart_type in ("line", "bar"):
        horizontal = draw(st.booleans())
        cat = draw(st.sampled_from(categorical))
        measures = draw(st.lists(st.sampled_from(numeric), min_size=1, max_size=3, unique=True))
        series = []
        for i, m in enumerate(measures):
            s_type = chart_type if i == 0 else draw(st.sampled_from(["line", "bar"]))
            value_ref: Any = [m] if draw(st.booleans()) else m
            encode: dict[str, Any] = (
                {"y": cat, "x": value_ref} if horizontal else {"x": cat, "y": value_ref}
            )
            if draw(st.booleans()):
                encode["tooltip"] = [cat, m]
            s: dict[str, Any] = {"type": s_type, "encode": encode}
            if s_type == "line" and draw(st.booleans()):
                s["smooth"] = draw(st.booleans())
            if s_type == "bar" and draw(st.booleans()):
                s["stack"] = "total"
            series.append(_series_extras(draw, s))
        cat_axis, val_axis = _axis(draw, "category"), _axis(draw, "value")
        option["xAxis"] = _maybe_list(draw, val_axis if horizontal else cat_axis)
        option["yAxis"] = _maybe_list(draw, cat_axis if horizontal else val_axis)
        option["series"] = series
    elif chart_type == "scatter":
        x, y = draw(st.lists(st.sampled_from(numeric), min_size=2, max_size=2, unique=True))
        s = {"type": "scatter", "encode": {"x": x, "y": y}}
        if draw(st.booleans()):
            s["symbolSize"] = draw(st.integers(2, 30))
        option["xAxis"] = _maybe_list(draw, _axis(draw, "value"))
        option["yAxis"] = _maybe_list(draw, _axis(draw, "value"))
        option["series"] = [_series_extras(draw, s)]
    elif chart_type == "heatmap":
        x, y = draw(st.lists(st.sampled_from(categorical), min_size=2, max_size=2, unique=True))
        value = draw(st.sampled_from(numeric))
        option["xAxis"] = _maybe_list(draw, _axis(draw, "category"))
        option["yAxis"] = _maybe_list(draw, _axis(draw, "category"))
        option["series"] = [
            _series_extras(draw, {"type": "heatmap", "encode": {"x": x, "y": y, "value": value}})
        ]
        option["visualMap"] = {
            "min": 0,
            "max": draw(st.integers(1, 10**6)),
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
        }
    else:  # pie
        cat = draw(st.sampled_from(categorical))
        value = draw(st.sampled_from(numeric))
        s = {"type": "pie", "encode": {"itemName": cat, "value": value}}
        if draw(st.booleans()):
            s["radius"] = draw(st.sampled_from(["50%", ["40%", "70%"]]))
        option["series"] = [_series_extras(draw, s)]

    # Komponen opsional dari allowlist C1.
    if draw(st.booleans()):
        title: dict[str, Any] = {"text": draw(safe_text)}
        if draw(st.booleans()):
            title["subtext"] = draw(safe_text)
        option["title"] = title
    if draw(st.booleans()):
        option["legend"] = {"show": draw(st.booleans())}
    if draw(st.booleans()):
        option["tooltip"] = {"trigger": "item" if chart_type in ("pie", "heatmap", "scatter") else "axis"}
    if draw(st.booleans()):
        option["color"] = draw(st.lists(_hex_colors, min_size=1, max_size=5))
    if draw(st.booleans()):
        option["toolbox"] = {"show": True, "feature": {"saveAsImage": {}}}
    if chart_type in CARTESIAN:
        if draw(st.booleans()):
            option["grid"] = {"left": draw(st.integers(0, 80)), "containLabel": True}
        if chart_type != "heatmap" and draw(st.booleans()):
            option["dataZoom"] = [{"type": "inside"}]

    spec: dict[str, Any] = {
        "spec_version": 1,
        "query_id": qid,
        "chart_type": chart_type,
        "option": option,
    }
    if draw(st.booleans()):
        spec["cross_filter_column"] = draw(st.sampled_from(names))
    return spec


# ---------------------------------------------------------------------------
# Mutator
# ---------------------------------------------------------------------------

_inline_values = st.one_of(
    st.lists(st.integers(-1000, 1000), min_size=1, max_size=6),
    st.lists(st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False), min_size=1, max_size=6),
    st.lists(_nonempty_text, min_size=1, max_size=6),
    st.lists(st.tuples(_nonempty_text, st.integers(0, 100)).map(list), min_size=1, max_size=6),
    st.lists(
        st.fixed_dictionaries({"name": _nonempty_text, "value": st.integers(0, 100)}),
        min_size=1,
        max_size=6,
    ),
)


def _axis_indices(axis: Any) -> list[int | None]:
    return list(range(len(axis))) if isinstance(axis, list) else [None]


@st.composite
def inject_inline_data(draw: st.DrawFn, spec: dict[str, Any]) -> dict[str, Any]:
    """Salinan ``spec`` dengan data inline (C1/C3 → ``INLINE_DATA``)."""
    out = copy.deepcopy(spec)
    option = out["option"]
    targets: list[tuple[str, int | None]] = [("series", i) for i in range(len(option["series"]))]
    if out["chart_type"] in CARTESIAN:
        targets += [("xAxis", i) for i in _axis_indices(option["xAxis"])]
        targets += [("yAxis", i) for i in _axis_indices(option["yAxis"])]
    targets.append(("dataset", None))
    kind, idx = draw(st.sampled_from(targets))
    data = draw(_inline_values)

    if kind == "series":
        option["series"][idx]["data"] = data
    elif kind in ("xAxis", "yAxis"):
        axis = option[kind] if idx is None else option[kind][idx]
        axis["data"] = data
    else:
        dataset: dict[str, Any] = {"source": [[v] for v in data]}
        if draw(st.booleans()):
            dataset["dimensions"] = ["value"]
        option["dataset"] = dataset
    return out


@st.composite
def inject_unknown_column(
    draw: st.DrawFn, spec: dict[str, Any], schema: list[ColumnInfo]
) -> tuple[dict[str, Any], str]:
    """``(salinan spec, nama)`` dengan satu referensi ke kolom ``nama`` di luar skema."""
    known = {c.name for c in schema}
    missing = draw(column_names.filter(lambda n: n not in known))
    out = copy.deepcopy(spec)
    series = out["option"]["series"]

    target = draw(st.sampled_from(["replace", "add_dim", "cross_filter"]))
    if target == "cross_filter":
        out["cross_filter_column"] = missing
        return out, missing
    s = series[draw(st.integers(0, len(series) - 1))]
    encode = s["encode"]
    if target == "replace":
        dim = draw(st.sampled_from(sorted(encode)))
        ref = encode[dim]
        if isinstance(ref, list):
            ref[draw(st.integers(0, len(ref) - 1))] = missing
        else:
            encode[dim] = missing
    else:
        encode["tooltip"] = [missing] if draw(st.booleans()) else missing
    return out, missing


#: Payload berpola kode (tabel C6): fungsi, arrow function, URL javascript,
#: tag script, konstruktor Function, dan eval.
_CODE_PAYLOADS = st.sampled_from(
    [
        "function(){return 1}",
        "function (params) { return params.value; }",
        "function\t(x){}",
        "x => x * 2",
        "(p)=>p.name",
        "javascript:alert(1)",
        "<script>alert(1)</script>",
        "<script src='https://evil.example/x.js'>",
        "new Function('return this')()",
        "eval(atob('YWxlcnQoMSk='))",
        "eval('1+1')",
    ]
)
code_strings = st.tuples(safe_text, _CODE_PAYLOADS, safe_text).map("".join)


def _containers(node: Any, path: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Semua path menuju dict/list di dalam ``node`` (termasuk root)."""
    out: list[tuple[Any, ...]] = []
    if isinstance(node, dict):
        out.append(path)
        for k, v in node.items():
            out.extend(_containers(v, (*path, k)))
    elif isinstance(node, list):
        out.append(path)
        for i, v in enumerate(node):
            out.extend(_containers(v, (*path, i)))
    return out


def _get(node: Any, path: tuple[Any, ...]) -> Any:
    for p in path:
        node = node[p]
    return node


@st.composite
def inject_code_string(draw: st.DrawFn, spec: dict[str, Any]) -> dict[str, Any]:
    """Salinan ``spec`` dengan string berpola kode di posisi acak (C6 → ``CODE_VALUE``)."""
    out = copy.deepcopy(spec)
    container = _get(out, draw(st.sampled_from(_containers(out))))
    code = draw(code_strings)
    if isinstance(container, dict):
        mode = draw(st.sampled_from(["replace_value", "new_value", "as_key"]))
        if mode == "replace_value" and container:
            container[draw(st.sampled_from(sorted(container)))] = code
        elif mode == "as_key":
            container[code] = draw(st.one_of(st.none(), st.integers(), safe_text))
        else:
            container[draw(st.sampled_from(["formatter", "renderItem", "name", "x_extra"]))] = code
    else:
        if container and draw(st.booleans()):
            container[draw(st.integers(0, len(container) - 1))] = code
        else:
            container.insert(draw(st.integers(0, len(container))), code)
    return out
