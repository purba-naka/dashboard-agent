"""Design_Rules: review Dashboard terhadap prinsip desain BI (murni; Req 39.2, 39.3, 39.7).

``review(content, queries, time_columns)`` → daftar ``Finding`` terurut
``(code, item_ids)`` sehingga hasilnya tidak bergantung pada urutan item.

Aturan (lihat design "Design_Rules"): ``TOO_MANY_VISUALS``, ``KPI_NO_COMPARISON``,
``KPI_NOT_ON_TOP``, ``PIE_TOO_MANY_SLICES``, ``MISSING_TITLE``,
``MISSING_AXIS_NAME``, ``MULTI_SERIES_NO_LEGEND``, ``NO_TIME_TREND``,
``NO_CROSS_FILTER``, ``INCONSISTENT_METRIC_FORMAT``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from studio.core.models import ChartItem, DashboardContent, KpiItem

__all__ = [
    "MAX_VISUALS",
    "PIE_MAX_SLICES",
    "Finding",
    "QueryInfo",
    "review",
]

MAX_VISUALS = 12
PIE_MAX_SLICES = 6
_CARTESIAN = frozenset({"line", "bar", "scatter", "heatmap"})

Severity = Literal["info", "warning"]


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    item_ids: tuple[str, ...]
    message: str
    suggestion: str

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "item_ids": list(self.item_ids),
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class QueryInfo:
    """Metadata query item yang relevan untuk review."""

    tables_used: tuple[str, ...] = ()
    #: ``{kolom output: (tabel, kolom) | None}``
    lineage: Mapping[str, tuple[str, str] | None] = field(default_factory=dict)
    #: ``{kolom output: jumlah nilai berbeda di snapshot}``
    distinct_counts: Mapping[str, int] = field(default_factory=dict)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _chart_title(item: ChartItem) -> str:
    title = item.spec.option.get("title")
    for t in _as_list(title):
        if isinstance(t, Mapping) and str(t.get("text") or "").strip():
            return str(t["text"])
    return ""


def _x_column(item: ChartItem) -> str | None:
    for s in _as_list(item.spec.option.get("series")):
        if isinstance(s, Mapping):
            enc = s.get("encode") or {}
            x = enc.get("x")
            if isinstance(x, (list, tuple)):
                x = x[0] if x else None
            if isinstance(x, str):
                return x
    return None


def _pie_category_column(item: ChartItem) -> str | None:
    for s in _as_list(item.spec.option.get("series")):
        if isinstance(s, Mapping):
            name = (s.get("encode") or {}).get("itemName")
            if isinstance(name, (list, tuple)):
                name = name[0] if name else None
            if isinstance(name, str):
                return name
    return None


def review(
    content: DashboardContent,
    queries: Mapping[str, QueryInfo] | None = None,
    time_columns: Collection[tuple[str, str]] = (),
) -> list[Finding]:
    """Temuan review deterministik (lihat docstring modul)."""
    queries = queries or {}
    times = set(time_columns)
    items = content.items
    ids = sorted(items)
    out: list[Finding] = []

    def add(code: str, severity: Severity, item_ids: Collection[str], message: str, suggestion: str) -> None:
        out.append(Finding(code, severity, tuple(sorted(item_ids)), message, suggestion))

    if len(items) > MAX_VISUALS:
        add(
            "TOO_MANY_VISUALS",
            "warning",
            (),
            f"Dashboard berisi {len(items)} item (lebih dari {MAX_VISUALS}).",
            "Pindahkan detail ke dashboard lain atau gabungkan chart yang mirip.",
        )

    charts = {i: items[i] for i in ids if isinstance(items[i], ChartItem)}
    kpis = {i: items[i] for i in ids if isinstance(items[i], KpiItem)}

    for i, kpi in kpis.items():
        assert isinstance(kpi, KpiItem)
        if kpi.spec.comparison_column is None:
            add(
                "KPI_NO_COMPARISON",
                "info",
                [i],
                f"KPI '{kpi.title}' tidak memiliki pembanding.",
                "Tambahkan pembanding (mis. periode sebelumnya atau target) agar angka punya konteks.",
            )

    non_kpi_ys = [content.layout[i].y for i in ids if i not in kpis]
    if non_kpi_ys:
        top = min(non_kpi_ys)
        low = [i for i in kpis if content.layout[i].y > top]
        if low:
            add(
                "KPI_NOT_ON_TOP",
                "info",
                low,
                "Ada KPI yang tidak berada di baris teratas.",
                "Pindahkan baris KPI ke bagian paling atas dashboard.",
            )

    for i, chart in charts.items():
        assert isinstance(chart, ChartItem)
        option = chart.spec.option
        if not chart.title.strip() and not _chart_title(chart):
            add("MISSING_TITLE", "warning", [i], "Chart tidak memiliki judul.", "Beri judul yang menjelaskan isi chart.")
        if chart.spec.chart_type in _CARTESIAN:
            axes = [*_as_list(option.get("xAxis")), *_as_list(option.get("yAxis"))]
            if any(isinstance(a, Mapping) and not str(a.get("name") or "").strip() for a in axes):
                add(
                    "MISSING_AXIS_NAME",
                    "info",
                    [i],
                    f"Chart '{chart.title}' memiliki sumbu tanpa nama.",
                    "Isi nama sumbu X dan Y (satuan bila ada).",
                )
        series = _as_list(option.get("series"))
        if len(series) > 1 and "legend" not in option:
            add(
                "MULTI_SERIES_NO_LEGEND",
                "info",
                [i],
                f"Chart '{chart.title}' memiliki beberapa seri tanpa legenda.",
                "Tambahkan legenda agar seri dapat dibedakan.",
            )
        if chart.spec.chart_type == "pie":
            column = _pie_category_column(chart)
            info = queries.get(chart.spec.query_id)
            count = info.distinct_counts.get(column, 0) if info and column else 0
            if count > PIE_MAX_SLICES:
                add(
                    "PIE_TOO_MANY_SLICES",
                    "warning",
                    [i],
                    f"Pie '{chart.title}' memiliki {count} kategori.",
                    "Gunakan bar chart atau kelompokkan kategori kecil menjadi 'Lainnya'.",
                )

    # Kolom waktu ada di tabel yang dipakai, tetapi tidak ada chart tren.
    used_tables = {t for i in ids for t in _tables_of(items[i], queries)}
    if times and any(t in used_tables for t, _ in times) and (charts or kpis):
        has_trend = False
        for chart in charts.values():
            assert isinstance(chart, ChartItem)
            if chart.spec.chart_type not in ("line", "bar"):
                continue
            x = _x_column(chart)
            info = queries.get(chart.spec.query_id)
            ref = info.lineage.get(x) if info and x else None
            if ref is not None and tuple(ref) in times:
                has_trend = True
                break
        if not has_trend:
            add(
                "NO_TIME_TREND",
                "info",
                (),
                "Data memiliki kolom waktu tetapi dashboard belum menampilkan tren.",
                "Tambahkan chart garis/batang per periode untuk metrik utama.",
            )

    if len(charts) >= 2 and not any(
        c.spec.cross_filter_column for c in charts.values() if isinstance(c, ChartItem)
    ):
        add(
            "NO_CROSS_FILTER",
            "info",
            list(charts),
            "Belum ada chart yang dapat dipakai untuk cross-filter.",
            "Set kolom cross-filter pada chart kategori agar pengguna bisa mengeksplorasi.",
        )

    by_metric: dict[str, list[str]] = {}
    for i, kpi in kpis.items():
        assert isinstance(kpi, KpiItem)
        if kpi.spec.metric_name:
            by_metric.setdefault(kpi.spec.metric_name, []).append(i)
    for name in sorted(by_metric):
        group = by_metric[name]
        formats = {items[i].spec.format.model_dump_json() for i in group}  # type: ignore[union-attr]
        if len(group) > 1 and len(formats) > 1:
            add(
                "INCONSISTENT_METRIC_FORMAT",
                "warning",
                group,
                f"Metrik '{name}' ditampilkan dengan format berbeda.",
                "Samakan format angka untuk metrik yang sama.",
            )

    return sorted(out, key=lambda f: (f.code, f.item_ids, f.message))


def _tables_of(item: Any, queries: Mapping[str, QueryInfo]) -> tuple[str, ...]:
    query_id = item.query_id if getattr(item, "kind", None) == "insight" else item.spec.query_id
    info = queries.get(query_id)
    return info.tables_used if info else ()
