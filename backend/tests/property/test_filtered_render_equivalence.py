"""Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter.

Render chart = SQL tersimpan yang dieksekusi apa adanya (Req 22.2) di atas
tabel yang sudah dimaterialisasi dengan FilterSet aktif + propagasi semi-join
lewat Confirmed_Relation (Req 10.5, 24.2). Oracle independen:

1. setiap tabel difilter dengan evaluasi predikat baris per baris (Python murni),
2. propagasi dirambatkan memakai ``set`` Python sepanjang jalur rencana,
3. SQL yang sama dijalankan dengan ``pl.SQLContext`` (engine default, eager)
   pada tabel referensi tersebut.

Hasil ``run_job`` (Parquet → scan → materialize → SQL, engine streaming) dan
``DataEngine.run_saved`` dibandingkan dengan oracle tanpa memperhatikan urutan
baris: kolom, dtype, baris (multiset), dan ``row_count`` harus identik.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.core.filters import Predicate, date_range, dump_filter_set, in_values
from studio.core.models import ColumnInfo, QueryResult
from studio.core.propagation import (
    PropagationPlan,
    RelationGraph,
    affected_tables,
    plan_propagation,
)
from studio.core.relations import RelationCandidate
from studio.core.sql_rules import ConfirmedRelation
from studio.data.engine import DataEngine, logical_type_of_dtype
from studio.data.worker import InlineQueryRunner, QueryJob, QueryOutput, run_job
from studio.store.db import Database
from studio.store.repos import Repositories, new_id
from tests.property.strategies_data import (
    DataGraph,
    domain_for,
    filter_sets,
    graphs_with_filters,
    relation_graphs,
)

Row = dict[str, Any]

_SLOW = [HealthCheck.too_slow]


# ---------------------------------------------------------------------------
# Oracle: filter + propagasi Python murni, lalu SQL yang sama
# ---------------------------------------------------------------------------


def _holds(p: Predicate, value: Any) -> bool:
    if p.kind == "date_range":
        if p.start is None and p.end is None:
            return True
        if value is None:
            return False
        d = value.date() if isinstance(value, datetime) else value
        assert isinstance(d, date)
        return (p.start is None or d >= p.start) and (p.end is None or d <= p.end)
    if value is None:
        return None in p.values
    return value in p.values


def _direct_rows(rows: list[Row], preds: Sequence[Predicate], table: str) -> list[Row]:
    own = [p for p in preds if p.table == table]
    return [r for r in rows if all(_holds(p, r[p.column]) for p in own)]


def _key_set(rows: list[Row], column: str) -> set[Any]:
    """Nilai kunci non-null (null tidak pernah cocok dalam semi-join)."""
    return {r[column] for r in rows if r[column] is not None}


def _frame(rows: list[Row], schema: Mapping[str, pl.DataType]) -> pl.DataFrame:
    return pl.DataFrame({c: [r[c] for r in rows] for c in schema}, schema=dict(schema))


def _reference_tables(
    frames: Mapping[str, pl.DataFrame], preds: Sequence[Predicate], plan: PropagationPlan
) -> dict[str, pl.DataFrame]:
    """Tabel terfilter referensi: predikat langsung + semi-join sepanjang jalur rencana."""
    base = {t: df.to_dicts() for t, df in frames.items()}
    out: dict[str, pl.DataFrame] = {}
    for table, rows in base.items():
        kept = _direct_rows(rows, preds, table)
        for c in plan.for_table(table):
            parent = _direct_rows(base[c.source], preds, c.source)
            # Tabel antara: scan dasar yang di-semi-join dengan kunci induknya.
            for step in c.path[:-1]:
                keys = _key_set(parent, step.parent_column)
                parent = [r for r in base[step.child_table] if r[step.child_column] in keys]
            last = c.path[-1]
            keys = _key_set(parent, last.parent_column)
            kept = [r for r in kept if r[last.child_column] in keys]
        out[table] = _frame(kept, frames[table].schema)
    return out


def _execute(sql: str, frames: Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    ctx = pl.SQLContext(frames={t: df.lazy() for t, df in frames.items()}, eager=False)
    return ctx.execute(sql).collect()


def _expected(
    graph: DataGraph,
    sql: str,
    preds: Sequence[Predicate],
    relations: Iterable[Any] | None = None,
) -> pl.DataFrame:
    rels = graph.confirmed if relations is None else relations
    plan = plan_propagation(RelationGraph(rels), preds)
    return _execute(sql, _reference_tables(graph.frames, preds, plan))


# ---------------------------------------------------------------------------
# Eksekusi lewat worker (run_job) atas file Parquet
# ---------------------------------------------------------------------------


def _write_parquet(frames: Mapping[str, pl.DataFrame], root: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for table, df in frames.items():
        path = root / f"{table}.parquet"
        df.write_parquet(path)
        paths[table] = str(path)
    return paths


def _run(job: QueryJob) -> QueryOutput:
    resp = run_job(job.to_dict())
    assert resp["ok"], resp.get("error")
    return QueryOutput(
        schema=[(str(n), str(t)) for n, t in resp["schema"]],
        ipc=resp["ipc"],
        row_count=resp["row_count"],
        truncated=bool(resp["truncated"]),
    )


def _wire_value(v: Any) -> Any:
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _wire(preds: Iterable[Predicate]) -> list[dict[str, Any]]:
    """FilterSet dalam bentuk JSON API: nilai ``in`` bertipe tanggal dikirim sebagai
    string ISO (skalar JSON) dan di-coerce ke dtype kolom oleh Filter_Engine."""
    return dump_filter_set(
        in_values(p.table, p.column, [_wire_value(v) for v in p.values]) if p.kind == "in" else p
        for p in preds
    )


def _render(
    paths: Mapping[str, str],
    sql: str,
    preds: Sequence[Predicate],
    relations: Iterable[ConfirmedRelation],
) -> QueryOutput:
    """Render satu chart: job worker dengan FilterSet + rencana propagasi."""
    plan = plan_propagation(RelationGraph(relations), preds)
    job = QueryJob(tables=dict(paths), sql=sql, filters=_wire(preds), plan=plan.to_dict())
    # Teks SQL yang dikirim ke worker identik dengan SQL tersimpan (Req 22.2).
    assert job.sql == sql
    assert job.to_dict()["sql"] == sql
    return _run(job)


def _assert_output_matches(out: QueryOutput, expected: pl.DataFrame) -> None:
    assert out.schema == [(n, str(t)) for n, t in expected.schema.items()]
    got = out.to_frame()
    assert got.schema == expected.schema
    assert not out.truncated
    assert out.row_count == got.height == expected.height
    assert Counter(got.rows()) == Counter(expected.rows())


def _assert_result_matches(result: QueryResult, expected: pl.DataFrame) -> None:
    assert result.columns == [
        ColumnInfo(name=n, type=logical_type_of_dtype(t)) for n, t in expected.schema.items()
    ]
    assert result.row_count == len(result.rows) == expected.height
    assert Counter(tuple(r) for r in result.rows) == Counter(expected.rows())


# ---------------------------------------------------------------------------
# Generator SQL
# ---------------------------------------------------------------------------


def _single_table_sqls(t: str) -> list[str]:
    return [
        f"SELECT * FROM {t}",
        f"SELECT s, COUNT(*) AS cnt, SUM(n) AS total FROM {t} GROUP BY s",
        f"SELECT COUNT(*) AS cnt, MIN(d) AS d_min, MAX(ts) AS ts_max FROM {t}",
        f"SELECT k0, n, d FROM {t} WHERE n >= 0 OR d IS NULL",
    ]


def _join_sqls(r: ConfirmedRelation) -> list[str]:
    on = f"a.{r.column_a} = b.{r.column_b}"
    return [
        f"SELECT a.{r.column_a} AS key_a, a.n AS n_a, b.s AS s_b, b.d AS d_b "
        f"FROM {r.table_a} AS a INNER JOIN {r.table_b} AS b ON {on}",
        # Catatan: Polars SQL belum dapat GROUP BY kolom sisi kanan yang namanya
        # bentrok (``s:b``), sehingga kunci grup diambil dari sisi kiri.
        f"SELECT a.s AS s_a, COUNT(*) AS cnt, SUM(a.n) AS n_total "
        f"FROM {r.table_a} AS a JOIN {r.table_b} AS b ON {on} GROUP BY a.s",
    ]


def _sqls(graph: DataGraph, table: str | None = None) -> st.SearchStrategy[str]:
    """SQL SELECT valid atas tabel graf (JOIN hanya lewat Confirmed_Relation)."""
    tables = [table] if table is not None else sorted(graph.frames)
    options = [s for t in tables for s in _single_table_sqls(t)]
    options += [
        s
        for r in graph.confirmed
        if table is None or table in (r.table_a, r.table_b)
        for s in _join_sqls(r)
    ]
    return st.sampled_from(options)


#: Bias ke relasi confirmed agar propagasi sering terjadi (candidate/rejected tetap muncul).
_BIASED_STATUSES = ("confirmed", "confirmed", "confirmed", "candidate", "rejected")


def _filtered_case(
    data: st.DataObject, *, max_tables: int, max_filters: int = 3
) -> tuple[DataGraph, list[Predicate], str]:
    """Graf + FilterSet tak kosong + SQL yang (umumnya) menyentuh tabel terdampak filter."""
    graph = data.draw(
        relation_graphs(max_tables=max_tables, statuses=_BIASED_STATUSES), label="graph"
    )
    preds = data.draw(
        filter_sets(graph.schemas, min_size=1, max_size=max_filters), label="filters"
    )
    rgraph = RelationGraph(graph.confirmed)
    focus: list[st.SearchStrategy[str | None]] = [st.none()]
    if affected := sorted(affected_tables(rgraph, preds)):
        focus.append(st.sampled_from(affected))
    if targets := plan_propagation(rgraph, preds).targets:
        # Tabel yang hanya terfilter lewat propagasi (target constraint).
        focus.append(st.sampled_from(sorted(targets)))
    table = data.draw(st.one_of(*focus), label="focus_table")
    return graph, preds, data.draw(_sqls(graph, table), label="sql")


# ---------------------------------------------------------------------------
# Property: render terfilter = SQL atas tabel terfilter
# ---------------------------------------------------------------------------


@settings(deadline=None, suppress_health_check=_SLOW)
@given(data=st.data())
def test_filtered_render_matches_query_over_filtered_tables(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter

    Global_Filter acak (termasuk duplikat/no-op) + graf relasi acak; SQL satu
    tabel maupun JOIN lewat Confirmed_Relation.

    **Validates: Requirements 10.5, 22.2, 24.2**
    """
    graph, preds, sql = _filtered_case(data, max_tables=4)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        paths = _write_parquet(graph.frames, Path(tmp))
        out = _render(paths, sql, preds, graph.confirmed)
    _assert_output_matches(out, _expected(graph, sql, preds))


@settings(deadline=None, suppress_health_check=_SLOW)
@given(data=st.data())
def test_empty_filter_set_equals_unfiltered_query(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter

    FilterSet kosong (tanpa predikat, atau hanya ``date_range`` tanpa batas yang
    dinormalisasi hilang) → hasil sama dengan eksekusi SQL tanpa filter.

    **Validates: Requirements 10.5, 22.2, 24.2**
    """
    graph = data.draw(relation_graphs(max_tables=4))
    sql = data.draw(_sqls(graph), label="sql")
    tables = sorted(graph.frames)
    noops = data.draw(
        st.lists(
            st.builds(
                lambda t, c: date_range(t, c, None, None),
                st.sampled_from(tables),
                st.sampled_from(("d", "ts")),
            ),
            max_size=2,
        ),
        label="noop_filters",
    )
    rgraph = RelationGraph(graph.confirmed)
    variants: list[tuple[list[Predicate], dict[str, Any] | None]] = [
        ([], None),
        ([], plan_propagation(rgraph, []).to_dict()),
        (noops, plan_propagation(rgraph, noops).to_dict()),
    ]
    expected = _execute(sql, graph.frames)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        paths = _write_parquet(graph.frames, Path(tmp))
        for filters, plan in variants:
            job = QueryJob(tables=paths, sql=sql, filters=list(filters), plan=plan)
            assert job.filters == []
            assert job.sql == sql
            _assert_output_matches(_run(job), expected)


# ---------------------------------------------------------------------------
# Property: Cross_Filter berlaku ke semua chart kecuali chart sumbernya
# ---------------------------------------------------------------------------

SOURCE_CHART = "source"


def _chart_filters(
    chart_id: str,
    global_filters: Sequence[Predicate],
    cross: tuple[str, Predicate] | None,
) -> list[Predicate]:
    """FilterSet efektif per chart: Global_Filter + Cross_Filter (kecuali chart sumber)."""
    out = list(global_filters)
    if cross is not None and cross[0] != chart_id:
        out.append(cross[1])
    return out


@settings(deadline=None, suppress_health_check=_SLOW)
@given(data=st.data())
def test_cross_filter_excluded_from_source_chart(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter

    Klik pada chart sumber (atas tabel T) menghasilkan Cross_Filter ``in`` pada
    kolom T. Chart sumber dirender hanya dengan Global_Filter (hasilnya sama
    dengan tanpa Cross_Filter); chart lain dengan Global_Filter + Cross_Filter.

    **Validates: Requirements 10.5, 22.2, 24.2**
    """
    graph = data.draw(relation_graphs(max_tables=4, statuses=_BIASED_STATUSES), label="graph")
    global_preds = data.draw(filter_sets(graph.schemas, max_size=2), label="global_filters")
    source_table = data.draw(st.sampled_from(sorted(graph.frames)), label="source_table")
    source_sql = data.draw(_sqls(graph, source_table), label="source_sql")

    schema = graph.frames[source_table].schema
    column = data.draw(st.sampled_from(sorted(schema)), label="cross_column")
    clicked = data.draw(
        st.lists(
            st.one_of(st.none(), st.sampled_from(domain_for(schema[column]))),
            min_size=1,
            max_size=2,
        ),
        label="clicked",
    )
    cross = in_values(source_table, column, clicked)

    # Chart lain umumnya atas tabel yang terdampak Cross_Filter (sumber + propagasi).
    reach = sorted(affected_tables(RelationGraph(graph.confirmed), [cross]))
    other_sqls = data.draw(
        st.lists(
            st.one_of(st.none(), st.sampled_from(reach)).flatmap(lambda t: _sqls(graph, t)),
            min_size=1,
            max_size=3,
        ),
        label="other_sqls",
    )

    charts = {SOURCE_CHART: source_sql, **{f"chart{i}": s for i, s in enumerate(other_sqls)}}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        paths = _write_parquet(graph.frames, Path(tmp))
        rendered = {
            chart_id: _render(
                paths, sql, _chart_filters(chart_id, global_preds, (SOURCE_CHART, cross)),
                graph.confirmed,
            )
            for chart_id, sql in charts.items()
        }

    # Chart sumber: Cross_Filter dikecualikan → sama dengan hanya Global_Filter.
    _assert_output_matches(rendered[SOURCE_CHART], _expected(graph, source_sql, global_preds))
    # Chart lain: Global_Filter + Cross_Filter (dengan propagasinya).
    for chart_id, sql in charts.items():
        if chart_id != SOURCE_CHART:
            _assert_output_matches(
                rendered[chart_id], _expected(graph, sql, [*global_preds, cross])
            )


# ---------------------------------------------------------------------------
# DataEngine.run_saved end-to-end (Metadata_Store + InlineQueryRunner)
# ---------------------------------------------------------------------------


async def _check_engine(graph: DataGraph, preds: Sequence[Predicate], sql: str) -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        data_dir = Path(tmp) / "data"
        db = await Database(data_dir / "studio.db").open()
        try:
            repos = Repositories(db)
            ws = await repos.workspaces.create("render")
            dataset_ids: dict[str, str] = {}
            for table, df in graph.frames.items():
                ds_id = new_id()
                target = data_dir / "uploads" / ws.id / f"{ds_id}.parquet"
                target.parent.mkdir(parents=True, exist_ok=True)
                df.write_parquet(target)
                await repos.datasets.create(
                    ws.id,
                    table_name=table,
                    source_name=f"{table}.csv",
                    parquet_path=f"{ws.id}/{ds_id}.parquet",
                    schema=[
                        ColumnInfo(name=c, type=logical_type_of_dtype(d))
                        for c, d in df.schema.items()
                    ],
                    column_mapping=[(c, c) for c in df.columns],
                    row_count=df.height,
                    dataset_id=ds_id,
                )
                dataset_ids[table] = ds_id
            for rel in graph.relations:
                [rec] = await repos.relations.upsert_candidates(
                    ws.id,
                    [
                        RelationCandidate(
                            candidate_key=f"{rel.id}:{rel.table_a}.{rel.column_a}|"
                            f"{rel.table_b}.{rel.column_b}",
                            from_dataset_id=dataset_ids[rel.table_a],
                            from_table=rel.table_a,
                            from_column=rel.column_a,
                            to_dataset_id=dataset_ids[rel.table_b],
                            to_table=rel.table_b,
                            to_column=rel.column_b,
                            cardinality="many_to_many",
                            overlap_pct=100.0,
                        )
                    ],
                )
                if rel.status == "confirmed":
                    await repos.relations.confirm(rec.id)
                elif rel.status == "rejected":
                    await repos.relations.reject(rec.id)

            engine = DataEngine(repos, InlineQueryRunner(), data_dir)
            # Eksekusi awal tanpa filter → disimpan; hasil = SQL tanpa filter.
            saved = await engine.execute(ws.id, sql, filters=[])
            _assert_result_matches(saved, _execute(sql, graph.frames))

            # Render ulang dengan FilterSet aktif (JSON seperti dari API).
            result = await engine.run_saved(
                saved.query_id, filters=_wire(preds), workspace_id=ws.id
            )
            tables = await engine.workspace_tables(ws.id)
            _assert_result_matches(result, _expected(graph, sql, preds, tables.relations))
            assert result.query_id == saved.query_id

            # SQL tersimpan tidak diubah dan render tidak membuat query baru.
            stored = await repos.queries.list_by_workspace(ws.id)
            assert [q.id for q in stored] == [saved.query_id]
            assert stored[0].sql == sql
        finally:
            await db.close()


@settings(deadline=None, max_examples=15, suppress_health_check=_SLOW)
@given(data=st.data())
def test_data_engine_run_saved_matches_query_over_filtered_tables(data: st.DataObject) -> None:
    """Feature: dashboard-studio-agent, Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter

    ``DataEngine.run_saved`` atas Workspace nyata (SQLite + Parquet di
    ``DATA_DIR/uploads``); relasi candidate/rejected tidak ikut propagasi.

    **Validates: Requirements 10.5, 22.2, 24.2**
    """
    graph, preds, sql = _filtered_case(data, max_tables=3)
    asyncio.run(_check_engine(graph, preds, sql))
