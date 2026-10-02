"""Pembaca XLSX untuk Ingestion_Service (Req 3.1–3.4, 5.2).

Modul ini hanya *membaca*: daftar sheet, skema logis, pemetaan header, dan
DataFrame Polars (utuh atau per batch). Penulisan ke Parquet dilakukan oleh
``ingestion.py`` memakai ``parquet_writer``.

Strategi baca:

- File ≤ ``XLSX_BLOCK_THRESHOLD_BYTES`` (100 MB): ``pl.read_excel(engine="calamine")``
  sekali untuk seluruh sheet; ``iter_batches`` memotong hasilnya.
- File lebih besar: per blok baris via ``fastexcel`` ``load_sheet(skip_rows, n_rows)``.
  Skema ditentukan dari blok pertama yang berisi data; bila blok berikutnya
  membutuhkan tipe yang lebih lebar, ``SchemaWidened`` di-raise membawa skema
  hasil pelebaran sehingga pemanggil dapat mengulang konversi sekali dengan
  ``XlsxSheetReader(..., logical_types=exc.schema)`` (sama seperti pipeline CSV).

Kedua jalur menerapkan penyempurnaan tipe yang sama dengan Polars: kolom float
yang semua nilainya bulat → ``Int64``, kolom datetime yang semua jamnya
00:00:00 → ``Date``. Header dinormalisasi dengan ``normalize_columns`` (header
kosong → ``column_{i}``, duplikat → sufiks ``_2``...). Baris yang seluruh
selnya kosong dibuang; kolom tidak pernah dibuang agar pemetaan header stabil.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import fastexcel
import polars as pl

from studio.api.errors import StudioError
from studio.core.identifiers import ColumnMappingEntry, normalize_columns
from studio.core.models import ColumnInfo, LogicalType

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "XLSX_BLOCK_THRESHOLD_BYTES",
    "EmptySheet",
    "SchemaWidened",
    "SheetInfo",
    "SheetNotFound",
    "UnreadableWorkbook",
    "XlsxSheetReader",
    "list_sheets",
    "logical_type_of",
    "polars_dtype_for",
    "read_sheet",
    "target_polars_schema",
    "widen_type",
]

#: Batas ukuran file untuk baca sekaligus via ``pl.read_excel`` (Req 5.2).
XLSX_BLOCK_THRESHOLD_BYTES = 100 * 1024 * 1024
#: Ukuran batch default (selaras dengan pipeline CSV).
DEFAULT_BATCH_SIZE = 100_000

_INT64_ABS_LIMIT = 9.2e18  # sedikit di bawah 2**63 agar cast Float64→Int64 aman


# ---------------------------------------------------------------------------
# Error domain
# ---------------------------------------------------------------------------


def _reason(exc: BaseException) -> str:
    """Baris pertama pesan error library (tanpa konteks path lokal)."""
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:300]


class UnreadableWorkbook(StudioError):
    """File XLSX rusak, terenkripsi, atau tidak dapat dibuka (Req 3.4)."""

    def __init__(self, filename: str, reason: str | None = None) -> None:
        details: dict[str, Any] = {"filename": filename}
        if reason:
            details["reason"] = reason
        super().__init__(
            code="UNREADABLE_WORKBOOK",
            message=(
                f"File '{filename}' tidak dapat dibaca. "
                "File mungkin rusak atau terenkripsi (dilindungi password)."
            ),
            details=details,
            http_status=422,
        )


class EmptySheet(StudioError):
    """Sheet yang dipilih tidak berisi baris data (Req 3.3)."""

    def __init__(self, sheet: str) -> None:
        super().__init__(
            code="EMPTY_SHEET",
            message=f"Sheet '{sheet}' tidak berisi baris data.",
            details={"sheet": sheet},
            http_status=422,
        )


class SheetNotFound(StudioError):
    """Nama sheet yang diminta tidak ada di workbook."""

    def __init__(self, sheet: str, available: Sequence[str]) -> None:
        super().__init__(
            code="SHEET_NOT_FOUND",
            message=f"Sheet '{sheet}' tidak ditemukan di workbook.",
            details={"sheet": sheet, "available": list(available)},
            http_status=422,
        )


class SchemaWidened(Exception):
    """Blok baris berikutnya membutuhkan tipe lebih lebar dari skema awal.

    ``schema`` adalah skema hasil pelebaran (nama ternormalisasi → tipe logis)
    untuk dipakai ulang lewat ``XlsxSheetReader(logical_types=...)``. Kolom yang
    sampai blok ini belum pernah berisi nilai tidak dicantumkan agar tipenya
    tetap ditentukan oleh nilai pertamanya (bukan dikunci ke ``string``).
    """

    def __init__(self, schema: dict[str, LogicalType], columns: list[str]) -> None:
        super().__init__(f"Tipe kolom perlu dilebarkan: {', '.join(columns)}")
        self.schema = schema
        self.columns = columns


# ---------------------------------------------------------------------------
# Pemetaan tipe
# ---------------------------------------------------------------------------


def logical_type_of(dtype: pl.DataType | type[pl.DataType]) -> LogicalType:
    """Petakan dtype Polars ke 6 tipe logis; selain yang dikenal → ``string``."""
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    if dtype == pl.Boolean:
        return "boolean"
    if dtype == pl.Date:
        return "date"
    if dtype == pl.Datetime:
        return "datetime"
    return "string"


_TARGET_DTYPES: dict[str, pl.DataType] = {
    "integer": pl.Int64(),
    "float": pl.Float64(),
    "string": pl.String(),
    "boolean": pl.Boolean(),
    "date": pl.Date(),
    "datetime": pl.Datetime("us"),
}


def polars_dtype_for(ltype: LogicalType) -> pl.DataType:
    """Dtype Polars target untuk tipe logis (konsisten dengan pembaca CSV)."""
    return _TARGET_DTYPES[ltype]


def target_polars_schema(columns: Sequence[ColumnInfo]) -> pl.Schema:
    return pl.Schema([(c.name, polars_dtype_for(c.type)) for c in columns])


def widen_type(a: LogicalType, b: LogicalType) -> LogicalType:
    """Tipe terkecil yang menampung ``a`` dan ``b`` (integer→float, date→datetime, lainnya→string)."""
    if a == b:
        return a
    pair = {a, b}
    if pair == {"integer", "float"}:
        return "float"
    if pair == {"date", "datetime"}:
        return "datetime"
    return "string"


# ---------------------------------------------------------------------------
# Daftar sheet
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetInfo:
    """Satu sheet dalam workbook; ``rows_hint`` = jumlah baris di bawah header (perkiraan)."""

    name: str
    rows_hint: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "rows_hint": self.rows_hint}


def _open_reader(path: str | os.PathLike[str]) -> fastexcel.ExcelReader:
    try:
        return fastexcel.read_excel(Path(path))
    except Exception as exc:  # CalamineError, IO error, format bukan zip (terenkripsi)
        raise UnreadableWorkbook(Path(path).name, _reason(exc)) from exc


def list_sheets(
    path: str | os.PathLike[str],
    *,
    block_threshold_bytes: int = XLSX_BLOCK_THRESHOLD_BYTES,
) -> list[SheetInfo]:
    """Daftar sheet workbook (Req 3.2); error baca → ``UnreadableWorkbook`` (Req 3.4).

    ``rows_hint`` hanya dihitung untuk file ≤ ``block_threshold_bytes`` karena
    menghitungnya memerlukan parsing penuh sheet.
    """
    reader = _open_reader(path)
    filename = Path(path).name
    names = list(reader.sheet_names)
    if not names:
        raise UnreadableWorkbook(filename, "workbook tidak memiliki sheet")

    with_hint = os.path.getsize(path) <= block_threshold_bytes
    result: list[SheetInfo] = []
    for name in names:
        hint: int | None = None
        if with_hint:
            try:
                ws = reader.load_sheet(name, n_rows=0)
                # Sheet kosong total: total_height underflow di fastexcel → paksa 0.
                hint = int(ws.total_height) if ws.width > 0 else 0
            except Exception as exc:
                raise UnreadableWorkbook(filename, _reason(exc)) from exc
        result.append(SheetInfo(name=name, rows_hint=hint))
    return result


# ---------------------------------------------------------------------------
# Normalisasi DataFrame
# ---------------------------------------------------------------------------


def _drop_empty_rows(df: pl.DataFrame) -> pl.DataFrame:
    if df.width == 0 or df.height == 0:
        return df
    return df.filter(~pl.all_horizontal(pl.all().is_null()))


def _refine_dtypes(df: pl.DataFrame) -> pl.DataFrame:
    """Samakan dengan ``pl.read_excel``: float bulat → Int64, datetime tengah malam → Date.

    Tipe lain di luar 6 tipe logis (Null, Duration, Time, ...) → String.
    """
    checks: list[tuple[pl.Expr, pl.Expr]] = []
    others: list[pl.Expr] = []
    for name, dtype in df.schema.items():
        col = pl.col(name)
        if dtype.is_float():
            checks.append(
                (
                    col.floor().eq_missing(col) & col.is_not_nan() & (col.abs() < _INT64_ABS_LIMIT),
                    col.cast(pl.Int64),
                )
            )
        elif dtype == pl.Datetime:
            checks.append((col.dt.time().eq(time(0, 0, 0)), col.cast(pl.Date)))
        elif logical_type_of(dtype) == "string" and dtype != pl.String:
            others.append(_to_string(col, dtype))
    if checks and df.height > 0:
        apply = df.select(cond.all(ignore_nulls=True) for cond, _ in checks).row(0)
        others.extend(cast for ok, (_, cast) in zip(apply, checks, strict=True) if ok)
    return df.with_columns(others) if others else df


def _to_string(col: pl.Expr, dtype: pl.DataType) -> pl.Expr:
    if dtype == pl.Null:
        return col.cast(pl.String)
    if dtype.is_float():
        # Nilai bulat ditulis tanpa ".0" (mis. 3.0 → "3") seperti koersi fastexcel.
        as_int = pl.when(col.floor().eq(col) & (col.abs() < _INT64_ABS_LIMIT)).then(
            col.cast(pl.Int64, strict=False).cast(pl.String)
        )
        return as_int.otherwise(col.cast(pl.String))
    return col.cast(pl.String)


def _logical_types(df: pl.DataFrame) -> dict[str, LogicalType | None]:
    """Tipe logis per kolom; ``None`` untuk kolom tanpa nilai (tidak membatasi skema)."""
    counts = df.select(pl.all().count()).row(0) if df.width else ()
    return {
        name: (logical_type_of(dtype) if n > 0 else None)
        for (name, dtype), n in zip(df.schema.items(), counts, strict=True)
    }


# ---------------------------------------------------------------------------
# Pembaca sheet
# ---------------------------------------------------------------------------


class XlsxSheetReader:
    """Baca satu sheet XLSX menjadi DataFrame dengan skema logis dan header ternormalisasi.

    ``logical_types`` (opsional) memaksa tipe logis kolom ternormalisasi,
    biasanya ``SchemaWidened.schema`` dari percobaan sebelumnya.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        sheet: str,
        *,
        logical_types: Mapping[str, LogicalType] | None = None,
        block_threshold_bytes: int = XLSX_BLOCK_THRESHOLD_BYTES,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size harus > 0")
        self.batch_size = batch_size
        self.path = Path(path)
        self.sheet = sheet
        self.filename = self.path.name
        self._reader = _open_reader(self.path)
        names = list(self._reader.sheet_names)
        if sheet not in names:
            raise SheetNotFound(sheet, names)

        self.file_size = os.path.getsize(self.path)
        self.uses_blocks = self.file_size > block_threshold_bytes
        self._override = dict(logical_types or {})

        self._raw_header = self._read_raw_header()
        self._full: pl.DataFrame | None = None  # cache jalur baca sekaligus
        self._first_block: tuple[int, pl.DataFrame] | None = None  # (offset berikutnya, blok)
        self._schema: dict[str, LogicalType] | None = None
        #: Kolom tanpa nilai di blok penentu skema (tipe placeholder "string").
        self._unresolved: set[str] = set()
        self._names: list[str] | None = None
        self._mapping: list[ColumnMappingEntry] | None = None

    # -- API publik --------------------------------------------------------

    def schema(self) -> list[ColumnInfo]:
        """Skema logis kolom ternormalisasi; sheet tanpa data → ``EmptySheet``."""
        types = self._ensure_schema()
        return [ColumnInfo(name=n, type=t) for n, t in types.items()]

    def polars_schema(self) -> pl.Schema:
        return target_polars_schema(self.schema())

    @property
    def column_mapping(self) -> list[ColumnMappingEntry]:
        """Pemetaan ``{original, normalized}`` per posisi kolom (Req 2.5)."""
        self._ensure_schema()
        assert self._mapping is not None
        return list(self._mapping)

    def iter_batches(self, batch_size: int | None = None) -> Iterator[pl.DataFrame]:
        """Iterasi batch DataFrame bertipe target (``polars_schema()``).

        Default ``batch_size`` = ukuran batch konstruktor. Jalur blok dapat
        me-raise ``SchemaWidened`` di tengah iterasi.
        """
        batch_size = self.batch_size if batch_size is None else batch_size
        if batch_size <= 0:
            raise ValueError("batch_size harus > 0")
        self._ensure_schema()
        if not self.uses_blocks:
            assert self._full is not None
            for offset in range(0, self._full.height, batch_size):
                yield self._full.slice(offset, batch_size)
            return
        yield from self._iter_blocks(batch_size)

    def read(self) -> pl.DataFrame:
        """Seluruh sheet sebagai satu DataFrame bertipe target."""
        self._ensure_schema()
        if not self.uses_blocks:
            assert self._full is not None
            return self._full
        batches = list(self._iter_blocks(self.batch_size))
        return pl.concat(batches, how="vertical") if batches else self.polars_schema().to_frame()

    # -- Header ------------------------------------------------------------

    def _read_raw_header(self) -> list[str]:
        try:
            header = self._reader.load_sheet(
                self.sheet, header_row=None, skip_rows=0, n_rows=1, dtypes="string"
            ).to_polars()
        except Exception as exc:
            raise UnreadableWorkbook(self.filename, _reason(exc)) from exc
        if header.height == 0:
            return []
        return ["" if v is None else str(v) for v in header.row(0)]

    def _apply_header(self, df: pl.DataFrame) -> pl.DataFrame:
        if self._names is None:
            if len(self._raw_header) == df.width:
                originals = self._raw_header
            else:  # fallback: nama dari parser, header kosong hasil generate → ""
                originals = ["" if c.startswith("__UNNAMED__") else c for c in df.columns]
            self._names, self._mapping = normalize_columns(originals)
        if len(self._names) != df.width:
            raise UnreadableWorkbook(self.filename, "jumlah kolom sheet tidak konsisten")
        return df.rename(dict(zip(df.columns, self._names, strict=True)))

    # -- Skema -------------------------------------------------------------

    def _ensure_schema(self) -> dict[str, LogicalType]:
        if self._schema is not None:
            return self._schema
        if self.uses_blocks:
            df = self._find_first_block()
        else:
            df = self._load_full()
        inferred = _logical_types(df)
        schema: dict[str, LogicalType] = {}
        for name, ltype in inferred.items():
            forced = self._override.get(name)
            if forced is not None:
                schema[name] = forced if ltype is None else widen_type(forced, ltype)
            else:
                schema[name] = ltype or "string"
                if ltype is None:
                    # Belum ada nilai: "string" hanya placeholder; blok berikutnya
                    # yang berisi nilai menentukan tipenya (lihat ``_conform``).
                    self._unresolved.add(name)
        self._schema = schema

        if not self.uses_blocks:
            self._full = self._conform(df)
        return schema

    def _check_not_empty(self, df: pl.DataFrame) -> None:
        if df.width == 0 or df.height == 0:
            raise EmptySheet(self.sheet)

    def _load_full(self) -> pl.DataFrame:
        if not self._raw_header:
            raise EmptySheet(self.sheet)
        try:
            df = pl.read_excel(
                self.path,
                sheet_name=self.sheet,
                engine="calamine",
                infer_schema_length=None,
                drop_empty_rows=True,
                drop_empty_cols=False,
                raise_if_empty=False,
                read_options={"header_row": 0},
            )
        except Exception as exc:
            raise UnreadableWorkbook(self.filename, _reason(exc)) from exc
        assert isinstance(df, pl.DataFrame)
        df = self._apply_header(df)
        df = _refine_dtypes(_drop_empty_rows(df))
        self._check_not_empty(df)
        return df

    # -- Jalur blok (file > 100 MB) ----------------------------------------

    def _load_block(self, offset: int, n_rows: int) -> tuple[pl.DataFrame, int]:
        """Blok mentah ``[offset, offset+n_rows)``; kembalikan (df ternormalisasi, tinggi mentah)."""
        try:
            raw = self._reader.load_sheet(
                self.sheet,
                header_row=0,
                skip_rows=offset,
                n_rows=n_rows,
                schema_sample_rows=None,
                dtype_coercion="coerce",
            ).to_polars()
        except Exception as exc:
            raise UnreadableWorkbook(self.filename, _reason(exc)) from exc
        height = raw.height
        if raw.width == 0:
            return raw, height
        df = _refine_dtypes(_drop_empty_rows(self._apply_header(raw)))
        return df, height

    def _find_first_block(self) -> pl.DataFrame:
        if not self._raw_header:
            raise EmptySheet(self.sheet)
        batch_size = self.batch_size
        offset = 0
        while True:
            df, height = self._load_block(offset, batch_size)
            offset += height
            if df.width and df.height:
                self._first_block = (offset, df)
                return df
            if height < batch_size:
                raise EmptySheet(self.sheet)

    def _iter_blocks(self, batch_size: int) -> Iterator[pl.DataFrame]:
        if self._first_block is not None:
            # Blok pertama sudah dibaca saat menentukan skema; pakai ulang sekali.
            offset, first = self._first_block
            self._first_block = None
            yield self._conform(first)
        else:
            offset = 0
        while True:
            df, height = self._load_block(offset, batch_size)
            offset += height
            if df.width and df.height:
                yield self._conform(df)
            if height < batch_size:
                return

    # -- Penyesuaian tipe --------------------------------------------------

    def _conform(self, df: pl.DataFrame) -> pl.DataFrame:
        """Cast ``df`` ke skema target; tipe yang lebih lebar → ``SchemaWidened``."""
        schema = self._schema
        assert schema is not None
        widened: list[str] = []
        new_schema = dict(schema)
        for name, ltype in _logical_types(df).items():
            if ltype is None:
                continue
            if name in self._unresolved:
                # Nilai pertama kolom yang sebelumnya kosong: tipenya ditentukan
                # blok ini (bukan dilebarkan dari placeholder "string").
                widened.append(name)
                new_schema[name] = ltype
                continue
            target = widen_type(schema[name], ltype)
            if target != schema[name]:
                widened.append(name)
                new_schema[name] = target
        if widened:
            # Kolom yang masih tanpa nilai tidak dikunci ke "string" pada percobaan ulang.
            still_unresolved = self._unresolved.difference(widened)
            raise SchemaWidened(
                {n: t for n, t in new_schema.items() if n not in still_unresolved}, widened
            )

        exprs = []
        for name, dtype in df.schema.items():
            target_dtype = polars_dtype_for(schema[name])
            if dtype == target_dtype:
                continue
            if target_dtype == pl.String:
                exprs.append(_to_string(pl.col(name), dtype).alias(name))
            else:
                exprs.append(pl.col(name).cast(target_dtype, strict=True))
        return df.with_columns(exprs) if exprs else df


def read_sheet(
    path: str | os.PathLike[str],
    sheet: str,
    *,
    block_threshold_bytes: int = XLSX_BLOCK_THRESHOLD_BYTES,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[pl.DataFrame, list[ColumnInfo], list[ColumnMappingEntry]]:
    """Baca seluruh sheet: ``(df, schema, column_mapping)``.

    Pada jalur blok, pelebaran tipe ditangani otomatis dengan mengulang baca
    memakai skema yang dilebarkan. Pelebaran monoton (maksimal 2 langkah per
    kolom: integer→float→string), sehingga pengulangan selalu berhenti.
    """
    logical_types: dict[str, LogicalType] | None = None
    while True:
        reader = XlsxSheetReader(
            path,
            sheet,
            logical_types=logical_types,
            block_threshold_bytes=block_threshold_bytes,
            batch_size=batch_size,
        )
        try:
            df = reader.read()
        except SchemaWidened as exc:
            logical_types = exc.schema
            continue
        return df, reader.schema(), reader.column_mapping
