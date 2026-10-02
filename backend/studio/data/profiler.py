"""Profiling Dataset & deteksi Relation_Candidate (Req 6.1–6.5, 7.1, 7.2, 7.5).

Fungsi murni (sinkron, di atas ``pl.LazyFrame``)::

    result = compute_column_profiles(pl.scan_parquet(path), dataset.schema)
    result.columns   # list[ColumnProfile] (role sudah diklasifikasi)
    result.quality   # {duplicate_rows, mixed_type_columns[], null_pct{col: pct}}
    roles = classify_roles(schema, result.columns, result.row_count)
    stats = compute_overlap(lf_a, "customer_id", lf_b, "customer_id")  # OverlapStats

Aturan statistik (deterministik): satu query agregat lazy per Dataset kecil; Dataset
besar (> ``PROFILE_HEAVY_MIN_ROWS`` baris) diagregasi berurutan per kolom dengan engine
streaming agar puncak memori terikat (Req 5.4, lihat ``_profile_row_heavy``):

- ``null_count`` / ``null_pct`` (``null_count / row_count × 100``; 0 bila tanpa baris).
- ``distinct_count``: jumlah nilai distinct **non-null**.
- ``min``/``max``: hanya kolom numerik (integer/float) dan date/datetime (string ISO-8601);
  ``mean``: hanya numerik. Nilai float non-finite (NaN/inf) dilaporkan ``None``.
- ``top_values``: ≤ 5 pasangan ``[nilai, jumlah]`` nilai non-null, urut jumlah menurun
  lalu nilai menaik (urutan alami Polars untuk tipe kolom tsb.); NaN dikecualikan.
  Nilai date/datetime diserialisasi ke string ISO.
- Baris duplikat: ``row_count - n_unique(struct(semua kolom))`` (Req 6.2).
- Tipe campuran (Req 6.2): kolom bertipe logis ``string`` yang porsi nilai non-null
  "bertipe" — setelah ``strip`` dapat di-cast ke ``Float64`` (cast non-strict Polars)
  **atau** di-parse sebagai tanggal ISO ``YYYY-MM-DD`` — berada di antara (0, 1)
  eksklusif. Kolom string yang seluruhnya angka/tanggal atau seluruhnya teks tidak
  dianggap campuran.

Klasifikasi peran (``classify_roles``, Req 6.3), dievaluasi berurutan:

1. ``time``: tipe ``date``/``datetime``.
2. ``identifier``: nama (dinormalisasi snake_case) cocok
   ``^id$|_id$|^id_|uuid|kode|_code$`` untuk tipe non-boolean, **atau** tipe
   integer/string dengan ``distinct_count / row_count ≥ 0,95`` (hanya bila
   ``row_count ≥ 2`` agar tabel satu baris tidak menjadikan semua kolom identifier).
3. ``measure``: numerik (integer/float) non-identifier.
4. ``dimension``: lainnya (string, boolean).

``Profiler`` menjalankan fungsi di atas di thread (``asyncio.to_thread``; Polars
``collect`` bersifat sinkron) lalu menyimpan hasil ke ``dataset_profiles`` dan
meng-upsert Relation_Candidate. Pasang ke Ingestion_Service lewat komposisi::

    profiler = Profiler(repos, bus, engine)          # atau data_dir
    ingestion = IngestionService(repos, bus, data_dir,
                                 on_dataset_registered=profiler.on_dataset_registered)

Event workspace: ``dataset.profiled {dataset_id}`` setelah profil tersimpan, lalu
``relation.updated {relation_ids: [...]}`` bila ada kandidat yang di-upsert.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import polars as pl

from studio.core.identifiers import safe_join
from studio.core.models import (
    MAX_TOP_VALUES,
    ColumnInfo,
    ColumnProfile,
    ColumnRole,
    LogicalType,
    Scalar,
)
from studio.core.relations import (
    ColumnRef,
    OverlapStats,
    TableInfo,
    candidate_key,
    candidate_pairs,
    normalize_name,
    score_candidates,
)
from studio.events.bus import EventBus
from studio.store.repos import (
    DatasetProfileRecord,
    DatasetRecord,
    RelationRecord,
    Repositories,
)

if TYPE_CHECKING:
    from studio.data.engine import DataEngine

__all__ = [
    "IDENTIFIER_NAME_PATTERN",
    "PROFILE_HEAVY_MIN_ROWS",
    "PROFILE_TOP_VALUES_SAMPLE",
    "UNIQUE_RATIO_THRESHOLD",
    "ProfileResult",
    "Profiler",
    "classify_roles",
    "compute_column_profiles",
    "compute_overlap",
]

logger = logging.getLogger(__name__)

_NUMERIC_TYPES: frozenset[str] = frozenset({"integer", "float"})
_TEMPORAL_TYPES: frozenset[str] = frozenset({"date", "datetime"})
_ISO_DATE_FORMAT = "%Y-%m-%d"

#: Pola nama kolom identifier (dicocokkan pada nama hasil ``normalize_name``).
IDENTIFIER_NAME_PATTERN = re.compile(r"^id$|_id$|^id_|uuid|kode|_code$")
#: Rasio ``distinct_count / row_count`` minimal agar integer/string dianggap identifier.
UNIQUE_RATIO_THRESHOLD = 0.95
_MIN_ROWS_FOR_UNIQUE_RULE = 2

#: Profiling paralel maksimum per ``Profiler`` (membatasi memori/CPU).
DEFAULT_MAX_CONCURRENT_PROFILES = 2

#: Di atas jumlah baris ini profil beralih ke jalur hemat memori (Req 5.4):
#: agregasi dieksekusi berurutan (bukan satu query) sehingga hanya satu hash
#: table aktif pada satu waktu. Pilihan desain: satu query gabungan eksak
#: membuat hash table seluruh kolom hidup bersamaan dan meledak untuk Dataset
#: 10 juta baris; lihat ``_profile_row_heavy``.
PROFILE_HEAVY_MIN_ROWS = 2_000_000

#: Ukuran sampel awal deterministik untuk ``top_values`` kolom non-unik pada
#: jalur hemat memori (tabel frekuensi penuh tidak masuk akal untuk kolom
#: dengan puluhan juta nilai unik).
PROFILE_TOP_VALUES_SAMPLE = 1_000_000


# ---------------------------------------------------------------------------
# Hasil
# ---------------------------------------------------------------------------


class ProfileResult(NamedTuple):
    """Output ``compute_column_profiles``."""

    #: Satu Column_Profile per kolom skema (urutan skema), role sudah terisi.
    columns: list[ColumnProfile]
    #: ``{duplicate_rows, mixed_type_columns[], null_pct{col: pct}}``
    quality: dict[str, Any]
    row_count: int


# ---------------------------------------------------------------------------
# Helper nilai
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Scalar:
    """Nilai Polars → ``Scalar`` JSON (tanggal → ISO, non-finite → None)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        as_float = float(value)
        return as_float if math.isfinite(as_float) else None
    if isinstance(value, timedelta):
        return str(value)
    return str(value)


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    as_float = float(value)
    return as_float if math.isfinite(as_float) else None


def _count_field(column: str) -> str:
    name = "count"
    while name == column:
        name += "_"
    return name


# ---------------------------------------------------------------------------
# Klasifikasi peran
# ---------------------------------------------------------------------------


def _role_for(col_type: LogicalType, name: str, distinct: int, row_count: int) -> ColumnRole:
    if col_type in _TEMPORAL_TYPES:
        return "time"
    if col_type != "boolean" and IDENTIFIER_NAME_PATTERN.search(normalize_name(name)):
        return "identifier"
    if (
        col_type in ("integer", "string")
        and row_count >= _MIN_ROWS_FOR_UNIQUE_RULE
        and distinct >= UNIQUE_RATIO_THRESHOLD * row_count
    ):
        return "identifier"
    if col_type in _NUMERIC_TYPES:
        return "measure"
    return "dimension"


def classify_roles(
    schema: Sequence[ColumnInfo],
    profiles: Sequence[ColumnProfile] | Mapping[str, ColumnProfile],
    row_count: int,
) -> dict[str, ColumnRole]:
    """Peran tiap kolom skema: tepat satu dari {dimension, measure, time, identifier}.

    ``profiles`` (list atau ``{nama: ColumnProfile}``) menyediakan ``distinct_count``;
    kolom tanpa profil dianggap ``distinct_count = 0``. Fungsi murni dan deterministik.
    """
    pmap = profiles if isinstance(profiles, Mapping) else {p.name: p for p in profiles}
    roles: dict[str, ColumnRole] = {}
    for col in schema:
        prof = pmap.get(col.name)
        distinct = prof.distinct_count if prof is not None else 0
        roles[col.name] = _role_for(col.type, col.name, distinct, row_count)
    return roles


# ---------------------------------------------------------------------------
# Profil kolom
# ---------------------------------------------------------------------------


def _schema_of(lf: pl.LazyFrame) -> list[ColumnInfo]:
    from studio.data.engine import logical_type_of_dtype  # impor tunda: engine berat

    return [
        ColumnInfo(name=name, type=logical_type_of_dtype(dtype))
        for name, dtype in lf.collect_schema().items()
    ]


def _cheap_exprs(i: int, col: ColumnInfo) -> list[pl.Expr]:
    """Agregat murah tanpa hash per nilai: null, min/max, mean, deteksi tipe campuran."""
    c = pl.col(col.name)
    exprs = [c.null_count().alias(f"n{i}")]
    if col.type in _NUMERIC_TYPES or col.type in _TEMPORAL_TYPES:
        exprs += [c.min().alias(f"lo{i}"), c.max().alias(f"hi{i}")]
    if col.type in _NUMERIC_TYPES:
        exprs.append(c.cast(pl.Float64).mean().alias(f"m{i}"))
    if col.type == "string":
        s = c.cast(pl.String).str.strip_chars()
        typed = s.cast(pl.Float64, strict=False).is_not_null() | s.str.to_date(
            _ISO_DATE_FORMAT, strict=False
        ).is_not_null()
        exprs.append(typed.sum().alias(f"x{i}"))
    return exprs


def _values_expr(col: ColumnInfo) -> pl.Expr:
    """Kolom non-null siap dihitung (NaN dibuang untuk float, lihat serialisasi JSON)."""
    values = pl.col(col.name).drop_nulls()
    if col.type == "float":
        values = values.cast(pl.Float64).drop_nans()  # NaN tidak dapat diserialisasi ke JSON
    return values


def _top_values_expr(col: ColumnInfo) -> pl.Expr:
    """``top_values`` eksak: value_counts, urut jumlah menurun lalu nilai menaik, 5 teratas."""
    cnt = _count_field(col.name)
    vc = _values_expr(col).value_counts(name=cnt)
    return (
        vc.sort_by(
            [vc.struct.field(cnt), vc.struct.field(col.name)],
            descending=[True, False],
            maintain_order=True,
        )
        .head(MAX_TOP_VALUES)
        .implode()
    )


def _column_exprs(i: int, col: ColumnInfo) -> list[pl.Expr]:
    """Ekspresi profil satu kolom untuk query gabungan jalur dataset kecil."""
    return [
        *_cheap_exprs(i, col),
        # distinct_count menghitung NaN sebagai nilai (hanya null yang dibuang);
        # drop_nans hanya berlaku untuk top_values (serialisasi JSON).
        pl.col(col.name).drop_nulls().n_unique().alias(f"d{i}"),
        _top_values_expr(col).alias(f"t{i}"),
    ]


def _row_count(lf: pl.LazyFrame) -> int:
    return int(lf.select(pl.len()).collect(engine="streaming").item())


def _distinct_count(lf: pl.LazyFrame, col: ColumnInfo) -> int:
    """``distinct_count`` kolom sebagai query sendiri agar hash table antar kolom tidak menumpuk.

    NaN dihitung sebagai nilai (konsisten dengan jalur dataset kecil).
    """
    return int(lf.select(pl.col(col.name).drop_nulls().n_unique()).collect(engine="streaming").item())


def _top_values_of(
    lf: pl.LazyFrame, col: ColumnInfo, distinct: int, row_count: int, null_count: int
) -> list[dict[str, Any]]:
    """``top_values`` kolom pada jalur hemat memori.

    Kolom yang seluruh nilainya unik (``distinct == row_count - null``): tiap
    jumlah pasti 1 sehingga 5 teratas = 5 nilai terkecil, dihitung eksak via
    ``bottom_k`` tanpa tabel frekuensi. Selain itu: ``value_counts`` atas sampel
    awal deterministik baris (perkiraan; tabel frekuensi penuh untuk kolom
    dengan puluhan juta nilai unik tidak masuk akal di memori).
    """
    cnt = _count_field(col.name)
    if distinct and distinct == row_count - null_count:
        values = (
            lf.select(_values_expr(col).bottom_k(MAX_TOP_VALUES).implode())
            .collect(engine="streaming")
            .item()
        )
        found = list(values) if values is not None else []
        return [{col.name: value, cnt: 1} for value in sorted(found)]
    # Sampel ≤ PROFILE_TOP_VALUES_SAMPLE baris: default engine agar urutan
    # sort_by deterministik (streaming salah mengurutkan multi-ekspresi).
    sampled = lf.select(pl.col(col.name)).head(PROFILE_TOP_VALUES_SAMPLE)
    out = sampled.select(_top_values_expr(col).alias("__top")).collect().row(0, named=True)
    return list(out["__top"] or [])


def _profile_row_heavy(lf: pl.LazyFrame, cols: list[ColumnInfo], row_count: int) -> dict[str, Any]:
    """Agregat profil Dataset besar (> :data:`PROFILE_HEAVY_MIN_ROWS` baris) berurutan.

    Bentuk hasil mengikuti query gabungan jalur kecil (``n/lo/hi/m/x/d/t{i}``,
    ``__rows``, ``__unique_rows``) sehingga pemakaian di
    :func:`compute_column_profiles` identik. Hanya satu hash table aktif pada
    satu waktu; baris duplikat memakai hash 64-bit (perkiraan: peluang tabrakan
    pasangan untuk 10 juta baris ~ 3e-6).
    """
    row: dict[str, Any] = {"__rows": row_count}
    cheap: list[pl.Expr] = []
    for i, col in enumerate(cols):
        cheap += _cheap_exprs(i, col)
    if cheap:
        row.update(lf.select(cheap).collect(engine="streaming").row(0, named=True))
    for i, col in enumerate(cols):
        nulls = int(row.get(f"n{i}", 0) or 0)
        distinct = _distinct_count(lf, col)
        row[f"d{i}"] = distinct
        row[f"t{i}"] = _top_values_of(lf, col, distinct, row_count, nulls)
    if cols and row_count:
        hashed = (
            lf.select(pl.struct([pl.col(c.name) for c in cols]).hash().n_unique())
            .collect(engine="streaming")
            .item()
        )
        row["__unique_rows"] = int(hashed)
    else:
        row["__unique_rows"] = 0
    return row


def compute_column_profiles(
    lf: pl.LazyFrame, schema: Sequence[ColumnInfo] | None = None
) -> ProfileResult:
    """Hitung Column_Profile + kualitas data (Req 6.1, 6.2, 6.5).

    ``schema`` (biasanya ``DatasetRecord.schema``) menentukan tipe logis tiap kolom;
    bila ``None`` diturunkan dari skema LazyFrame. Sinkron — panggil dari thread.

    Dataset kecil (≤ :data:`PROFILE_HEAVY_MIN_ROWS` baris): satu query agregat
    lazy eksak. Dataset besar: agregat dieksekusi berurutan dengan engine
    streaming (Req 5.4) — hanya satu hash table aktif pada satu waktu; baris
    duplikat memakai hash 64-bit dan ``top_values`` kolom non-unik memakai
    sampel awal deterministik (lihat :func:`_profile_row_heavy`).
    """
    cols = list(schema) if schema is not None else _schema_of(lf)
    row_count = _row_count(lf)
    if row_count <= PROFILE_HEAVY_MIN_ROWS:
        exprs: list[pl.Expr] = [pl.len().alias("__rows")]
        if cols:
            exprs.append(
                pl.struct([pl.col(c.name) for c in cols]).n_unique().alias("__unique_rows")
            )
        for i, col in enumerate(cols):
            exprs += _column_exprs(i, col)
        # Default engine (bukan streaming): sort_by multi-ekspresi pada engine
        # streaming tidak menjaga urutan dengan benar; dataset kecil muat di memori.
        row = lf.select(exprs).collect().row(0, named=True)
        row_count = int(row["__rows"])
    else:
        row = _profile_row_heavy(lf, cols, row_count)
    duplicate_rows = row_count - int(row["__unique_rows"]) if cols and row_count else 0

    drafts: list[dict[str, Any]] = []
    mixed: list[str] = []
    null_pct: dict[str, float] = {}
    for i, col in enumerate(cols):
        nulls = int(row[f"n{i}"])
        pct = nulls * 100.0 / row_count if row_count else 0.0
        null_pct[col.name] = pct
        cnt = _count_field(col.name)
        top = [(_scalar(e[col.name]), int(e[cnt])) for e in (row[f"t{i}"] or [])]
        draft: dict[str, Any] = {
            "name": col.name,
            "type": col.type,
            "null_count": nulls,
            "null_pct": pct,
            "distinct_count": int(row[f"d{i}"]),
            "top_values": top,
        }
        if col.type in _NUMERIC_TYPES:
            draft["min"] = _scalar(row[f"lo{i}"])
            draft["max"] = _scalar(row[f"hi{i}"])
            draft["mean"] = _finite(row[f"m{i}"])
        elif col.type in _TEMPORAL_TYPES:
            draft["min"] = _scalar(row[f"lo{i}"])
            draft["max"] = _scalar(row[f"hi{i}"])
        if col.type == "string":
            typed = int(row[f"x{i}"] or 0)
            if 0 < typed < row_count - nulls:
                mixed.append(col.name)
        drafts.append(draft)

    roles = {
        col.name: _role_for(col.type, col.name, d["distinct_count"], row_count)
        for col, d in zip(cols, drafts, strict=True)
    }
    profiles = [ColumnProfile(role=roles[d["name"]], **d) for d in drafts]
    quality = {
        "duplicate_rows": duplicate_rows,
        "mixed_type_columns": mixed,
        "null_pct": null_pct,
    }
    return ProfileResult(profiles, quality, row_count)


# ---------------------------------------------------------------------------
# Overlap nilai
# ---------------------------------------------------------------------------


def _common_dtype(a: pl.DataType, b: pl.DataType) -> pl.DataType | None:
    """Tipe bersama untuk membandingkan kunci; ``None`` = tidak perlu cast."""
    if a == b:
        return None
    if a.is_integer() and b.is_integer() and pl.UInt64 not in (a, b):
        return pl.Int64()
    return pl.String()


def compute_overlap(
    lf_a: pl.LazyFrame, col_a: str, lf_b: pl.LazyFrame, col_b: str
) -> OverlapStats:
    """Statistik overlap nilai non-null ``lf_a[col_a]`` vs ``lf_b[col_b]`` (Req 7.1, 7.2).

    Nilai null diabaikan; bila dtype berbeda keduanya di-cast ke tipe bersama
    (Int64 untuk integer, selainnya String). Sinkron — panggil dari thread.
    """
    dtype_a = lf_a.collect_schema()[col_a]
    dtype_b = lf_b.collect_schema()[col_b]
    common = _common_dtype(dtype_a, dtype_b)

    def keys(lf: pl.LazyFrame, col: str) -> pl.LazyFrame:
        expr = pl.col(col)
        if common is not None:
            expr = expr.cast(common)
        return lf.select(expr.alias("k")).drop_nulls()

    ka, kb = keys(lf_a, col_a), keys(lf_b, col_b)
    stats_a = ka.select(pl.len().alias("n"), pl.col("k").n_unique().alias("d"))
    stats_b = kb.select(pl.len().alias("n"), pl.col("k").n_unique().alias("d"))
    inter = ka.unique().join(kb.unique(), on="k", how="inner").select(pl.len().alias("i"))
    df_a, df_b, df_i = pl.collect_all([stats_a, stats_b, inter])
    n_a, d_a = int(df_a["n"][0]), int(df_a["d"][0])
    n_b, d_b = int(df_b["n"][0]), int(df_b["d"][0])
    return OverlapStats(
        distinct_a=d_a,
        distinct_b=d_b,
        intersection=int(df_i["i"][0]),
        unique_a=d_a == n_a,
        unique_b=d_b == n_b,
    )


def _overlaps_for(
    pairs: Sequence[tuple[ColumnRef, ColumnRef]], paths: Mapping[str, Path]
) -> dict[str, OverlapStats]:
    """Overlap semua pasangan (sinkron; satu thread untuk seluruh batch)."""
    frames: dict[str, pl.LazyFrame] = {}

    def frame(table: str) -> pl.LazyFrame:
        if table not in frames:
            frames[table] = pl.scan_parquet(paths[table])
        return frames[table]

    return {
        candidate_key(a, b): compute_overlap(frame(a.table), a.column, frame(b.table), b.column)
        for a, b in pairs
    }


_NO_OVERLAP = OverlapStats(distinct_a=0, distinct_b=0, intersection=0, unique_a=False, unique_b=False)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class Profiler:
    """Profiling otomatis + deteksi Relation_Candidate di atas Metadata_Store.

    ``source``: ``DataEngine`` (memakai ``parquet_path``-nya) atau ``DATA_DIR``
    (``datasets.parquet_path`` relatif di-resolve terhadap ``DATA_DIR/uploads``).
    """

    def __init__(
        self,
        repos: Repositories,
        bus: EventBus,
        source: DataEngine | str | os.PathLike[str],
        *,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT_PROFILES,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent harus >= 1")
        self.repos = repos
        self.bus = bus
        if isinstance(source, (str, os.PathLike)):
            self._engine = None
            self.uploads_root = Path(source).expanduser().resolve() / "uploads"
        else:
            self._engine = source
            self.uploads_root = source.uploads_root
        self._slots = asyncio.Semaphore(max_concurrent)

    def parquet_path(self, dataset: DatasetRecord) -> Path:
        if self._engine is not None:
            return self._engine.parquet_path(dataset)
        path = Path(dataset.parquet_path)
        return path if path.is_absolute() else safe_join(self.uploads_root, dataset.parquet_path)

    # -- profiling ------------------------------------------------------------

    async def profile_dataset(self, dataset: DatasetRecord | str) -> DatasetProfileRecord:
        """Hitung profil Dataset (versi terbaru) dan simpan ke ``dataset_profiles`` (Req 6.4)."""
        dataset_id = dataset.id if isinstance(dataset, DatasetRecord) else dataset
        record = await self.repos.datasets.get(dataset_id)
        path = self.parquet_path(record)
        schema = list(record.schema)
        async with self._slots:
            result = await asyncio.to_thread(
                lambda: compute_column_profiles(pl.scan_parquet(path), schema)
            )
        return await self.repos.profiles.upsert(
            record.id,
            data_version=record.data_version,
            columns=result.columns,
            quality=result.quality,
        )

    # -- relasi ---------------------------------------------------------------

    async def detect_relations(
        self, ws_id: str, *, dataset_id: str | None = None
    ) -> list[RelationRecord]:
        """Skor & upsert Relation_Candidate Workspace (Req 7.1, 7.2, 7.5).

        Pasangan ber-``candidate_key`` yang ditolak tidak pernah dihitung/diusulkan.
        Bila ``dataset_id`` diberikan, hanya pasangan yang melibatkan Dataset itu
        yang dihitung ulang (pasangan lain tidak berubah). Mengembalikan record
        hasil upsert (status ``confirmed`` yang sudah ada dipertahankan repo).
        """
        datasets = await self.repos.datasets.list_by_workspace(ws_id)
        if len(datasets) < 2:
            return []
        stored = await self.repos.profiles.list_by_workspace(ws_id)
        profiles = {ds_id: rec.columns for ds_id, rec in stored.items()}
        tables = [TableInfo(d.id, d.table_name, d.schema) for d in datasets]
        rejected = await self.repos.relations.rejected_keys(ws_id)

        pairs = candidate_pairs(tables, profiles, rejected)
        if dataset_id is not None:
            focus = {d.table_name for d in datasets if d.id == dataset_id}
            pairs = [p for p in pairs if p[0].table in focus or p[1].table in focus]
        if not pairs:
            return []

        paths = {d.table_name: self.parquet_path(d) for d in datasets}
        async with self._slots:
            stats = await asyncio.to_thread(_overlaps_for, pairs, paths)
        candidates = score_candidates(
            tables,
            profiles,
            rejected,
            # Pasangan di luar fokus tidak dihitung → tidak memenuhi ambang.
            lambda a, b: stats.get(candidate_key(a, b), _NO_OVERLAP),
        )
        return await self.repos.relations.upsert_candidates(ws_id, candidates)

    async def overlap(self, ws_id: str, a: ColumnRef, b: ColumnRef) -> OverlapStats:
        """Overlap dua kolom tabel Workspace (dipakai tool agent)."""
        datasets = await self.repos.datasets.list_by_workspace(ws_id)
        paths = {d.table_name: self.parquet_path(d) for d in datasets}
        async with self._slots:
            stats = await asyncio.to_thread(_overlaps_for, [(a, b)], paths)
        return stats[candidate_key(a, b)]

    # -- hook Ingestion_Service ------------------------------------------------

    async def on_dataset_registered(self, dataset: DatasetRecord) -> None:
        """Profil → publish ``dataset.profiled`` → deteksi relasi → ``relation.updated``.

        Tidak pernah melempar exception (kegagalan dicatat di log).
        """
        try:
            await self.profile_dataset(dataset)
        except Exception:
            logger.exception("Profiling Dataset %s gagal", dataset.id)
            return
        self._publish(dataset.workspace_id, "dataset.profiled", {"dataset_id": dataset.id})

        try:
            records = await self.detect_relations(dataset.workspace_id, dataset_id=dataset.id)
        except Exception:
            logger.exception("Deteksi relasi untuk Dataset %s gagal", dataset.id)
            return
        ids = [r.id for r in records if r.status == "candidate"]
        if ids:
            self._publish(dataset.workspace_id, "relation.updated", {"relation_ids": ids})

    def _publish(self, workspace_id: str, event_type: str, data: dict[str, Any]) -> None:
        try:
            self.bus.publish(workspace_id, event_type, data)
        except Exception:
            logger.exception("Gagal menerbitkan event %s", event_type)
