"""KPI_Card: validasi spesifikasi dan perhitungan nilai (murni; tanpa I/O).

- ``validate_kpi_spec(spec, schema)``: kolom nilai/pembanding wajib ada di
  skema hasil query dan bertipe numerik (Req 38.2, 38.3).
- ``compute_kpi(spec, result)``: nilai, pembanding, delta, delta persen,
  sentimen, dan string terformat id-ID (Req 38.4, 38.5, 38.9, 38.10).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from studio.api.errors import StudioError
from studio.core.models import ColumnInfo, KpiSpec, NumberFormat, QueryResult
from studio.core.numbers import format_number

__all__ = [
    "KpiSpecInvalid",
    "KpiShapeError",
    "KpiValue",
    "validate_kpi_spec",
    "format_kpi_number",
    "kpi_sentiment",
    "compute_kpi",
]

_NUMERIC_TYPES = frozenset({"integer", "float"})
_COMPACT_MIN = 1000

Sentiment = Literal["positive", "negative", "neutral"]


class KpiShapeError(StudioError):
    """Hasil query KPI tidak berjumlah tepat satu baris (Req 38.5)."""

    def __init__(self, row_count: int) -> None:
        super().__init__(
            "KPI_SHAPE",
            f"Query KPI harus menghasilkan tepat satu baris (didapat {row_count}).",
            {"row_count": row_count},
            http_status=422,
        )


class KpiValue(BaseModel):
    """Hasil render KPI_Card (tanpa LLM)."""

    model_config = ConfigDict(extra="forbid")

    value: float | None
    comparison: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    sentiment: Sentiment = "neutral"
    formatted: dict[str, str | None] = Field(default_factory=dict)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    return None


def format_kpi_number(value: float | None, fmt: NumberFormat, *, signed: bool = False) -> str | None:
    """String id-ID untuk satu nilai KPI sesuai ``fmt`` (``None`` bila nilai kosong).

    ``percent`` memperlakukan nilai sebagai rasio (0,125 → ``12,5%``);
    ``currency`` IDR berprefiks ``Rp``; ``compact`` memilih rb/jt/M/T otomatis.
    """
    if value is None:
        return None
    if fmt.style == "percent":
        text = format_number(value, fmt.decimals, "%")
    else:
        scale = "auto" if fmt.compact and abs(value) >= _COMPACT_MIN else None
        text = format_number(value, fmt.decimals, scale)
        if fmt.style == "currency":
            prefix = "Rp" if (fmt.currency or "IDR").upper() == "IDR" else (fmt.currency or "").upper()
            negative = text.startswith("-")
            body = text[1:] if negative else text
            text = f"{'-' if negative else ''}{prefix} {body}"
    if signed and value > 0:
        text = "+" + text
    return text


def kpi_sentiment(delta: float | None, good_direction: str) -> Sentiment:
    """Positif tepat ketika tanda delta searah arah nilai yang baik (Req 38.9)."""
    if good_direction == "neutral" or delta is None or delta == 0:
        return "neutral"
    wanted = 1 if good_direction == "up" else -1
    return "positive" if (1 if delta > 0 else -1) == wanted else "negative"


def compute_kpi(spec: KpiSpec, result: QueryResult) -> KpiValue:
    """Nilai, pembanding, delta, sentimen, dan string terformat (Req 38.4, 38.5)."""
    if len(result.rows) != 1:
        raise KpiShapeError(len(result.rows))
    names = [c.name for c in result.columns]
    row = result.rows[0]

    def cell(column: str | None) -> float | None:
        if column is None or column not in names:
            return None
        return _num(row[names.index(column)])

    value = cell(spec.value_column)
    comparison = cell(spec.comparison_column)
    delta = None if value is None or comparison is None else value - comparison
    delta_pct = (
        None if delta is None or comparison in (None, 0) else delta / abs(comparison) * 100  # type: ignore[arg-type]
    )
    if delta_pct is not None and not math.isfinite(delta_pct):
        delta_pct = None  # pembanding mendekati nol (mis. subnormal): persentase tak bermakna
    fmt = spec.format
    pct_fmt = NumberFormat(style="number", decimals=1, compact=False)
    return KpiValue(
        value=value,
        comparison=comparison,
        delta=delta,
        delta_pct=delta_pct,
        sentiment=kpi_sentiment(delta, spec.good_direction),
        formatted={
            "value": format_kpi_number(value, fmt),
            "comparison": format_kpi_number(comparison, fmt),
            "delta": format_kpi_number(delta, fmt, signed=True),
            "delta_pct": None
            if delta_pct is None
            else (format_kpi_number(delta_pct, pct_fmt, signed=True) or "") + "%",
        },
    )


class KpiSpecInvalid(StudioError):
    """Spesifikasi KPI tidak cocok dengan skema hasil query."""

    def __init__(self, column: str | None, reason: str, extra: dict[str, Any] | None = None) -> None:
        details: dict[str, Any] = {"column": column, "reason": reason}
        if extra:
            details.update(extra)
        super().__init__(
            "KPI_SPEC_INVALID",
            f"Spesifikasi KPI tidak valid: {reason}",
            details,
            http_status=422,
        )


def validate_kpi_spec(spec: KpiSpec, schema: Sequence[ColumnInfo]) -> KpiSpec:
    """Raise ``KpiSpecInvalid`` bila kolom tidak ada atau bukan numerik."""
    types = {c.name: c.type for c in schema}
    available = sorted(types)
    for role, column in (("value_column", spec.value_column), ("comparison_column", spec.comparison_column)):
        if column is None:
            continue
        if column not in types:
            raise KpiSpecInvalid(
                column,
                f"{role} '{column}' tidak ada pada hasil query; kolom tersedia: {available}",
                {"available": available},
            )
        if types[column] not in _NUMERIC_TYPES:
            raise KpiSpecInvalid(
                column,
                f"{role} '{column}' bertipe {types[column]}, bukan numerik",
            )
    return spec
