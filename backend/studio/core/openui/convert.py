"""Converter satu arah Dashboard legacy (items + grid) → program OpenUI Lang (D10).

Pemetaan:

- KPI → ``Query("render_kpi", ...)`` + ``Kpi``; Insight → ``Insight`` (lampiran
  Insight_Card lama dipasang ulang oleh Dashboard_Store).
- Chart → komponen chart dari ``chart_type`` + ``series[].encode``. Opsi yang
  tidak bisa dipetakan (mis. waterfall legacy dengan series transparan) menjadi
  ``Unsupported`` berisi alasan.
- Layout: item dengan ``y`` sama menjadi satu ``HContainer`` dengan ``weights``
  = lebar grid; baris disusun dalam ``root = VContainer([...])``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from studio.core.models import ChartItem, DashboardContent, DashboardItem, InsightItem, KpiItem
from studio.core.openui.program import Call, Ref, safe_name, statement_line


class _Unsupported(Exception):
    pass


@dataclass
class LegacyProgram:
    program: str
    #: nama statement komponen -> item_id legacy
    item_names: dict[str, str] = field(default_factory=dict)


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _series(option: Mapping[str, Any]) -> list[dict[str, Any]]:
    series = option.get("series")
    if isinstance(series, dict):
        series = [series]
    return [s for s in series or [] if isinstance(s, dict)]


def _axis_type(option: Mapping[str, Any], key: str) -> str | None:
    axis = option.get(key)
    axis = _first(axis) if isinstance(axis, list) else axis
    return axis.get("type") if isinstance(axis, dict) else None


def _enc(s: Mapping[str, Any], key: str) -> str | None:
    enc = s.get("encode")
    value = _first(enc.get(key)) if isinstance(enc, dict) else None
    return value if isinstance(value, str) else None


def _chart_call(item: ChartItem, data: Ref) -> Call:
    spec = item.spec
    option = spec.option
    series = _series(option)
    if not series:
        raise _Unsupported("chart tanpa series")
    opts: dict[str, Any] = {"crossFilter": spec.cross_filter_column}
    title = item.title

    if any(isinstance(s.get("itemStyle"), dict) and s["itemStyle"].get("color") == "transparent" for s in series):
        raise _Unsupported("waterfall legacy (series transparan) perlu dibuat ulang")

    if spec.chart_type in ("line", "bar"):
        horizontal = _axis_type(option, "yAxis") == "category"
        cat_key, val_key = ("y", "x") if horizontal else ("x", "y")
        x = next((_enc(s, cat_key) for s in series if _enc(s, cat_key)), None)
        ys = [c for c in (_enc(s, val_key) for s in series) if c]
        if not x or not ys:
            raise _Unsupported("encode x/y tidak lengkap")
        if spec.chart_type == "line" and all(s.get("type", "line") == "line" for s in series):
            opts["area"] = any("areaStyle" in s for s in series) or None
            opts["smooth"] = any(s.get("smooth") for s in series) or None
            return Call("LineChart", (title, data, x, ys, opts))
        lines = [c for s in series if s.get("type") == "line" and (c := _enc(s, val_key))]
        opts["stack"] = any(s.get("stack") for s in series) or None
        opts["horizontal"] = horizontal or None
        opts["lines"] = lines or None
        return Call("BarChart", (title, data, x, ys, opts))

    if spec.chart_type == "pie":
        s = series[0]
        category, value = _enc(s, "itemName"), _enc(s, "value")
        if not category or not value:
            raise _Unsupported("encode itemName/value tidak lengkap")
        opts["donut"] = isinstance(s.get("radius"), list) or None
        return Call("PieChart", (title, data, category, value, opts))

    if spec.chart_type == "scatter":
        s = series[0]
        x, y = _enc(s, "x"), _enc(s, "y")
        if not x or not y:
            raise _Unsupported("encode x/y tidak lengkap")
        return Call("ScatterChart", (title, data, x, y, opts))

    if spec.chart_type == "heatmap":
        s = series[0]
        x, y, value = _enc(s, "x"), _enc(s, "y"), _enc(s, "value")
        if not value:
            vm = option.get("visualMap")
            vm = _first(vm) if isinstance(vm, list) else vm
            dim = vm.get("dimension") if isinstance(vm, dict) else None
            value = dim if isinstance(dim, str) else None
        if not x or not y or not value:
            raise _Unsupported("encode heatmap tidak lengkap")
        return Call("Heatmap", (title, data, x, y, value, opts))

    raise _Unsupported(f"tipe chart '{spec.chart_type}' tidak dikenal")


def _kpi_query(item: KpiItem) -> Call:
    spec = item.spec
    fmt = spec.format
    args = {
        "query": spec.query_id,
        "value": spec.value_column,
        "compare": spec.comparison_column,
        "compare_label": spec.comparison_label,
        "format": fmt.style,
        "currency": fmt.currency,
        "decimals": fmt.decimals,
        "compact": fmt.compact,
        "good": spec.good_direction,
        "metric": spec.metric_name,
    }
    return Call("Query", ("render_kpi", args, {"formatted": {}}))


def legacy_to_program(content: DashboardContent) -> LegacyProgram:
    """Program OpenUI Lang yang setara dengan ``content`` legacy."""
    lines: list[str] = []
    out = LegacyProgram(program="")
    query_names: dict[str, str] = {}

    def render_query(query_id: str) -> Ref:
        name = query_names.get(query_id)
        if name is None:
            name = f"q_{len(query_names) + 1}"
            query_names[query_id] = name
            lines.append(statement_line(name, Call("Query", ("render_query", {"query": query_id}, {"rows": []}))))
        return Ref(name)

    def component(item: DashboardItem) -> Call:
        if isinstance(item, KpiItem):
            qname = safe_name(f"qk_{item.id}")
            lines.append(statement_line(qname, _kpi_query(item)))
            return Call("Kpi", (item.title, Ref(qname)))
        if isinstance(item, InsightItem):
            return Call("Insight", (item.insight_type, item.title, item.text, item.query_id))
        assert isinstance(item, ChartItem)
        try:
            return _chart_call(item, render_query(item.spec.query_id))
        except _Unsupported as exc:
            return Call("Unsupported", (item.title, f"Chart lama tidak dapat dikonversi: {exc}."))

    rows: dict[int, list[tuple[int, str, int]]] = {}
    for item_id, item in content.items.items():
        rect = content.layout[item_id]
        name = safe_name(f"it_{item_id}")
        out.item_names[name] = item_id
        lines.append(statement_line(name, component(item)))
        rows.setdefault(rect.y, []).append((rect.x, name, rect.w))

    row_refs: list[Ref] = []
    for i, y in enumerate(sorted(rows)):
        cells = sorted(rows[y])
        if len(cells) == 1:
            row_refs.append(Ref(cells[0][1]))
            continue
        row_name = f"row_{i + 1}"
        lines.append(
            statement_line(row_name, Call("HContainer", ([Ref(n) for _, n, _ in cells], [w for _, _, w in cells])))
        )
        row_refs.append(Ref(row_name))

    out.program = "\n".join([statement_line("root", Call("VContainer", (row_refs,))), *lines])
    return out


__all__ = ["LegacyProgram", "legacy_to_program"]
