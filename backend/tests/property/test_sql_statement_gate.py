"""Feature: dashboard-studio-agent, Property 12: Gerbang statement SQL.

Query SELECT/CTE yang dibangkitkan dari template AST sqlglot atas tabel terdaftar
selalu diterima SQL_Validator; SQL non-SELECT, multi-statement, atau yang membaca
file melalui table function / literal path selalu ditolak dengan kode alasan yang
sesuai, dan eksekutor tidak pernah dipanggil untuk SQL yang ditolak.

**Validates: Requirements 10.3, 10.4**
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import Mock

import pytest
import sqlglot
from hypothesis import given
from hypothesis import strategies as st
from sqlglot import exp

from studio.api.errors import StudioError
from studio.core.sql_rules import DIALECT, SqlAnalysis, analyze_sql

TABLES: dict[str, list[str]] = {
    "sales": ["id", "region", "amount", "qty", "order_date"],
    "customers": ["customer_id", "name", "city", "segment"],
    "products": ["product_id", "category", "price"],
}
_NUMERIC = {"id", "amount", "qty", "customer_id", "product_id", "price"}


def validate_then_execute(sql: str, executor: Callable[[str], Any]) -> Any:
    """Jalur eksekusi minimal: validasi dulu, eksekutor hanya dipanggil bila lolos."""
    analysis = analyze_sql(sql, TABLES)
    return executor(analysis.sql)


# ---------------------------------------------------------------------------
# Generator query SELECT (diterima)
# ---------------------------------------------------------------------------

_tables = st.sampled_from(sorted(TABLES))
_ident = st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6).map(lambda s: f"q_{s}")


@st.composite
def _condition(draw: st.DrawFn, table: str, qual: str | None = None) -> exp.Expression:
    col = draw(st.sampled_from(TABLES[table]))
    ref = exp.column(col, table=qual)
    if col in _NUMERIC:
        value = exp.Literal.number(draw(st.integers(-1000, 1000)))
        op = draw(st.sampled_from([exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ]))
        return op(this=ref, expression=value)
    text = exp.Literal.string(draw(st.text(alphabet="abcXYZ '%_", max_size=8)))
    kind = draw(st.sampled_from(["eq", "like", "null"]))
    if kind == "eq":
        return exp.EQ(this=ref, expression=text)
    if kind == "like":
        return exp.Like(this=ref, expression=text)
    return exp.Is(this=ref, expression=exp.Null())


@st.composite
def _base_select(draw: st.DrawFn, table: str | None = None) -> exp.Select:
    """SELECT satu tabel: proyeksi kolom / agregat, WHERE, GROUP BY, ORDER BY, LIMIT."""
    table = table or draw(_tables)
    cols = TABLES[table]
    alias = draw(st.one_of(st.none(), _ident))
    qual = alias if alias else draw(st.sampled_from([None, table]))
    source = exp.table_(table, alias=alias) if alias else exp.table_(table)

    if draw(st.booleans()):  # agregasi dengan GROUP BY
        keys = draw(st.lists(st.sampled_from(cols), min_size=1, max_size=2, unique=True))
        measure = draw(st.sampled_from(sorted(set(cols) & _NUMERIC)))
        agg = draw(st.sampled_from([exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count]))
        projs: list[exp.Expression] = [exp.column(k, table=qual) for k in keys]
        projs.append(exp.alias_(agg(this=exp.column(measure, table=qual)), "m"))
        query = exp.select(*projs).from_(source).group_by(*[exp.column(k, table=qual) for k in keys])
        order_keys = [exp.column(k, table=qual) for k in keys]
    else:
        if draw(st.booleans()):
            projs = [exp.Star()]
            order_keys = [exp.column(cols[0], table=qual)]
        else:
            picked = draw(st.lists(st.sampled_from(cols), min_size=1, max_size=len(cols), unique=True))
            projs = [
                exp.alias_(exp.column(c, table=qual), f"{c}_out") if draw(st.booleans()) else exp.column(c, table=qual)
                for c in picked
            ]
            order_keys = [exp.column(picked[0], table=qual)]
        query = exp.select(*projs).from_(source)

    for _ in range(draw(st.integers(0, 2))):
        query = query.where(draw(_condition(table, qual)))
    if draw(st.booleans()):
        query = query.order_by(*order_keys)
    if draw(st.booleans()):
        query = query.limit(draw(st.integers(1, 1000)))
    return query


@st.composite
def _select_ast(draw: st.DrawFn) -> exp.Expression:
    shape = draw(st.sampled_from(["plain", "cte", "derived", "union", "self_join", "in_subquery"]))
    if shape == "plain":
        return draw(_base_select())
    if shape == "cte":
        names = draw(st.lists(_ident, min_size=1, max_size=3, unique=True))
        query: exp.Select = exp.select("*").from_(names[-1])
        for name in names:
            query = query.with_(name, as_=draw(_base_select()))
        return query
    if shape == "derived":
        inner = draw(_base_select())
        return exp.select("*").from_(inner.subquery(draw(_ident)))
    if shape == "union":
        table = draw(_tables)
        left = exp.select(*TABLES[table]).from_(table).where(draw(_condition(table)))
        right = exp.select(*TABLES[table]).from_(table).where(draw(_condition(table)))
        op = draw(st.sampled_from([exp.union, exp.intersect, exp.except_]))
        return op(left, right, distinct=draw(st.booleans()))
    if shape == "self_join":
        table = draw(_tables)
        key = TABLES[table][0]
        return (
            exp.select(exp.column(key, table="a"))
            .from_(exp.table_(table, alias="a"))
            .join(
                exp.table_(table, alias="b"),
                on=exp.EQ(this=exp.column(key, table="a"), expression=exp.column(key, table="b")),
                join_type=draw(st.sampled_from(["inner", "left"])),
            )
        )
    # in_subquery
    outer = draw(_base_select())
    other = draw(_tables)
    sub = exp.select(TABLES[other][0]).from_(other)
    col = TABLES[outer.args["from_" if "from_" in outer.args else "from"].this.name][0]
    return outer.where(exp.In(this=exp.column(col), query=sub.subquery()))


@st.composite
def select_queries(draw: st.DrawFn) -> str:
    """Query SELECT/CTE valid atas tabel terdaftar, dirender dari AST sqlglot."""
    sql = draw(_select_ast()).sql(dialect=DIALECT)
    return sql + draw(st.sampled_from(["", ";", " ;", ";\n"]))


# ---------------------------------------------------------------------------
# Generator SQL terlarang (ditolak)
# ---------------------------------------------------------------------------

_paths = st.builds(
    lambda d, n, e: f"{d}{n}{e}",
    st.sampled_from(["", "data/", "./", "../", "/tmp/", "C:/data/", "s3://bucket/"]),
    st.text(alphabet="abcxyz_0123", min_size=1, max_size=8),
    st.sampled_from([".csv", ".parquet", ".json", ".arrow", ".xlsx", ".tsv"]),
)
_file_functions = st.sampled_from(
    ["read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_ipc", "parquet_scan"]
)
_new_names = _ident.map(lambda s: f"new_{s}")


@st.composite
def _root_non_select(draw: st.DrawFn) -> tuple[str, str]:
    """Statement non-SELECT pada root → ``(sql, kata kunci statement)``."""
    table = draw(_tables)
    cols = TABLES[table]
    col = draw(st.sampled_from(cols))
    kind = draw(
        st.sampled_from(
            ["INSERT", "INSERT_VALUES", "UPDATE", "DELETE", "DROP", "CREATE", "CREATE_VIEW",
             "ALTER", "COPY", "TRUNCATE", "ATTACH", "PRAGMA", "INSTALL"]
        )
    )
    tbl = exp.to_table(table)
    if kind == "INSERT":
        node: exp.Expression = exp.insert(draw(_base_select(table)), table)
    elif kind == "INSERT_VALUES":
        row = tuple(draw(st.integers(0, 99)) for _ in cols)
        node = exp.insert(exp.values([row]), table, columns=cols)
    elif kind == "UPDATE":
        node = exp.update(tbl, {col: exp.Literal.number(draw(st.integers(0, 9)))},
                          where=draw(st.one_of(st.none(), _condition(table))))
    elif kind == "DELETE":
        node = exp.delete(tbl, where=draw(st.one_of(st.none(), _condition(table))))
    elif kind == "DROP":
        what = draw(st.sampled_from(["TABLE", "VIEW", "TABLE IF EXISTS"]))
        node = sqlglot.parse_one(f"DROP {what} {table}", dialect=DIALECT)
    elif kind in ("CREATE", "CREATE_VIEW"):
        node = exp.Create(
            this=exp.to_table(draw(_new_names)),
            kind="TABLE" if kind == "CREATE" else "VIEW",
            expression=draw(_base_select(table)),
        )
    elif kind == "ALTER":
        node = sqlglot.parse_one(f"ALTER TABLE {table} ADD COLUMN {draw(_new_names)} INTEGER", dialect=DIALECT)
    elif kind == "COPY":
        node = sqlglot.parse_one(f"COPY {table} TO '{draw(_paths)}'", dialect=DIALECT)
    elif kind == "TRUNCATE":
        node = sqlglot.parse_one(f"TRUNCATE {table}", dialect=DIALECT)
    elif kind == "ATTACH":
        node = sqlglot.parse_one(f"ATTACH '{draw(_paths)}'", dialect=DIALECT)
    elif kind == "PRAGMA":
        node = sqlglot.parse_one(f"PRAGMA {draw(st.sampled_from(['version', 'database_list']))}", dialect=DIALECT)
    else:
        node = sqlglot.parse_one(f"INSTALL {draw(st.sampled_from(['httpfs', 'sqlite']))}", dialect=DIALECT)
    keyword = kind.split("_")[0]
    return node.sql(dialect=DIALECT), keyword


@st.composite
def _nested_forbidden(draw: st.DrawFn) -> str:
    """Query SELECT yang menyembunyikan statement terlarang di dalam AST."""
    table = draw(_tables)
    if draw(st.booleans()):
        # SELECT ... INTO: dirender ulang oleh sqlglot sebagai CREATE, jadi dipakai teks template.
        return f"SELECT * INTO {draw(_new_names)} FROM {table}"
    cte = draw(_ident)
    dml = draw(st.sampled_from([f"DELETE FROM {table}", f"UPDATE {table} SET {TABLES[table][0]} = 0"]))
    tree = sqlglot.parse_one(f"WITH {cte} AS ({dml} RETURNING *) SELECT * FROM {cte}", dialect=DIALECT)
    return tree.sql(dialect=DIALECT)


@st.composite
def _file_source(draw: st.DrawFn) -> str:
    """Pembacaan file via table function atau literal path pada FROM/JOIN/CTE/subquery."""
    path = draw(_paths)
    if draw(st.booleans()):
        src_sql = f"{draw(_file_functions)}('{path}')"
    else:
        src_sql = f"'{path}'"
    reader = sqlglot.parse_one(f"SELECT * FROM {src_sql}", dialect=DIALECT)
    table = draw(_tables)
    key = TABLES[table][0]
    place = draw(st.sampled_from(["from", "cte", "derived", "join", "where_in"]))
    if place == "from":
        tree: exp.Expression = reader
    elif place == "cte":
        tree = exp.select("*").from_("f").with_("f", as_=reader)
    elif place == "derived":
        tree = exp.select("*").from_(reader.subquery("f"))
    elif place == "join":
        tree = exp.select("*").from_(exp.table_(table, alias="t")).join(
            reader.subquery("f"),
            on=exp.EQ(this=exp.column(key, table="t"), expression=exp.column(key, table="f")),
        )
    else:
        sub = sqlglot.parse_one(f"SELECT {key} FROM {src_sql}", dialect=DIALECT)
        tree = exp.select("*").from_(table).where(exp.In(this=exp.column(key), query=sub.subquery()))
    return tree.sql(dialect=DIALECT)


@st.composite
def _multi_statement(draw: st.DrawFn) -> str:
    parts = draw(
        st.lists(st.one_of(select_queries().map(lambda s: s.rstrip().rstrip(";")),
                           _root_non_select().map(lambda t: t[0])),
                 min_size=2, max_size=4)
    )
    return draw(st.sampled_from(["; ", ";\n", " ; "])).join(parts) + draw(st.sampled_from(["", ";"]))


@st.composite
def forbidden_statements(draw: st.DrawFn) -> tuple[str, str, str | None]:
    """SQL terlarang → ``(sql, kode error yang diharapkan, kata kunci statement | None)``."""
    category = draw(st.sampled_from(["root", "nested", "multi", "file"]))
    if category == "root":
        sql, keyword = draw(_root_non_select())
        return sql, "NOT_SELECT", keyword
    if category == "nested":
        return draw(_nested_forbidden()), "FORBIDDEN_STATEMENT", None
    if category == "multi":
        return draw(_multi_statement()), "MULTIPLE_STATEMENTS", None
    return draw(_file_source()), "FORBIDDEN_SOURCE", None


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@given(select_queries())
def test_generated_select_queries_are_accepted_and_executed(sql: str) -> None:
    """Feature: dashboard-studio-agent, Property 12: Gerbang statement SQL.

    **Validates: Requirements 10.3, 10.4**
    """
    executor = Mock(return_value="result")
    assert validate_then_execute(sql, executor) == "result"
    executor.assert_called_once_with(sql)

    analysis = analyze_sql(sql, TABLES)
    assert isinstance(analysis, SqlAnalysis)
    assert analysis.tables_used and set(analysis.tables_used) <= set(TABLES)


@given(forbidden_statements())
def test_forbidden_sql_is_rejected_without_execution(case: tuple[str, str, str | None]) -> None:
    """Feature: dashboard-studio-agent, Property 12: Gerbang statement SQL.

    **Validates: Requirements 10.3, 10.4**
    """
    sql, expected_code, keyword = case
    executor = Mock(return_value="result")
    with pytest.raises(StudioError) as info:
        validate_then_execute(sql, executor)
    executor.assert_not_called()

    err = info.value
    assert err.code == expected_code, (sql, err.code, err.message)
    assert err.message  # alasan penolakan selalu disebutkan
    if keyword is not None:
        assert keyword in err.details["statement_type"]
        assert keyword in err.message
    if expected_code == "MULTIPLE_STATEMENTS":
        assert err.details["statement_count"] >= 2
    if expected_code == "FORBIDDEN_SOURCE":
        assert err.details["source"]
