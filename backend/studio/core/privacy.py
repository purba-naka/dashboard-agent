"""Privacy_Guard: pembatas payload data yang dikirim ke LLM (Req 27).

Fungsi murni tanpa I/O. Semua Agent_Tools yang mengembalikan data ke LLM
wajib membangun payload lewat salah satu fungsi di modul ini:

- ``build_dataset_context``: konteks satu Dataset yang HANYA berisi nama
  tabel, skema, Column_Profile, dan ``sample_rows[:settings.sample_rows]``
  (default 5; Req 27.1, 27.2). Bila ``settings.no_samples`` aktif, key
  ``sample_rows`` dihilangkan dan nilai contoh (``top_values``, serta
  ``min``/``max`` yang juga merupakan nilai asli) dihapus dari profil kolom
  bertipe string (Req 27.4, 27.5).
- ``truncate_result``: hasil query dipotong ke maksimal ``limit`` (≤ 200)
  baris beserta ``row_count`` asli dan penanda ``truncated`` (Req 27.3).

Kedua fungsi menghasilkan dict yang langsung JSON-serializable: ``date``,
``datetime``, dan ``time`` menjadi string ISO-8601, ``Decimal`` menjadi
``float``, ``timedelta`` menjadi total detik, ``bytes`` menjadi string
heksadesimal, dan float non-finite (NaN/±inf) menjadi ``None``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol

import polars as pl

from studio.core.models import ColumnInfo, ColumnProfile, QueryResult

#: Jumlah Sample_Rows default per Dataset (Req 27.2).
DEFAULT_SAMPLE_ROWS = 5
#: Batas baris hasil query yang boleh dikirim ke LLM (Req 27.3, 27.6).
MAX_RESULT_ROWS = 200

#: Field Column_Profile yang berisi nilai contoh dan dihapus untuk kolom
#: string saat mode ``no_samples`` aktif.
_STRING_EXAMPLE_FIELDS: tuple[str, ...] = ("top_values", "min", "max")


class DatasetMeta(Protocol):
    """Metadata Dataset minimal yang dibutuhkan Privacy_Guard.

    Setiap objek dengan atribut ``table_name`` dan ``schema`` memenuhi
    protokol ini (mis. record repositori Dataset). Elemen ``schema`` boleh
    berupa ``ColumnInfo`` atau mapping ``{"name", "type"}``.
    """

    @property
    def table_name(self) -> str: ...

    @property
    def schema(self) -> Sequence[ColumnInfo | Mapping[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class PrivacySettings:
    """Pengaturan privasi per Dataset.

    - ``sample_rows``: jumlah maksimum Sample_Rows yang dikirim (≥ 0).
    - ``no_samples``: toggle "jangan kirim sample rows" (Req 27.4, 27.5).
    """

    sample_rows: int = DEFAULT_SAMPLE_ROWS
    no_samples: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.sample_rows, bool) or not isinstance(self.sample_rows, int):
            raise TypeError("sample_rows harus bilangan bulat")
        if self.sample_rows < 0:
            raise ValueError(f"sample_rows harus >= 0 (didapat {self.sample_rows})")


# ---------------------------------------------------------------------------
# Konversi nilai ke bentuk JSON
# ---------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    """Konversi satu nilai sel ke nilai JSON yang aman."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    # ``datetime`` adalah subclass ``date``; keduanya punya ``isoformat``.
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return json_safe(float(value))
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, pl.Series):
        return [json_safe(v) for v in value.to_list()]
    return str(value)


def _column_info_dict(col: ColumnInfo | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(col, ColumnInfo):
        return {"name": col.name, "type": col.type}
    return {"name": str(col["name"]), "type": str(col["type"])}


def _profile_dict(profile: ColumnProfile, *, no_samples: bool) -> dict[str, Any]:
    data = profile.model_dump(mode="json")
    if no_samples and profile.type == "string":
        for key in _STRING_EXAMPLE_FIELDS:
            data.pop(key, None)
    # model_dump(mode="json") mempertahankan NaN; normalkan agar JSON valid.
    return json_safe(data)


# ---------------------------------------------------------------------------
# API publik
# ---------------------------------------------------------------------------


def build_dataset_context(
    dataset: DatasetMeta,
    profile: Sequence[ColumnProfile],
    sample: pl.DataFrame | None,
    settings: PrivacySettings,
) -> dict[str, Any]:
    """Bangun konteks Dataset untuk LLM.

    Payload berisi tepat key ``table_name``, ``schema``, ``column_profiles``,
    dan (hanya bila ``no_samples`` tidak aktif) ``sample_rows`` berupa daftar
    dict ``{kolom: nilai}`` dengan panjang ≤ ``settings.sample_rows``.
    """
    context: dict[str, Any] = {
        "table_name": dataset.table_name,
        "schema": [_column_info_dict(c) for c in dataset.schema],
        "column_profiles": [
            _profile_dict(p, no_samples=settings.no_samples) for p in profile
        ],
    }
    if not settings.no_samples:
        rows: list[dict[str, Any]] = []
        if sample is not None and settings.sample_rows > 0:
            rows = [
                {name: json_safe(value) for name, value in row.items()}
                for row in sample.head(settings.sample_rows).iter_rows(named=True)
            ]
        context["sample_rows"] = rows
    return context


def truncate_result(result: QueryResult, limit: int = MAX_RESULT_ROWS) -> dict[str, Any]:
    """Potong hasil query untuk LLM: ``{columns, rows, row_count, truncated}``.

    ``rows`` berisi maksimal ``limit`` baris pertama, ``row_count`` adalah
    jumlah baris total hasil asli, dan ``truncated`` bernilai ``True`` bila
    ada baris yang tidak ikut terkirim.
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit harus bilangan bulat")
    if not 0 <= limit <= MAX_RESULT_ROWS:
        raise ValueError(f"limit harus di antara 0 dan {MAX_RESULT_ROWS} (didapat {limit})")

    rows = [[json_safe(cell) for cell in row] for row in result.rows[:limit]]
    truncated = result.row_count > limit or len(result.rows) > limit
    return {
        "columns": [_column_info_dict(c) for c in result.columns],
        "rows": rows,
        "row_count": result.row_count,
        "truncated": truncated,
    }


__all__ = [
    "DEFAULT_SAMPLE_ROWS",
    "MAX_RESULT_ROWS",
    "DatasetMeta",
    "PrivacySettings",
    "json_safe",
    "build_dataset_context",
    "truncate_result",
]
