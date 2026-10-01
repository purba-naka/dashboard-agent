"""Strategi Hypothesis untuk konten Dashboard dan Command (Property 20–25).

Dipakai oleh test murni ``core/patches.py`` (Property 23, 24) dan test
state-machine Dashboard_Store (Property 20, 21, 22, 25).

Generator utama:

- ``dashboard_contents()`` — ``DashboardContent`` valid: chart (Chart_Spec valid
  terhadap ``QUERY_SCHEMAS``) dan Insight_Card, layout di grid 12 kolom,
  ``global_filters`` acak. Semua nilai "JSON-stabil" (sel bukti tanggal sudah
  berupa string ISO, timestamp aware UTC) sehingga content tetap sama setelah
  dipersist lalu dibaca ulang dari SQLite.
- ``valid_commands(content)`` — salah satu dari 9 jenis ``Command`` yang valid
  terhadap ``content`` saat ini (id yang ada untuk update/remove/layout; target
  ``change_chart_type`` yang dapat dikonversi).
- ``invalid_commands(content)`` — command yang pasti ditolak ``resolve_command``
  (id tidak ada → ``ItemNotFound``; jenis item salah / ``update_insight`` tanpa
  perubahan → ``InvalidOp``).
- ``contents_with_commands()`` — pasangan ``(content, command_valid)``.

Pendukung:

- ``QUERY_SCHEMAS`` — registri ``query_id → skema hasil`` tetap; setiap Chart_Spec
  yang dibangkitkan mereferensikan salah satu query ini (test Dashboard_Store
  dapat menyimpan query tersebut ke ``QueryRepo``).
- ``schema_converter(spec, new_type)`` — ``ChartConverter`` untuk
  ``resolve_command(..., convert_chart_type=...)``.
- ``sequential_ids(prefix)`` — ``IdFactory`` deterministik yang tidak pernah
  bentrok dengan id item hasil generator (prefix berbeda).
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, get_args

from hypothesis import strategies as st

from studio.core.chart_convert import convert_chart_type
from studio.core.chart_spec import ChartSpecError
from studio.core.models import (
    GRID_COLUMNS,
    AddChartCommand,
    AddInsightCommand,
    AddKpiCommand,
    BriefKpi,
    ChangeChartTypeCommand,
    ChartItem,
    ChartSpec,
    ChartType,
    ColumnInfo,
    Command,
    DashboardContent,
    DateRangePredicate,
    DesignBrief,
    EvidenceTable,
    InPredicate,
    InsightChanges,
    InsightDraft,
    InsightItem,
    InsightType,
    KpiItem,
    KpiSpec,
    LayoutRect,
    NumberFormat,
    NumberMatch,
    RemoveItemCommand,
    SectionRole,
    SetBriefCommand,
    TimeGrain,
    UpdateKpiCommand,
    SetGlobalFiltersCommand,
    SetLayoutCommand,
    SetTitleCommand,
    UpdateChartCommand,
    UpdateInsightCommand,
)
from tests.property.strategies_chart import chart_specs, query_results, result_schemas, safe_text

__all__ = [
    "QUERY_SCHEMAS",
    "COMMAND_TYPES",
    "ITEM_ID_PREFIX",
    "schema_converter",
    "compatible_chart_types",
    "sequential_ids",
    "item_ids",
    "layout_rects",
    "predicates",
    "filter_sets",
    "chart_spec_models",
    "evidence_tables",
    "insight_drafts",
    "insight_changes",
    "number_formats",
    "kpi_specs",
    "design_briefs",
    "dashboard_contents",
    "valid_commands",
    "invalid_commands",
    "contents_with_commands",
]

ALL_CHART_TYPES: tuple[str, ...] = get_args(ChartType)
INSIGHT_TYPES: tuple[str, ...] = get_args(InsightType)
COMMAND_TYPES: tuple[str, ...] = (
    "add_chart",
    "add_insight",
    "update_chart",
    "change_chart_type",
    "update_insight",
    "remove_item",
    "set_layout",
    "set_global_filters",
    "set_title",
    "add_kpi",
    "update_kpi",
    "set_brief",
)
#: Prefix id item hasil generator; ``sequential_ids`` memakai prefix lain.
ITEM_ID_PREFIX = "it_"

# ---------------------------------------------------------------------------
# Registri skema query & converter
# ---------------------------------------------------------------------------


def _schema(*cols: tuple[str, str]) -> list[ColumnInfo]:
    return [ColumnInfo(name=n, type=t) for n, t in cols]  # type: ignore[arg-type]


#: ``query_id → skema hasil``. Variasi sengaja: q_sales (semua tipe chart layak),
#: q_simple (1 dimensi + 1 measure: scatter/heatmap tidak layak), q_metric
#: (2 measure + 1 dimensi boolean: scatter layak, heatmap tidak).
QUERY_SCHEMAS: dict[str, list[ColumnInfo]] = {
    "q_sales0001": _schema(
        ("region", "string"), ("month", "date"), ("revenue", "float"), ("qty", "integer")
    ),
    "q_simple0002": _schema(("category", "string"), ("total", "integer")),
    "q_metric0003": _schema(("price", "float"), ("cost", "float"), ("is_promo", "boolean")),
}


def schema_converter(spec: ChartSpec, new_type: ChartType) -> ChartSpec:
    """``ChartConverter``: ``convert_chart_type`` dengan skema dari ``QUERY_SCHEMAS``."""
    return convert_chart_type(spec, new_type, QUERY_SCHEMAS[spec.query_id])


def compatible_chart_types(spec: ChartSpec) -> list[str]:
    """Tipe target yang berhasil dikonversi dari ``spec`` (selalu memuat tipe asal)."""
    out: list[str] = []
    for chart_type in ALL_CHART_TYPES:
        try:
            schema_converter(spec, chart_type)  # type: ignore[arg-type]
        except ChartSpecError:
            continue
        out.append(chart_type)
    return out


def sequential_ids(prefix: str = "new_") -> Callable[[], str]:
    """``IdFactory`` deterministik ``new_0001, new_0002, ...``."""
    counter = itertools.count(1)
    return lambda: f"{prefix}{next(counter):04d}"


# ---------------------------------------------------------------------------
# Primitif
# ---------------------------------------------------------------------------

item_ids = st.from_regex(rf"{ITEM_ID_PREFIX}[0-9a-z]{{8}}", fullmatch=True)
_names = st.from_regex(r"[a-z][a-z0-9_]{0,9}", fullmatch=True)
_dataset_ids = st.from_regex(r"ds_[0-9a-z]{6}", fullmatch=True)
_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1), max_value=datetime(2030, 12, 31), timezones=st.just(UTC)
)
_finite_floats = st.floats(-1e9, 1e9, allow_nan=False, allow_infinity=False)
_scalars = st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6), _finite_floats, safe_text)


@st.composite
def layout_rects(draw: st.DrawFn, max_y: int = 40) -> LayoutRect:
    """Rect grid 12 kolom (``x + w <= 12``)."""
    x = draw(st.integers(0, GRID_COLUMNS - 1))
    return LayoutRect(
        x=x,
        y=draw(st.integers(0, max_y)),
        w=draw(st.integers(1, GRID_COLUMNS - x)),
        h=draw(st.integers(1, 8)),
    )


@st.composite
def predicates(draw: st.DrawFn) -> DateRangePredicate | InPredicate:
    table, column = draw(_names), draw(_names)
    if draw(st.booleans()):
        start = draw(st.none() | st.dates(date(2000, 1, 1), date(2035, 12, 31)))
        end = draw(st.none() | st.dates(date(2000, 1, 1), date(2035, 12, 31)))
        if start is not None and end is not None and start > end:
            start, end = end, start
        return DateRangePredicate(table=table, column=column, start=start, end=end)
    values = draw(st.lists(_scalars, min_size=0, max_size=4))
    return InPredicate(table=table, column=column, values=tuple(values))


def filter_sets(max_size: int = 3) -> st.SearchStrategy[list[Any]]:
    return st.lists(predicates(), max_size=max_size)


def chart_spec_models(query_id: str | None = None) -> st.SearchStrategy[ChartSpec]:
    """``ChartSpec`` valid untuk salah satu query di ``QUERY_SCHEMAS``."""
    qids = st.just(query_id) if query_id is not None else st.sampled_from(sorted(QUERY_SCHEMAS))
    return qids.flatmap(
        lambda qid: chart_specs(QUERY_SCHEMAS[qid], query_id=qid)
    ).map(ChartSpec.model_validate)


def _json_stable_cell(value: Any) -> Any:
    # Tanggal pada sel bukti disimpan sebagai string ISO (sama seperti setelah
    # round-trip JSON), sehingga content stabil saat dipersist.
    if isinstance(value, date):
        return value.isoformat()
    return value


@st.composite
def evidence_tables(draw: st.DrawFn) -> EvidenceTable:
    schema = draw(result_schemas(max_dimensions=2, max_measures=2))
    result = draw(query_results(schema, max_rows=5))
    rows = [[_json_stable_cell(c) for c in row] for row in result.rows]
    return EvidenceTable(
        columns=result.columns,
        rows=rows,
        row_count=draw(st.integers(len(rows), len(rows) + 1000)),
    )


_number_matches = st.builds(
    NumberMatch,
    token=safe_text,
    value=_finite_floats,
    row=st.none() | st.integers(0, 199),
    column=st.none() | _names,
)


@st.composite
def insight_drafts(draw: st.DrawFn) -> InsightDraft:
    ds_ids = draw(st.lists(_dataset_ids, min_size=1, max_size=3, unique=True))
    versions = {d: draw(st.integers(1, 20)) for d in ds_ids if draw(st.booleans())}
    return InsightDraft(
        insight_type=draw(st.sampled_from(INSIGHT_TYPES)),
        title=draw(safe_text),
        text=draw(safe_text),
        query_id=draw(st.sampled_from(sorted(QUERY_SCHEMAS))),
        sql=draw(st.sampled_from(["SELECT 1", "SELECT region, SUM(revenue) FROM t GROUP BY 1"])),
        evidence=draw(evidence_tables()),
        matched_numbers=draw(st.lists(_number_matches, max_size=3)),
        filters_snapshot=draw(filter_sets()),
        dataset_ids=ds_ids,
        computed_at=draw(_timestamps),
        dataset_versions=versions,
    )


_INSIGHT_CHANGE_FIELDS: dict[str, st.SearchStrategy[Any]] = {
    "insight_type": st.sampled_from(INSIGHT_TYPES),
    "title": safe_text,
    "text": safe_text,
    "query_id": st.sampled_from(sorted(QUERY_SCHEMAS)),
    "sql": st.just("SELECT 2"),
    "evidence": evidence_tables(),
    "matched_numbers": st.lists(_number_matches, max_size=2),
    "filters_snapshot": filter_sets(),
    "dataset_ids": st.lists(_dataset_ids, min_size=1, max_size=2, unique=True),
    "computed_at": _timestamps,
    "dataset_versions": st.dictionaries(_dataset_ids, st.integers(1, 20), max_size=2),
}


@st.composite
def insight_changes(draw: st.DrawFn) -> InsightChanges:
    """``InsightChanges`` dengan minimal satu field terisi."""
    names = draw(
        st.lists(st.sampled_from(sorted(_INSIGHT_CHANGE_FIELDS)), min_size=1, max_size=3, unique=True)
    )
    return InsightChanges(**{n: draw(_INSIGHT_CHANGE_FIELDS[n]) for n in names})


# ---------------------------------------------------------------------------
# Dashboard content
# ---------------------------------------------------------------------------


def _numeric_columns(query_id: str) -> list[str]:
    return [c.name for c in QUERY_SCHEMAS[query_id] if c.type in ("integer", "float")]


number_formats = st.builds(
    NumberFormat,
    style=st.sampled_from(["number", "currency", "percent"]),
    currency=st.sampled_from(["IDR", "USD", None]),
    decimals=st.integers(0, 4),
    compact=st.booleans(),
)


@st.composite
def kpi_specs(draw: st.DrawFn) -> KpiSpec:
    """``KpiSpec`` valid terhadap salah satu query (kolom numerik)."""
    qid = draw(st.sampled_from(sorted(QUERY_SCHEMAS)))
    numeric = _numeric_columns(qid)
    value = draw(st.sampled_from(numeric))
    comparison = draw(st.none() | st.sampled_from(numeric))
    return KpiSpec(
        query_id=qid,
        value_column=value,
        comparison_column=comparison,
        comparison_label=draw(st.none() | safe_text),
        format=draw(number_formats),
        good_direction=draw(st.sampled_from(["up", "down", "neutral"])),
        metric_name=draw(st.none() | _names),
    )


_short_texts = st.lists(safe_text, max_size=3)


@st.composite
def design_briefs(draw: st.DrawFn) -> DesignBrief:
    return DesignBrief(
        purpose=draw(safe_text),
        audience=draw(safe_text),
        key_questions=draw(_short_texts),
        kpis=draw(
            st.lists(
                st.builds(
                    BriefKpi,
                    metric=_names,
                    compare=st.sampled_from(["previous_period", "target", "none"]),
                ),
                max_size=3,
            )
        ),
        sections=draw(st.lists(st.sampled_from(get_args(SectionRole)), max_size=4)),
        time_grain=draw(st.none() | st.sampled_from(get_args(TimeGrain))),
        assumptions=draw(_short_texts),
    )


@st.composite
def dashboard_contents(
    draw: st.DrawFn, min_items: int = 0, max_items: int = 6
) -> DashboardContent:
    """``DashboardContent`` valid (invariant ``keys(layout) == keys(items)``)."""
    ids = draw(st.lists(item_ids, min_size=min_items, max_size=max_items, unique=True))
    items: dict[str, ChartItem | InsightItem | KpiItem] = {}
    layout: dict[str, LayoutRect] = {}
    for item_id in ids:
        kind = draw(st.sampled_from(["chart", "insight", "kpi"]))
        if kind == "chart":
            items[item_id] = ChartItem(
                id=item_id, title=draw(safe_text), spec=draw(chart_spec_models())
            )
        elif kind == "kpi":
            items[item_id] = KpiItem(id=item_id, title=draw(safe_text), spec=draw(kpi_specs()))
        else:
            draft = draw(insight_drafts())
            items[item_id] = InsightItem(id=item_id, **dict(draft))
        layout[item_id] = draw(layout_rects())
    return DashboardContent(
        title=draw(safe_text),
        items=items,
        layout=layout,
        global_filters=draw(filter_sets()),
        brief=draw(st.none() | design_briefs()),
    )


def _ids_of(content: DashboardContent, kind: str | None = None) -> list[str]:
    return sorted(i for i, item in content.items.items() if kind is None or item.kind == kind)


def available_command_types(content: DashboardContent) -> list[str]:
    """Jenis command yang dapat dibuat valid untuk ``content``."""
    out = ["add_chart", "add_insight", "add_kpi", "set_global_filters", "set_title", "set_brief"]
    if _ids_of(content, "chart"):
        out += ["update_chart", "change_chart_type"]
    if _ids_of(content, "kpi"):
        out.append("update_kpi")
    if _ids_of(content, "insight"):
        out.append("update_insight")
    if content.items:
        out += ["remove_item", "set_layout"]
    return out


@st.composite
def valid_commands(
    draw: st.DrawFn,
    content: DashboardContent,
    command_types: tuple[str, ...] | list[str] | None = None,
) -> Command:
    """Command valid terhadap ``content`` (resolve & apply tidak akan gagal).

    ``command_types`` membatasi jenis command (diiris dengan yang tersedia).
    ``add_*`` mengasumsikan ``new_id`` pemanggil menghasilkan id baru (mis.
    ``sequential_ids()`` atau ULID default).
    """
    kinds = available_command_types(content)
    if command_types is not None:
        kinds = [k for k in kinds if k in command_types]
    if not kinds:
        raise ValueError("tidak ada jenis command yang tersedia untuk content ini")
    kind = draw(st.sampled_from(kinds))
    optional_layout = st.none() | layout_rects()

    if kind == "add_chart":
        return AddChartCommand(
            title=draw(safe_text), spec=draw(chart_spec_models()), layout=draw(optional_layout)
        )
    if kind == "add_insight":
        return AddInsightCommand(insight=draw(insight_drafts()), layout=draw(optional_layout))
    if kind == "update_chart":
        return UpdateChartCommand(
            id=draw(st.sampled_from(_ids_of(content, "chart"))),
            spec=draw(chart_spec_models()),
            title=draw(st.none() | safe_text),
        )
    if kind == "change_chart_type":
        item_id = draw(st.sampled_from(_ids_of(content, "chart")))
        spec = content.items[item_id].spec  # type: ignore[union-attr]
        return ChangeChartTypeCommand(
            id=item_id, chart_type=draw(st.sampled_from(compatible_chart_types(spec)))
        )
    if kind == "update_insight":
        return UpdateInsightCommand(
            id=draw(st.sampled_from(_ids_of(content, "insight"))),
            changes=draw(insight_changes()),
        )
    if kind == "remove_item":
        return RemoveItemCommand(id=draw(st.sampled_from(_ids_of(content))))
    if kind == "set_layout":
        targets = draw(st.lists(st.sampled_from(_ids_of(content)), min_size=1, unique=True))
        return SetLayoutCommand(changes={t: draw(layout_rects()) for t in targets})
    if kind == "set_global_filters":
        return SetGlobalFiltersCommand(filters=draw(filter_sets()))
    if kind == "add_kpi":
        return AddKpiCommand(
            title=draw(safe_text), spec=draw(kpi_specs()), layout=draw(optional_layout)
        )
    if kind == "update_kpi":
        return UpdateKpiCommand(
            id=draw(st.sampled_from(_ids_of(content, "kpi"))),
            spec=draw(kpi_specs()),
            title=draw(st.none() | safe_text),
        )
    if kind == "set_brief":
        return SetBriefCommand(brief=draw(st.none() | design_briefs()))
    return SetTitleCommand(title=draw(safe_text))


@st.composite
def invalid_commands(draw: st.DrawFn, content: DashboardContent) -> Command:
    """Command yang ditolak ``resolve_command`` untuk ``content``.

    Id tidak ada → ``ItemNotFound`` (``NOT_FOUND``); chart/insight tertukar atau
    ``update_insight`` tanpa perubahan → ``InvalidOp`` (``INVALID_OP``).
    """
    missing = draw(st.from_regex(r"missing_[0-9a-z]{6}", fullmatch=True))
    options: list[str] = ["missing"]
    if _ids_of(content, "insight"):
        options += ["chart_cmd_on_insight", "empty_insight_changes"]
    if _ids_of(content, "chart"):
        options.append("insight_cmd_on_chart")
    variant = draw(st.sampled_from(options))

    if variant == "missing":
        kind = draw(
            st.sampled_from(
                ["update_chart", "change_chart_type", "update_insight", "remove_item", "set_layout"]
            )
        )
        if kind == "update_chart":
            return UpdateChartCommand(id=missing, spec=draw(chart_spec_models()))
        if kind == "change_chart_type":
            return ChangeChartTypeCommand(id=missing, chart_type=draw(st.sampled_from(ALL_CHART_TYPES)))
        if kind == "update_insight":
            return UpdateInsightCommand(id=missing, changes=draw(insight_changes()))
        if kind == "remove_item":
            return RemoveItemCommand(id=missing)
        # Campuran id yang ada dan satu id yang tidak ada: seluruh command ditolak.
        existing_ids = (
            draw(st.lists(st.sampled_from(_ids_of(content)), unique=True)) if content.items else []
        )
        changes = {i: draw(layout_rects()) for i in existing_ids}
        changes[missing] = draw(layout_rects())
        return SetLayoutCommand(changes=changes)
    if variant == "chart_cmd_on_insight":
        target = draw(st.sampled_from(_ids_of(content, "insight")))
        if draw(st.booleans()):
            return UpdateChartCommand(id=target, spec=draw(chart_spec_models()))
        return ChangeChartTypeCommand(id=target, chart_type=draw(st.sampled_from(ALL_CHART_TYPES)))
    if variant == "insight_cmd_on_chart":
        target = draw(st.sampled_from(_ids_of(content, "chart")))
        return UpdateInsightCommand(id=target, changes=draw(insight_changes()))
    target = draw(st.sampled_from(_ids_of(content, "insight")))
    return UpdateInsightCommand(id=target, changes=InsightChanges())


def contents_with_commands(
    min_items: int = 0, max_items: int = 6
) -> st.SearchStrategy[tuple[DashboardContent, Command]]:
    """Pasangan ``(content, command valid terhadap content)``."""
    return dashboard_contents(min_items=min_items, max_items=max_items).flatmap(
        lambda c: st.tuples(st.just(c), valid_commands(c))
    )
