"""Pembaca CSV streaming untuk Ingestion_Service (Req 2.1, 2.2, 2.3, 2.5, 5.2).

Pipeline (lihat design.md "Pipeline CSV"):

1. **Validasi encoding** — byte di-stream per 1 MiB dan di-decode UTF-8 (BOM
   diterima); byte tidak valid → ``ParseError(cause="encoding", line=n)``.
2. **Validasi struktur** — satu pass streaming dengan modul ``csv`` stdlib
   (``locate_bad_row``): membaca header, menghitung baris data, dan menemukan
   baris pertama yang jumlah field-nya berbeda dari header (Polars sendiri
   hanya menolak baris yang *lebih* panjang; baris yang lebih pendek diisi
   null diam-diam). File kosong / hanya header → ``ParseError(cause="empty")``.
3. **Inferensi skema** — ``pl.scan_csv(infer_schema_length=10_000,
   try_parse_dates=True)``; header dinormalisasi ``normalize_columns`` dan
   diberikan ke Polars lewat ``new_columns`` (menggantikan nama otomatis
   Polars seperti ``a_duplicated_0``). Tipe Polars dipetakan ke 6 tipe logis.
4. **Batch** — file dibaca ulang sebagai String murni per 100.000 baris
   (``scan_csv(...).collect_batches``) lalu setiap kolom di-cast ke tipe
   target tetap (``TARGET_DTYPES``). Cast gagal → ``ColumnCastError``;
   ``convert_csv_to_parquet`` lalu melebarkan tipe (integer→float→string,
   lainnya→string) dan mengulang konversi satu kali.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pyarrow as pa
import pyarrow.csv as pacsv

from studio.api.errors import StudioError
from studio.core.identifiers import ColumnMappingEntry, normalize_columns
from studio.core.models import ColumnInfo, LogicalType
from studio.data.parquet_writer import ParquetBatchWriter

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "INFER_SCHEMA_LENGTH",
    "TARGET_DTYPES",
    "ColumnCastError",
    "CsvBatch",
    "CsvReader",
    "ParseError",
    "ProgressCallback",
    "convert_csv_to_parquet",
    "locate_bad_row",
    "logical_type_of",
    "validate_utf8",
    "widen_type",
]

DEFAULT_BATCH_SIZE = 100_000
INFER_SCHEMA_LENGTH = 10_000
_CHUNK_BYTES = 1 << 20
_PROGRESS_EVERY_ROWS = 50_000
#: Ukuran blok baca streaming pyarrow CSV (Req 5.2): blok kecil menjaga puncak
#: memori rendah (~ukuran blok + buffer), jauh di bawah pembaca paralel Polars
#: yang menyimpan morsel utuh di memori.
_CSV_STREAM_BLOCK_BYTES = 1 << 20

#: Tipe Polars target untuk setiap tipe logis (skema Parquet tetap).
TARGET_DTYPES: dict[LogicalType, pl.DataType] = {
    "integer": pl.Int64(),
    "float": pl.Float64(),
    "string": pl.String(),
    "boolean": pl.Boolean(),
    "date": pl.Date(),
    "datetime": pl.Datetime("us"),
}

#: Satu langkah pelebaran tipe saat cast gagal.
_WIDEN: dict[LogicalType, LogicalType] = {
    "integer": "float",
    "float": "string",
    "boolean": "string",
    "date": "string",
    "datetime": "string",
    "string": "string",
}

#: Menerima persentase progres 0–99.
ProgressCallback = Callable[[float], None]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

_CAUSE_LABELS = {
    "encoding": "encoding tidak didukung (file harus UTF-8)",
    "column_count": "jumlah kolom tidak konsisten",
    "empty": "file kosong atau hanya berisi header",
    "header": "baris header kosong",
    "malformed": "format CSV tidak valid",
    "type_conversion": "konversi tipe kolom gagal",
}


class ParseError(StudioError):
    """File CSV tidak dapat di-parse (Req 2.3).

    ``details = {"cause": ..., "line": int | None, ...}``; pesan menyebut
    penyebab dan nomor baris pertama yang bermasalah bila tersedia.
    """

    def __init__(self, cause: str, line: int | None = None, **extra: Any) -> None:
        label = _CAUSE_LABELS.get(cause, cause)
        where = f" pada baris {line}" if line is not None else ""
        message = f"File CSV tidak dapat di-parse: {label}{where}."
        hint = extra.get("hint")
        if hint:
            message = f"{message} {hint}"
        super().__init__(
            code="PARSE_ERROR",
            message=message,
            details={"cause": cause, "line": line, **extra},
            http_status=422,
        )
        self.cause = cause
        self.line = line


class ColumnCastError(ValueError):
    """Nilai kolom pada suatu batch tidak dapat di-cast ke tipe targetnya."""

    def __init__(self, column: str, logical_type: LogicalType, batch_index: int) -> None:
        super().__init__(
            f"Kolom {column!r} tidak dapat di-cast ke {logical_type} (batch {batch_index})"
        )
        self.column = column
        self.logical_type = logical_type
        self.batch_index = batch_index


# ---------------------------------------------------------------------------
# Tipe
# ---------------------------------------------------------------------------


def logical_type_of(dtype: pl.DataType) -> LogicalType:
    """Petakan dtype Polars ke tipe logis; tipe lain/Null → ``string``."""
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    if dtype == pl.Boolean:
        return "boolean"
    if dtype == pl.Date:
        return "date"
    if isinstance(dtype, pl.Datetime):
        return "datetime"
    return "string"


def widen_type(logical_type: LogicalType) -> LogicalType:
    """Satu langkah pelebaran: integer→float→string; boolean/date/datetime→string."""
    return _WIDEN[logical_type]


_NULL_BOOL = pl.lit(None, dtype=pl.Boolean)


def _cast_series(raw: pl.Series, logical_type: LogicalType) -> pl.Series:
    """Cast kolom String mentah ke tipe target; raise bila ada nilai tak valid."""
    if logical_type == "string":
        return raw
    if logical_type in ("integer", "float"):
        return raw.cast(TARGET_DTYPES[logical_type], strict=True)
    if logical_type == "boolean":
        lowered = raw.str.strip_chars().str.to_lowercase()
        out = (
            pl.select(
                pl.when(lowered == "true")
                .then(True)
                .when(lowered == "false")
                .then(False)
                .otherwise(_NULL_BOOL)
                .alias(raw.name)
            )
            .to_series()
        )
        if out.null_count() != raw.null_count():
            raise ValueError("nilai boolean tidak valid")
        return out
    if raw.null_count() == raw.len():
        return pl.Series(raw.name, [None] * raw.len(), dtype=TARGET_DTYPES[logical_type])
    if logical_type == "date":
        return raw.str.to_date(strict=True)
    # datetime: nilai ber-offset dinormalisasi ke UTC lalu dijadikan naive.
    out = raw.str.to_datetime(time_unit="us", strict=True)
    if isinstance(out.dtype, pl.Datetime) and out.dtype.time_zone is not None:
        out = out.dt.convert_time_zone("UTC").dt.replace_time_zone(None)
    return out


def _try_cast(raw: pl.Series, logical_type: LogicalType) -> pl.Series | None:
    try:
        return _cast_series(raw, logical_type)
    except (pl.exceptions.PolarsError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Validasi encoding & struktur
# ---------------------------------------------------------------------------


def validate_utf8(path: str | os.PathLike[str], *, chunk_bytes: int = _CHUNK_BYTES) -> None:
    """Pastikan seluruh file adalah UTF-8 valid (BOM diterima), secara streaming.

    Gagal → ``ParseError(cause="encoding", line=n)`` dengan ``n`` nomor baris
    fisik (1-based) tempat byte tidak valid pertama berada.
    """
    # BOM UTF-8 (EF BB BF) sendiri adalah urutan UTF-8 valid sehingga tidak
    # perlu penanganan khusus di sini; BOM lain (UTF-16/32) gagal di baris 1.
    newlines_before = 0
    carry = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_bytes)
            final = not chunk
            data = carry + chunk
            try:
                data.decode("utf-8")
                valid_end = len(data)
                carry = b""
            except UnicodeDecodeError as e:
                # Urutan multibyte terpotong di akhir chunk: tunda ke chunk berikutnya.
                truncated_tail = e.end == len(data) and len(data) - e.start < 4
                if not final and truncated_tail:
                    valid_end = e.start
                    carry = data[e.start :]
                else:
                    line = newlines_before + data.count(b"\n", 0, e.start) + 1
                    raise ParseError(
                        "encoding", line, hint="Simpan ulang file sebagai UTF-8."
                    ) from None
            newlines_before += data.count(b"\n", 0, valid_end)
            if final:
                return


@dataclass(frozen=True, slots=True)
class CsvStructure:
    """Hasil pass struktur stdlib ``csv``."""

    header: list[str]
    data_rows: int
    bad_line: int | None = None
    bad_fields: int | None = None


def _set_field_limit() -> None:
    limit = 2**31 - 1
    if csv.field_size_limit() < limit:
        csv.field_size_limit(limit)


def _scan_structure(
    path: str | os.PathLike[str],
    *,
    stop_at_first_bad: bool = True,
    progress: Callable[[float], None] | None = None,
) -> CsvStructure | None:
    """Pindai file dengan ``csv`` stdlib; ``None`` bila file tidak berisi record apa pun."""
    _set_field_limit()
    size = max(1, os.path.getsize(path))
    with open(path, "rb") as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
        reader = csv.reader(text)
        try:
            header = next(reader, None)
            if header is None:
                return None
            if not header:
                # Baris pertama kosong: file hanya berisi baris kosong → kosong;
                # selain itu header tidak valid.
                return CsvStructure([], 0) if any(row for row in reader) else None
            expected = len(header)
            data_rows = 0
            prev_line = reader.line_num
            for row in reader:
                start_line = prev_line + 1
                prev_line = reader.line_num
                n = len(row)
                if n == 0:
                    # Baris kosong: Polars membacanya sebagai baris null. Untuk
                    # CSV satu kolom itu adalah nilai null yang sah.
                    if expected == 1:
                        data_rows += 1
                    continue
                if n != expected:
                    return CsvStructure(header, data_rows, start_line, n)
                data_rows += 1
                if progress is not None and data_rows % _PROGRESS_EVERY_ROWS == 0:
                    progress(min(1.0, raw.tell() / size))
        except csv.Error as e:
            raise ParseError("malformed", reader.line_num or None, reason=str(e)) from None
        except UnicodeDecodeError:
            validate_utf8(path)  # memberi nomor baris yang tepat
            raise ParseError("encoding", None) from None  # pragma: no cover
    return CsvStructure(header, data_rows)


def locate_bad_row(path: str | os.PathLike[str]) -> int | None:
    """Nomor baris (1-based, termasuk header) record pertama yang jumlah field-nya
    berbeda dari header; ``None`` bila semua konsisten. Streaming, stdlib ``csv``.
    """
    structure = _scan_structure(path)
    return structure.bad_line if structure is not None else None


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CsvBatch:
    """Satu batch hasil cast; ``progress`` = estimasi fraksi file yang telah diproses."""

    index: int
    frame: pl.DataFrame
    rows_done: int
    total_rows: int

    @property
    def progress(self) -> float:
        return 1.0 if self.total_rows == 0 else min(1.0, self.rows_done / self.total_rows)


def _polars_error(path: str | os.PathLike[str], exc: Exception) -> ParseError:
    text = str(exc)
    if "more fields" in text or "fields than" in text:
        return ParseError("column_count", locate_bad_row(path))
    return ParseError("malformed", None, reason=text.splitlines()[0] if text else type(exc).__name__)


class CsvReader:
    """Pembaca CSV streaming dengan skema tetap.

    ``schema()`` menjalankan validasi (encoding, struktur, kosong) dan inferensi
    sekali lalu meng-cache hasilnya. ``iter_batches()`` menghasilkan
    ``CsvBatch`` bernama kolom hasil normalisasi dan bertipe ``TARGET_DTYPES``.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        infer_schema_length: int = INFER_SCHEMA_LENGTH,
    ) -> None:
        self.path = Path(path)
        self.infer_schema_length = infer_schema_length
        self._columns: list[ColumnInfo] | None = None
        self._mapping: list[ColumnMappingEntry] | None = None
        self._names: list[str] = []
        self._total_rows = 0

    # -- validasi & inferensi -------------------------------------------------

    def schema(
        self, progress: Callable[[float], None] | None = None
    ) -> tuple[list[ColumnInfo], list[ColumnMappingEntry]]:
        """Validasi file lalu kembalikan ``(kolom logis, pemetaan nama kolom)``."""
        if self._columns is not None and self._mapping is not None:
            return list(self._columns), list(self._mapping)

        if os.path.getsize(self.path) == 0:
            raise ParseError("empty")
        validate_utf8(self.path)
        structure = _scan_structure(self.path, progress=progress)
        if structure is None:
            raise ParseError("empty")
        if not structure.header:
            raise ParseError("header", 1)
        if structure.bad_line is not None:
            raise ParseError(
                "column_count",
                structure.bad_line,
                expected_fields=len(structure.header),
                found_fields=structure.bad_fields,
            )
        if structure.data_rows == 0:
            raise ParseError("empty")

        names, mapping = normalize_columns(structure.header)
        try:
            inferred = pl.scan_csv(
                self.path,
                has_header=True,
                new_columns=names,
                infer_schema_length=self.infer_schema_length,
                try_parse_dates=True,
            ).collect_schema()
        except (pl.exceptions.PolarsError, OSError) as e:
            raise _polars_error(self.path, e) from None
        if list(inferred.names()) != names:
            raise ParseError("malformed", 1, reason="header tidak dapat dibaca konsisten")

        self._names = names
        self._mapping = mapping
        self._columns = [
            ColumnInfo(name=name, type=logical_type_of(dtype)) for name, dtype in inferred.items()
        ]
        self._total_rows = structure.data_rows
        return list(self._columns), list(mapping)

    @property
    def total_rows(self) -> int:
        """Jumlah baris data hasil pass validasi (tersedia setelah ``schema()``)."""
        self.schema()
        return self._total_rows

    def _resolve_types(self, types: Mapping[str, LogicalType] | None) -> dict[str, LogicalType]:
        columns, _ = self.schema()
        base = {c.name: c.type for c in columns}
        if types:
            unknown = set(types) - set(base)
            if unknown:
                raise KeyError(f"Kolom tidak dikenal: {sorted(unknown)}")
            base.update(types)
        return base

    # -- batch ---------------------------------------------------------------

    def iter_raw_batches(self, batch_size: int = DEFAULT_BATCH_SIZE) -> Iterator[pl.DataFrame]:
        """Batch mentah: semua kolom String, nama kolom hasil normalisasi.

        Membaca lewat streaming ``pyarrow.csv.open_csv`` (blok
        ``_CSV_STREAM_BLOCK_BYTES``) — pembaca paralel Polars menyimpan morsel
        utuh file di memori sehingga puncak RSS mencapai ~2x ukuran file, yang
        melanggar anggaran memori konversi (Req 5.2). Jumlah baris per batch
        mengikuti ukuran blok (``batch_size`` hanya dipakai jalur cast).
        """
        self.schema()
        read = pacsv.ReadOptions(
            # ``column_names`` eksplisit → pyarrow TIDAK otomatis melewati baris
            # header asli (lihat docstring ReadOptions); skip_rows=1 melewatinya.
            column_names=self._names, skip_rows=1, block_size=_CSV_STREAM_BLOCK_BYTES
        )
        parse = pacsv.ParseOptions(ignore_empty_lines=False)
        convert = pacsv.ConvertOptions(
            # Semua kolom String; string kosong menjadi null (perilaku yang
            # sama dengan pembaca CSV Polars).
            column_types={name: pa.string() for name in self._names},
            strings_can_be_null=True,
            null_values=[""],
        )
        try:
            with pacsv.open_csv(
                self.path, read_options=read, parse_options=parse, convert_options=convert
            ) as reader:
                for arrow_batch in reader:
                    frame = pl.from_arrow(arrow_batch)
                    if frame.height:
                        yield frame  # type: ignore[misc]
        except (pa.ArrowInvalid, OSError) as e:
            raise _polars_error(self.path, e) from None

    def iter_batches(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        types: Mapping[str, LogicalType] | None = None,
    ) -> Iterator[CsvBatch]:
        """Batch ber-skema tetap. ``types`` menimpa tipe hasil inferensi per kolom.

        Nilai yang tidak dapat di-cast → ``ColumnCastError`` (tanpa pelebaran).
        """
        resolved = self._resolve_types(types)
        rows_done = 0
        for index, raw in enumerate(self.iter_raw_batches(batch_size)):
            cols: list[pl.Series] = []
            for name in self._names:
                cast = _try_cast(raw.get_column(name), resolved[name])
                if cast is None:
                    raise ColumnCastError(name, resolved[name], index)
                cols.append(cast)
            rows_done += raw.height
            yield CsvBatch(index, pl.DataFrame(cols), rows_done, max(rows_done, self._total_rows))

    def widen_types(
        self,
        types: Mapping[str, LogicalType] | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> dict[str, LogicalType]:
        """Pindai seluruh file dan lebarkan tipe tiap kolom sampai semua batch dapat di-cast.

        Pelebaran monoton (integer→float→string), sehingga tipe hasil pasti
        berhasil untuk semua batch.
        """
        resolved = self._resolve_types(types)
        for raw in self.iter_raw_batches(batch_size):
            for name in self._names:
                series = raw.get_column(name)
                while resolved[name] != "string" and _try_cast(series, resolved[name]) is None:
                    resolved[name] = widen_type(resolved[name])
        return resolved

    @staticmethod
    def target_schema(types: Mapping[str, LogicalType]) -> dict[str, pl.DataType]:
        """Skema Polars tetap untuk pemetaan ``nama → tipe logis``."""
        return {name: TARGET_DTYPES[t] for name, t in types.items()}


# ---------------------------------------------------------------------------
# Konversi CSV → Parquet
# ---------------------------------------------------------------------------

#: Porsi progres untuk pass validasi; sisanya untuk konversi (maks 99).
_VALIDATION_SHARE = 20.0


def convert_csv_to_parquet(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
    progress_cb: ProgressCallback | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[list[ColumnInfo], list[ColumnMappingEntry], int]:
    """Konversi CSV ke Parquet secara streaming dan atomik.

    Kembalikan ``(skema, pemetaan kolom, jumlah baris)``. ``progress_cb``
    menerima persentase 0–99 yang monoton naik. Bila cast gagal di suatu batch,
    tipe kolom dilebarkan (lihat ``CsvReader.widen_types``) dan konversi diulang
    satu kali. Kegagalan apa pun menghapus ``{dst}.partial``; ``dst`` hanya
    muncul bila konversi sukses.
    """
    last = 0.0

    def report(pct: float) -> None:
        nonlocal last
        pct = min(99.0, max(last, pct))
        if progress_cb is not None and pct > last:
            progress_cb(pct)
        last = pct

    reader = CsvReader(src)
    columns, mapping = reader.schema(progress=lambda f: report(f * _VALIDATION_SHARE))
    report(_VALIDATION_SHARE)
    types: dict[str, LogicalType] = {c.name: c.type for c in columns}

    for attempt in range(2):
        try:
            with ParquetBatchWriter(dst, CsvReader.target_schema(types)) as writer:
                for batch in reader.iter_batches(batch_size, types):
                    writer.write(batch.frame)
                    report(_VALIDATION_SHARE + batch.progress * (99.0 - _VALIDATION_SHARE))
            final_columns = [ColumnInfo(name=n, type=t) for n, t in types.items()]
            return final_columns, mapping, writer.rows_written
        except ColumnCastError as e:
            if attempt == 1:
                raise ParseError("type_conversion", None, column=e.column) from e
            types = reader.widen_types(types, batch_size)
    raise AssertionError("unreachable")  # pragma: no cover
