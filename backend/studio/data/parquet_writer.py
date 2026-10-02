"""Penulis Parquet per batch yang atomik (Req 4.1, 5.2, 5.5).

Data ditulis ke ``{final}.partial`` (mis. ``sales.parquet.partial``) dengan
``pyarrow.parquet.ParquetWriter`` (kompresi zstd). ``finalize()`` menutup file
lalu ``os.replace`` ke nama final sehingga pembaca tidak pernah melihat file
setengah jadi; ``abort()`` menghapus file parsial.

Sebagai context manager: keluar normal → ``finalize()`` (bila belum
difinalisasi/dibatalkan), keluar karena exception → ``abort()``.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from types import TracebackType
from typing import Self

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

__all__ = [
    "PARTIAL_SUFFIX",
    "ParquetBatchWriter",
    "arrow_schema_for",
    "partial_path_for",
    "read_parquet",
    "write_parquet",
]

PARTIAL_SUFFIX = ".partial"
DEFAULT_COMPRESSION = "zstd"


def partial_path_for(final_path: str | os.PathLike[str]) -> Path:
    """Path file sementara untuk ``final_path``: ``{final_path}.partial``."""
    final = Path(final_path)
    return final.with_name(final.name + PARTIAL_SUFFIX)


def arrow_schema_for(schema: pl.Schema | dict[str, pl.DataType]) -> pa.Schema:
    """Skema Arrow yang identik dengan hasil ``DataFrame.to_arrow()`` untuk skema Polars ini."""
    return pl.DataFrame(schema=schema).to_arrow().schema


class ParquetBatchWriter:
    """Tulis ``pl.DataFrame`` per batch ke satu file Parquet secara atomik.

    ``schema`` (Polars atau Arrow) opsional; bila tidak diberikan, skema diambil
    dari batch pertama. Setiap batch di-cast ke skema tersebut sehingga semua
    row group konsisten. File tanpa batch tetap valid (0 baris) asalkan skema
    diketahui.
    """

    def __init__(
        self,
        final_path: str | os.PathLike[str],
        schema: pa.Schema | pl.Schema | dict[str, pl.DataType] | None = None,
        *,
        compression: str = DEFAULT_COMPRESSION,
    ) -> None:
        self.final_path = Path(final_path)
        self.partial_path = partial_path_for(self.final_path)
        self.compression = compression
        self.rows_written = 0
        self._schema: pa.Schema | None = (
            schema if isinstance(schema, pa.Schema) or schema is None else arrow_schema_for(schema)
        )
        self._writer: pq.ParquetWriter | None = None
        self._closed = False
        self._finalized = False
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        if self._schema is not None:
            self._open(self._schema)

    # -- state ---------------------------------------------------------------

    @property
    def schema(self) -> pa.Schema | None:
        return self._schema

    @property
    def finalized(self) -> bool:
        return self._finalized

    def _open(self, schema: pa.Schema) -> None:
        # Sisa file parsial dari percobaan sebelumnya ditimpa.
        self.partial_path.unlink(missing_ok=True)
        self._writer = pq.ParquetWriter(
            str(self.partial_path), schema, compression=self.compression
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("ParquetBatchWriter sudah ditutup")

    # -- API -----------------------------------------------------------------

    def write(self, frame: pl.DataFrame | pa.Table) -> None:
        """Tambahkan satu batch (satu atau lebih row group)."""
        self._ensure_open()
        table = frame.to_arrow() if isinstance(frame, pl.DataFrame) else frame
        if self._schema is None:
            self._schema = table.schema
            self._open(self._schema)
        elif not table.schema.equals(self._schema):
            table = table.select(self._schema.names).cast(self._schema)
        if table.num_rows == 0:
            return
        assert self._writer is not None
        self._writer.write_table(table)
        self.rows_written += table.num_rows

    def finalize(self) -> Path:
        """Tutup file lalu pindahkan atomik ke ``final_path``; kembalikan path final."""
        self._ensure_open()
        if self._writer is None:
            raise ValueError("Skema Parquet tidak diketahui: tidak ada batch yang ditulis")
        try:
            self._writer.close()
            self._closed = True
            os.replace(self.partial_path, self.final_path)
        except BaseException:
            self.abort()
            raise
        self._finalized = True
        return self.final_path

    def abort(self) -> None:
        """Batalkan penulisan dan hapus file parsial (idempoten)."""
        if self._finalized:
            return
        if self._writer is not None and not self._closed:
            with contextlib.suppress(Exception):
                self._writer.close()
        self._closed = True
        with contextlib.suppress(OSError):
            self.partial_path.unlink(missing_ok=True)

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.abort()
        elif not self._closed:
            self.finalize()


def write_parquet(
    frame: pl.DataFrame,
    path: str | os.PathLike[str],
    *,
    compression: str = DEFAULT_COMPRESSION,
) -> Path:
    """Tulis satu DataFrame ke Parquet secara atomik (via ``.partial`` + ``os.replace``)."""
    with ParquetBatchWriter(path, frame.schema, compression=compression) as writer:
        writer.write(frame)
    return writer.final_path


def read_parquet(path: str | os.PathLike[str]) -> pl.DataFrame:
    """Baca file Parquet ke ``pl.DataFrame``."""
    return pl.read_parquet(path)
