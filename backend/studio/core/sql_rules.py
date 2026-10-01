"""SQL_Validator: aturan statik V1–V6 berbasis sqlglot (Req 7.6, 7.7, 10.3, 10.4, 14.6).

``analyze_sql`` menerapkan aturan berikut secara berurutan dan berhenti pada
pelanggaran pertama dengan melempar :class:`StudioError`:

====  ==========================================================  =======================
#     Aturan                                                      Kode error
====  ==========================================================  =======================
V1    Tepat satu statement non-kosong (``;`` di akhir diizinkan)  ``PARSE_ERROR`` /
                                                                  ``MULTIPLE_STATEMENTS``
V2    Root adalah SELECT, set-operation dari SELECT, atau         ``NOT_SELECT``
      ``WITH ... SELECT``
V3    Tidak ada node DML/DDL/command di mana pun dalam AST        ``FORBIDDEN_STATEMENT``
V4    Tidak ada table function pembaca file / literal path        ``FORBIDDEN_SOURCE``
V5    Semua tabel (di luar nama CTE) terdaftar di Workspace       ``UNKNOWN_TABLE``
V6    JOIN antar-tabel Workspace berbeda hanya melalui            ``UNCONFIRMED_JOIN``
      kesetaraan kolom pada Confirmed_Relation
====  ==========================================================  =======================

V7 (``collect_schema`` Polars) dijalankan oleh ``DataEngine.validate``.

Pilihan dialek: SQL akhirnya dieksekusi oleh ``pl.SQLContext`` yang sintaksnya
mengikuti PostgreSQL/DuckDB (``SELECT * EXCLUDE``, ``read_csv(...)``, dsb.). Parser
sqlglot tidak memiliki dialek Polars, sehingga dipakai dialek ``duckdb``: ia
menerima sintaks Polars yang umum dan mengenali table function pembaca file
(``read_csv``/``read_parquet``/...) serta literal path di ``FROM`` sebagai sumber,
sehingga V4 dapat mendeteksinya. Teks SQL tidak diubah; AST hanya untuk analisis.

Resolusi alias, CTE, dan derived table ke tabel dasar memakai scope sqlglot
(``sqlglot.optimizer.scope``). Lineage kolom output hanya terisi untuk referensi
kolom langsung (menembus alias/CTE/subquery); ekspresi lain bernilai ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import Scope, build_scope

from studio.api.errors import StudioError

__all__ = [
    "DIALECT",
    "ColumnRef",
    "ConfirmedRelation",
    "SqlAnalysis",
    "analyze_sql",
]

#: Dialek parser sqlglot (lihat docstring modul).
DIALECT = "duckdb"

#: Referensi kolom tabel dasar: ``(table, column)``.
ColumnRef = tuple[str, str]


@dataclass(frozen=True)
class ConfirmedRelation:
    """Confirmed_Relation sebagai pasangan kolom tak berurut ``(table_a.column_a ↔ table_b.column_b)``."""

    id: str
    table_a: str
    column_a: str
    table_b: str
    column_b: str


@dataclass(frozen=True)
class SqlAnalysis:
    """Hasil analisis statik SQL yang lolos V1–V6."""

    sql: str
    #: Tabel dasar Workspace yang direferensikan (terurut, unik).
    tables_used: tuple[str, ...]
    #: ID Confirmed_Relation yang dipakai sebagai kondisi JOIN (urutan kemunculan, unik).
    relations_used: tuple[str, ...]
    #: ``{output_col: (table, column) | None}``; nama output pertama yang menang bila duplikat.
    lineage: dict[str, ColumnRef | None] = field(default_factory=dict)
    #: Kolom output posisional ``(nama menurut sqlglot, lineage)``. Nama ekspresi tanpa alias
    #: bisa berbeda dengan nama Polars, sehingga pemanggil dapat men-zip per posisi dengan
    #: ``output_schema`` hasil V7.
    output_columns: tuple[tuple[str, ColumnRef | None], ...] = ()


# ---------------------------------------------------------------------------
# Konstanta aturan
# ---------------------------------------------------------------------------

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Drop", "Create", "Alter", "TruncateTable",
    "Copy", "Command", "Pragma", "Attach", "Detach", "Set", "Use", "Transaction",
    "Commit", "Rollback", "Grant", "Revoke", "Analyze", "Install", "LoadData",
    "Cache", "Uncache", "Refresh", "Kill", "Export", "Into", "Describe", "Show",
)
_FORBIDDEN_NODE_TYPES: tuple[type[exp.Expression], ...] = tuple(
    t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NODE_NAMES) if isinstance(t, type)
)

_STATEMENT_NAMES = {
    "truncatetable": "TRUNCATE",
    "into": "SELECT INTO",
    "loaddata": "LOAD DATA",
    "transaction": "BEGIN",
}

_FILE_FUNCTION_NAMES = frozenset(
    {"parquet_scan", "parquet_metadata", "parquet_schema", "sniff_csv", "glob", "json_scan"}
)
_FILE_FUNCTION_PREFIXES = ("read_", "scan_")

_PATH_CHARS = re.compile(r"[/\\:*?~]")
_FILE_EXTENSION = re.compile(
    r"\.(csv|tsv|txt|parquet|pq|json|jsonl|ndjson|ipc|arrow|feather|avro|orc|xlsx|xlsm|xls|ods"
    r"|db|sqlite|duckdb|gz|zst)$",
    re.IGNORECASE,
)

_SET_OPERATION = getattr(exp, "SetOperation", (exp.Union, exp.Intersect, exp.Except))


# ---------------------------------------------------------------------------
# Katalog tabel/kolom Workspace
# ---------------------------------------------------------------------------


def _column_names(columns: Any) -> list[str]:
    """Terima list nama, list objek ber-``name`` (mis. ColumnInfo), atau mapping/schema."""
    if columns is None:
        return []
    if isinstance(columns, Mapping):
        return [str(k) for k in columns.keys()]
    names: list[str] = []
    for item in columns:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping) and "name" in item:
            names.append(str(item["name"]))
        elif hasattr(item, "name"):
            names.append(str(item.name))
        else:
            names.append(str(item))
    return names


def _unique_ci(candidates: Iterable[str], name: str) -> str | None:
    """Cocokkan persis dulu, lalu case-insensitive bila hasilnya tunggal."""
    pool = list(candidates)
    if name in pool:
        return name
    lowered = [c for c in pool if c.lower() == name.lower()]
    return lowered[0] if len(lowered) == 1 else None


class _Catalog:
    def __init__(self, tables: Mapping[str, Any]) -> None:
        self.columns: dict[str, list[str]] = {
            str(name): _column_names(cols) for name, cols in (tables or {}).items()
        }

    def table(self, name: str) -> str | None:
        return _unique_ci(self.columns.keys(), name)

    def column(self, table: str, name: str) -> str | None:
        return _unique_ci(self.columns.get(table, []), name)


def _rel_attr(rel: Any, key: str) -> str:
    value = rel.get(key) if isinstance(rel, Mapping) else getattr(rel, key)
    return str(value)


# ---------------------------------------------------------------------------
# Helper AST
# ---------------------------------------------------------------------------


def _strip_paren(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    node = _strip_paren(node)
    if isinstance(node, exp.And):
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


def _statement_kind(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        return str(node.this).upper()
    return _STATEMENT_NAMES.get(node.key, node.key.upper())


def _non_select_part(node: exp.Expression) -> exp.Expression | None:
    """Node pertama yang bukan query SELECT pada root (None bila root valid)."""
    if isinstance(node, exp.Subquery):
        return _non_select_part(node.this)
    if isinstance(node, exp.Select):
        return None
    if isinstance(node, _SET_OPERATION):
        return _non_select_part(node.left) or _non_select_part(node.right)
    return node


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _is_file_function(node: exp.Expression) -> bool:
    if not isinstance(node, exp.Func) or isinstance(node, exp.Glob):
        return False
    name = _function_name(node)
    return name in _FILE_FUNCTION_NAMES or name.startswith(_FILE_FUNCTION_PREFIXES)


def _table_full_name(table: exp.Table) -> str:
    return ".".join(p for p in (table.catalog, table.db, table.name) if p)


def _looks_like_path(name: str) -> bool:
    return bool(_PATH_CHARS.search(name) or _FILE_EXTENSION.search(name) or name.startswith("."))


def _fmt(ref: ColumnRef) -> str:
    return f"{ref[0]}.{ref[1]}"


_MISSING = object()


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class _Analyzer:
    def __init__(self, sql: str, tables: Mapping[str, Any], relations: Iterable[Any]) -> None:
        self.sql = sql
        self.catalog = _Catalog(tables)
        self.relations: dict[frozenset[ColumnRef], str] = {}
        self.relation_labels: list[str] = []
        for rel in relations or ():
            a = self._canon_ref(_rel_attr(rel, "table_a"), _rel_attr(rel, "column_a"))
            b = self._canon_ref(_rel_attr(rel, "table_b"), _rel_attr(rel, "column_b"))
            self.relations.setdefault(frozenset({a, b}), _rel_attr(rel, "id"))
            self.relation_labels.append(f"{_fmt(a)} = {_fmt(b)}")
        self._outputs_memo: dict[int, list[tuple[str, ColumnRef | None]]] = {}
        self._base_memo: dict[int, frozenset[str]] = {}
        self._in_progress: set[tuple[str, int]] = set()

    def _canon_ref(self, table: str, column: str) -> ColumnRef:
        t = self.catalog.table(table) or table
        return (t, self.catalog.column(t, column) or column)

    # -- V1 ---------------------------------------------------------------
    def parse(self) -> exp.Expression:
        if not isinstance(self.sql, str) or not self.sql.strip():
            raise StudioError("PARSE_ERROR", "SQL kosong.")
        try:
            statements = sqlglot.parse(self.sql, dialect=DIALECT)
        except ParseError as e:
            errors = [
                {"description": err.get("description"), "line": err.get("line"), "col": err.get("col")}
                for err in e.errors
            ]
            first = errors[0] if errors else {"description": str(e)}
            where = f" (baris {first.get('line')}, kolom {first.get('col')})" if first.get("line") else ""
            raise StudioError(
                "PARSE_ERROR", f"SQL tidak dapat di-parse: {first.get('description')}{where}.", {"errors": errors}
            ) from e
        except (SqlglotError, RecursionError, ValueError) as e:
            raise StudioError("PARSE_ERROR", f"SQL tidak dapat di-parse: {e}") from e
        stmts = [s for s in statements if s is not None]
        if not stmts:
            raise StudioError("PARSE_ERROR", "SQL tidak berisi statement.")
        if len(stmts) > 1:
            raise StudioError(
                "MULTIPLE_STATEMENTS",
                f"Hanya satu statement SQL yang diizinkan; ditemukan {len(stmts)} statement.",
                {"statement_count": len(stmts)},
            )
        return stmts[0]

    # -- V2 & V3 ----------------------------------------------------------
    @staticmethod
    def check_statement(root: exp.Expression) -> None:
        bad = _non_select_part(root)
        if bad is not None:
            kind = _statement_kind(bad)
            raise StudioError(
                "NOT_SELECT",
                f"Hanya statement SELECT (termasuk WITH ... SELECT) yang diizinkan; ditemukan statement {kind}.",
                {"statement_type": kind},
            )
        for node in root.walk():
            if isinstance(node, _FORBIDDEN_NODE_TYPES):
                kind = _statement_kind(node)
                raise StudioError(
                    "FORBIDDEN_STATEMENT",
                    f"SQL mengandung statement terlarang {kind}; hanya query SELECT baca-saja yang diizinkan.",
                    {"statement_type": kind},
                )

    # -- V4 ---------------------------------------------------------------
    def check_sources(self, root: exp.Expression) -> None:
        for node in root.walk():
            if _is_file_function(node):
                self._forbidden_source(_function_name(node))
        for container in root.find_all(exp.From, exp.Join):
            self._check_source_node(container.this)

    def _check_source_node(self, src: exp.Expression | None) -> None:
        # Subquery/UNNEST/VALUES/LATERAL tidak membaca file; isinya sudah diperiksa oleh walk di atas.
        if src is None or isinstance(src, (exp.Subquery, exp.Unnest, exp.Values, exp.Lateral)):
            return
        if isinstance(src, exp.Table):
            if not isinstance(src.this, exp.Identifier):
                inner = src.this
                name = _function_name(inner) if isinstance(inner, exp.Func) else inner.sql(dialect=DIALECT)
                self._forbidden_source(name)
            full = _table_full_name(src)
            if self.catalog.table(full) is None and _looks_like_path(full):
                self._forbidden_source(full, path=True)
            return
        name = _function_name(src) if isinstance(src, exp.Func) else src.sql(dialect=DIALECT)
        self._forbidden_source(name)

    @staticmethod
    def _forbidden_source(name: str, path: bool = False) -> None:
        what = f"literal path '{name}'" if path else f"table function {name}"
        raise StudioError(
            "FORBIDDEN_SOURCE",
            f"SQL tidak boleh membaca file atau memakai {what} sebagai sumber; gunakan tabel Workspace.",
            {"source": name},
        )

    # -- V5 ---------------------------------------------------------------
    def check_tables(self, root: exp.Expression, scope: Scope) -> tuple[str, ...]:
        used: set[str] = set()
        unknown: list[str] = []
        covered: set[int] = set()

        def register(table: exp.Table) -> None:
            full = _table_full_name(table)
            canon = self.catalog.table(full)
            if canon is not None:
                used.add(canon)
            elif full not in unknown:
                unknown.append(full)

        for s in scope.traverse():
            for src in s.sources.values():
                if isinstance(src, exp.Table) and id(src) not in covered:
                    covered.add(id(src))
                    register(src)
        # Jaring pengaman: node Table yang tidak tercatat sebagai source scope.
        cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
        for table in root.find_all(exp.Table):
            if id(table) in covered:
                continue
            if not table.db and not table.catalog and table.name.lower() in cte_names:
                continue
            register(table)

        if unknown:
            available = sorted(self.catalog.columns)
            raise StudioError(
                "UNKNOWN_TABLE",
                f"Tabel tidak terdaftar di Workspace: {', '.join(unknown)}. "
                f"Tabel tersedia: {', '.join(available) or '(tidak ada)'}.",
                {"unknown_tables": unknown, "available_tables": available},
            )
        return tuple(sorted(used))

    # -- Resolusi scope ---------------------------------------------------
    def _table_of(self, table: exp.Table) -> str | None:
        return self.catalog.table(_table_full_name(table))

    @staticmethod
    def _entries(scope: Scope) -> list[tuple[str, Any]]:
        """Sumber FROM/JOIN berurutan: ``(alias, source)`` dengan source Table | Scope | None."""
        sel = scope.expression
        if not isinstance(sel, exp.Select):
            return []
        by_node = {id(node): (alias, src) for alias, (node, src) in scope.selected_sources.items()}
        nodes: list[exp.Expression] = []
        from_ = sel.args.get("from_") or sel.args.get("from")
        if from_ is not None:
            nodes.append(from_.this)
        nodes.extend(j.this for j in sel.args.get("joins") or [])
        entries: list[tuple[str, Any]] = []
        for n in nodes:
            hit = by_node.get(id(n))
            if hit is None and isinstance(n, exp.Subquery):
                hit = by_node.get(id(n.this))  # derived table: scope mencatat Select di dalamnya
            if hit is None:
                alias = n.alias_or_name
                node_src = scope.selected_sources.get(alias)
                hit = (alias, node_src[1] if node_src else None)
            entries.append(hit)
        return entries

    def _base_tables(self, src: Any) -> frozenset[str]:
        if isinstance(src, exp.Table):
            t = self._table_of(src)
            return frozenset({t}) if t else frozenset()
        if not isinstance(src, Scope):
            return frozenset()
        key = id(src)
        if key in self._base_memo:
            return self._base_memo[key]
        if ("base", key) in self._in_progress:  # CTE rekursif
            return frozenset()
        self._in_progress.add(("base", key))
        result: set[str] = set()
        for s in src.traverse():
            for _node, inner in s.selected_sources.values():
                if inner is not src:
                    result |= self._base_tables(inner)
        self._in_progress.discard(("base", key))
        self._base_memo[key] = frozenset(result)
        return self._base_memo[key]

    def _source_columns(self, src: Any) -> list[tuple[str, ColumnRef | None]]:
        if isinstance(src, exp.Table):
            t = self._table_of(src)
            return [(c, (t, c)) for c in self.catalog.columns.get(t, [])] if t else []
        if isinstance(src, Scope):
            return self.outputs(src)
        return []

    def _lineage_in_source(self, src: Any, name: str) -> ColumnRef | None:
        if isinstance(src, exp.Table):
            t = self._table_of(src)
            c = self.catalog.column(t, name) if t else None
            return (t, c) if t and c else None
        if isinstance(src, Scope):
            outs = self.outputs(src)
            match = _unique_ci([n for n, _ in outs], name)
            if match is not None:
                return next(lin for n, lin in outs if n == match)
        return None

    def _has_column(self, src: Any, name: str) -> bool:
        if isinstance(src, exp.Table):
            t = self._table_of(src)
            return bool(t) and self.catalog.column(t, name) is not None
        if isinstance(src, Scope):
            return any(n.lower() == name.lower() for n, _ in self.outputs(src))
        return False

    def _locate(
        self, scope: Scope, col: exp.Column, entries: list[tuple[str, Any]]
    ) -> tuple[int | None, Any]:
        """Cari source kolom: ``(indeks di entries | None, source | _MISSING)``."""
        qual = col.table
        if qual:
            for i, (alias, src) in enumerate(entries):
                if alias.lower() == qual.lower():
                    return i, src
            parent = scope.parent  # referensi berkorelasi ke scope luar
            while parent is not None:
                for alias, (_node, src) in parent.selected_sources.items():
                    if alias.lower() == qual.lower():
                        return None, src
                parent = parent.parent
            return None, _MISSING
        matches = [i for i, (_a, src) in enumerate(entries) if self._has_column(src, col.name)]
        if len(matches) == 1:
            return matches[0], entries[matches[0]][1]
        if not matches and len(entries) == 1:
            return 0, entries[0][1]
        return None, _MISSING

    def _resolve(
        self, scope: Scope, col: exp.Column, entries: list[tuple[str, Any]] | None = None
    ) -> ColumnRef | None:
        _idx, src = self._locate(scope, col, self._entries(scope) if entries is None else entries)
        return None if src is _MISSING else self._lineage_in_source(src, col.name)

    def outputs(self, scope: Scope) -> list[tuple[str, ColumnRef | None]]:
        key = id(scope)
        if key in self._outputs_memo:
            return self._outputs_memo[key]
        if ("out", key) in self._in_progress:  # CTE rekursif
            return []
        self._in_progress.add(("out", key))
        expr = scope.expression
        result: list[tuple[str, ColumnRef | None]] = []
        if scope.set_operation_scopes:
            branches = [self.outputs(s) for s in scope.set_operation_scopes]
            first = branches[0]
            for i, (name, lin) in enumerate(first):
                same = all(len(b) > i and b[i][1] == lin for b in branches[1:])
                result.append((name, lin if same else None))
        elif isinstance(expr, exp.Select):
            result = self._select_outputs(scope, expr)
        elif isinstance(expr, exp.Subquery) and scope.derived_table_scopes:
            result = self.outputs(scope.derived_table_scopes[0])  # root "(SELECT ...) ORDER BY ..."
        self._in_progress.discard(("out", key))
        self._outputs_memo[key] = result
        return result

    def _select_outputs(self, scope: Scope, sel: exp.Select) -> list[tuple[str, ColumnRef | None]]:
        entries = self._entries(scope)
        out: list[tuple[str, ColumnRef | None]] = []
        for proj in sel.expressions:
            if isinstance(proj, exp.Star):
                out.extend(self._expand_star(entries, proj, None))
            elif isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star):
                out.extend(self._expand_star(entries, proj.this, proj.table))
            else:
                inner = _strip_paren(proj.this if isinstance(proj, exp.Alias) else proj)
                lin = self._resolve(scope, inner, entries) if isinstance(inner, exp.Column) else None
                out.append((proj.alias_or_name, lin))
        return out

    def _expand_star(
        self, entries: list[tuple[str, Any]], star: exp.Star, qualifier: str | None
    ) -> list[tuple[str, ColumnRef | None]]:
        excluded = {
            e.name.lower() for e in (star.args.get("except_") or star.args.get("except") or [])
        }
        replaced = {
            e.alias_or_name.lower() for e in (star.args.get("replace_") or star.args.get("replace") or [])
        }
        targets = entries if not qualifier else [e for e in entries if e[0].lower() == qualifier.lower()]
        out: list[tuple[str, ColumnRef | None]] = []
        for _alias, src in targets:
            for name, lin in self._source_columns(src):
                if name.lower() in excluded:
                    continue
                out.append((name, None if name.lower() in replaced else lin))
        return out

    # -- V6 ---------------------------------------------------------------
    def check_joins(self, scope: Scope) -> tuple[str, ...]:
        violations: list[dict[str, Any]] = []
        used: list[str] = []
        for s in scope.traverse():
            sel = s.expression
            joins = sel.args.get("joins") if isinstance(sel, exp.Select) else None
            if not joins:
                continue
            entries = self._entries(s)
            for i, join in enumerate(joins):
                if i + 1 >= len(entries):
                    break
                self._check_join(s, join, entries[: i + 2], violations, used)

        if violations:
            pairs = [[v["left"], v["right"]] for v in violations if "left" in v and "right" in v]
            raise StudioError(
                "UNCONFIRMED_JOIN",
                "JOIN antar-Dataset hanya boleh memakai Confirmed_Relation. "
                + " ".join(v["message"] for v in violations),
                {
                    "unconfirmed_pairs": pairs,
                    "violations": violations,
                    "confirmed_relations": list(self.relation_labels),
                },
            )
        return tuple(dict.fromkeys(used))

    def _check_join(
        self,
        scope: Scope,
        join: exp.Join,
        entries: list[tuple[str, Any]],
        violations: list[dict[str, Any]],
        used: list[str],
    ) -> None:
        right_idx = len(entries) - 1
        left_base: set[str] = set()
        for _alias, src in entries[:right_idx]:
            left_base |= self._base_tables(src)
        right_alias, right_src = entries[right_idx]
        right_base = self._base_tables(right_src)
        resolved = all(isinstance(src, (exp.Table, Scope)) for _a, src in entries)
        if resolved and len(left_base | right_base) <= 1:
            return  # self-join atau tanpa tabel dasar: bukan JOIN antar-Dataset
        # Source yang tidak ter-resolve diperlakukan konservatif: kondisi JOIN tetap diperiksa.
        left_label = ", ".join(sorted(left_base)) or "(subquery)"
        right_label = ", ".join(sorted(right_base)) or right_alias or "(subquery)"
        before = len(violations)

        method = str(join.args.get("method") or "").upper()
        using = join.args.get("using")
        on = join.args.get("on")
        if method == "NATURAL":
            violations.append({
                "reason": "natural_join",
                "tables": [left_label, right_label],
                "message": f"NATURAL JOIN antara {left_label} dan {right_label} tidak diizinkan; "
                "gunakan ON dengan kolom Confirmed_Relation.",
            })
            return
        if using:
            for ident in using:
                name = ident.name
                right_ref = self._lineage_in_source(right_src, name)
                left_ref = next(
                    (self._lineage_in_source(src, name) for _a, src in entries[:right_idx] if self._has_column(src, name)),
                    None,
                )
                self._check_pair(
                    left_ref, right_ref, f"{left_label}.{name}", f"{right_label}.{name}", violations, used
                )
            return
        if on is None:
            violations.append({
                "reason": "cross_join",
                "tables": [left_label, right_label],
                "message": f"CROSS JOIN/comma join antara {left_label} dan {right_label} tidak diizinkan; "
                "gunakan JOIN ... ON dengan kolom Confirmed_Relation.",
            })
            return

        connected = False
        for term in _conjuncts(on):
            term = _strip_paren(term)
            sides = (
                (_strip_paren(term.left), _strip_paren(term.right)) if isinstance(term, exp.EQ) else None
            )
            if sides is None or not all(isinstance(c, exp.Column) for c in sides):
                violations.append({
                    "reason": "non_equality",
                    "condition": term.sql(dialect=DIALECT),
                    "message": f"Kondisi JOIN '{term.sql(dialect=DIALECT)}' bukan kesetaraan kolom; "
                    "JOIN antar-Dataset harus berupa konjungsi kesetaraan kolom Confirmed_Relation.",
                })
                continue
            a, b = sides
            ia, src_a = self._locate(scope, a, entries)
            ib, src_b = self._locate(scope, b, entries)
            ref_a = None if src_a is _MISSING else self._lineage_in_source(src_a, a.name)
            ref_b = None if src_b is _MISSING else self._lineage_in_source(src_b, b.name)
            if self._check_pair(ref_a, ref_b, a.sql(dialect=DIALECT), b.sql(dialect=DIALECT), violations, used):
                if ia is not None and ib is not None and right_idx in (ia, ib) and ia != ib:
                    connected = True
        if not connected and len(violations) == before:
            violations.append({
                "reason": "no_join_equality",
                "tables": [left_label, right_label],
                "message": f"JOIN antara {left_label} dan {right_label} tidak memiliki kesetaraan kolom "
                "yang menghubungkan kedua sisi.",
            })

    def _check_pair(
        self,
        ref_a: ColumnRef | None,
        ref_b: ColumnRef | None,
        label_a: str,
        label_b: str,
        violations: list[dict[str, Any]],
        used: list[str],
    ) -> bool:
        if ref_a is None or ref_b is None:
            left = _fmt(ref_a) if ref_a else label_a
            right = _fmt(ref_b) if ref_b else label_b
            violations.append({
                "reason": "unresolved_column",
                "left": left,
                "right": right,
                "message": f"Pasangan kolom {left} = {right} tidak dapat di-resolve ke kolom tabel dasar "
                "sehingga tidak dapat dicocokkan dengan Confirmed_Relation.",
            })
            return False
        if ref_a[0] == ref_b[0]:
            return True  # kesetaraan dalam tabel yang sama (self-join)
        rel_id = self.relations.get(frozenset({ref_a, ref_b}))
        if rel_id is None:
            violations.append({
                "reason": "unconfirmed",
                "left": _fmt(ref_a),
                "right": _fmt(ref_b),
                "message": f"Pasangan kolom {_fmt(ref_a)} = {_fmt(ref_b)} belum dikonfirmasi sebagai relasi.",
            })
            return False
        used.append(rel_id)
        return True


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------


def analyze_sql(
    sql: str,
    tables: Mapping[str, Any],
    confirmed_relations: Iterable[ConfirmedRelation | Any] = (),
) -> SqlAnalysis:
    """Validasi statik SQL (V1–V6) dan hitung tabel, relasi, serta lineage kolom output.

    ``tables``: ``{table_name: kolom}`` dengan kolom berupa list nama, list objek
    ber-atribut ``name``, atau mapping/schema ``{kolom: tipe}``.
    ``confirmed_relations``: :class:`ConfirmedRelation` atau objek/mapping dengan
    field ``id, table_a, column_a, table_b, column_b``.

    Melempar :class:`StudioError` dengan kode sesuai aturan pertama yang dilanggar.
    """
    analyzer = _Analyzer(sql, tables, confirmed_relations)
    root = analyzer.parse()                     # V1
    analyzer.check_statement(root)              # V2, V3
    analyzer.check_sources(root)                # V4
    try:
        scope = build_scope(root)
    except (SqlglotError, RecursionError) as e:
        raise StudioError("PARSE_ERROR", f"Struktur query tidak dapat dianalisis: {e}") from e
    if scope is None:
        raise StudioError("PARSE_ERROR", "Struktur query tidak dapat dianalisis.")
    tables_used = analyzer.check_tables(root, scope)   # V5
    relations_used = analyzer.check_joins(scope)       # V6

    output_columns = tuple(analyzer.outputs(scope))
    lineage: dict[str, ColumnRef | None] = {}
    for name, lin in output_columns:
        if name and name not in lineage:
            lineage[name] = lin
    return SqlAnalysis(
        sql=sql,
        tables_used=tables_used,
        relations_used=relations_used,
        lineage=lineage,
        output_columns=output_columns,
    )
