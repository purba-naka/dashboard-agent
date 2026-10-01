"""Verifikasi angka pada teks Insight_Card terhadap hasil query (murni).

Lihat design "Insight number verification":

1. ``extract_numbers(text)`` mengenali bilangan format id-ID (``1.234.567,89``)
   maupun en (``1,234,567.89``), persen (``12,5%`` / ``12,5 persen``), tanda
   minus, dan sufiks skala (``rb``/``ribu`` = 1e3, ``jt``/``juta`` = 1e6,
   ``M``/``miliar`` = 1e9, ``T``/``triliun`` = 1e12). Token ambigu (mis.
   ``1.234``) menghasilkan beberapa interpretasi (``1234`` dan ``1,234``).
2. ``allowed_values(evidence, filters)``: sel numerik hasil query, bilangan di
   dalam sel string/tanggal (mis. ``2024`` dan ``1`` dari ``"2024-01"``), nilai
   filter aktif, dan ``row_count``.
3. Token ``n`` dengan ``d`` digit desimal cocok dengan nilai ``v`` bila ada
   interpretasi sehingga ``round(|v| / skala, d) == |n|``; untuk persen
   ``round(|v| * 100, d) == |n|`` atau ``round(|v|, d) == |n|``. Pembulatan
   diterima baik half-up (dipakai ``format_number``) maupun half-even.
   Pencocokan tidak memperhatikan tanda agar kalimat seperti "turun 12,5%"
   untuk nilai ``-0.125`` tetap diterima.
4. Token yang tidak cocok → ``InsightNumberMismatch(unmatched)`` (Req 14.3, 14.4).

``check_insight_type`` memastikan ``cross_dataset_correlation`` hanya dipakai
bila query memakai >= 2 tabel dan minimal satu Confirmed_Relation (Req 14.6).

Aturan tokenisasi (diterapkan sama pada teks dan sel string evidence):

- Bilangan yang menempel pada huruf/digit/underscore di kirinya diabaikan
  (``Q1``, ``SKU123``), kecuali prefiks mata uang ``Rp`` (``Rp1.234``).
- Bilangan setelah pola huruf-hubung diabaikan (``ke-2``, ``COVID-19``);
  setelah digit-hubung tetap diambil (``2024-01`` → ``2024``, ``01``).
- Minus hanya dianggap tanda bila tidak menempel pada karakter kata di
  kirinya (``2024-01`` bukan ``-1``).
- Deret dengan pemisah yang tidak valid (``01.02.2024``, ``1,2,3``) dipecah
  menjadi bilangan bulat per kelompok digit.
"""

from __future__ import annotations

import math
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any, Literal, get_args

from studio.api.errors import StudioError
from studio.core.models import EvidenceTable, InsightType, NumberMatch, QueryResult

# ---------------------------------------------------------------------------
# Konstanta
# ---------------------------------------------------------------------------

#: Sufiks skala → pengali. Kunci huruf kecil untuk sufiks kata (case-insensitive);
#: ``M`` dan ``T`` hanya dikenali dalam huruf besar.
SCALE_SUFFIXES: dict[str, int] = {
    "rb": 10**3,
    "ribu": 10**3,
    "jt": 10**6,
    "juta": 10**6,
    "M": 10**9,
    "miliar": 10**9,
    "milyar": 10**9,
    "T": 10**12,
    "triliun": 10**12,
}

#: Skala yang dipilih ``format_number(..., scale="auto")`` (terbesar lebih dulu).
_AUTO_SCALES: tuple[tuple[str, int], ...] = (
    ("triliun", 10**12),
    ("miliar", 10**9),
    ("juta", 10**6),
    ("ribu", 10**3),
)

_PERCENT_SCALES = frozenset({"%", "persen"})

#: Presisi lebar agar float hingga ~1e308 dapat di-quantize tanpa error.
_CTX = Context(prec=400)

_NUMBER_RE = re.compile(
    r"""
    (?:(?<!\w)(?P<sign>[-+\u2212]))?          # tanda, bila tidak menempel kata
    (?:(?<=Rp)|(?<!\w))                       # tidak menempel huruf/digit (kecuali Rp)
    (?<![^\W\d]-)                             # bukan "ke-2" / "COVID-19"
    (?P<body>\d+(?:[.,]\d+)*)
    (?:
        (?P<pct>\s?(?:%|(?i:persen)\b))
      | \s?(?P<scale>(?i:ribu|rb|juta|jt|miliar|milyar|triliun)\b|M\b|T\b)
    )?
    """,
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------


class InsightNumberMismatch(StudioError):
    """Teks insight memuat angka yang tidak dapat dicocokkan dengan hasil query (Req 14.4)."""

    def __init__(self, unmatched: Sequence[str]) -> None:
        self.unmatched: list[str] = list(unmatched)
        listed = ", ".join(self.unmatched)
        super().__init__(
            "INSIGHT_NUMBER_MISMATCH",
            f"Angka berikut tidak dapat dicocokkan dengan hasil query tertaut: {listed}. "
            "Gunakan hanya angka dari hasil query (format dengan format_number).",
            {"unmatched": self.unmatched},
            http_status=422,
        )


class InvalidInsightType(StudioError):
    """Tipe insight tidak sesuai dengan query sumbernya (Req 14.6)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("INVALID_INSIGHT_TYPE", message, details, http_status=422)


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberInterpretation:
    """Satu cara membaca token.

    ``number`` adalah bilangan yang tertulis (bertanda, tanpa skala);
    ``decimals`` jumlah digit desimal yang tertulis; ``scale`` pengali sufiks
    (1 bila tanpa sufiks); ``percent`` bila token diakhiri ``%``/``persen``.
    """

    number: Decimal
    decimals: int
    scale: int = 1
    percent: bool = False

    @property
    def value(self) -> Decimal:
        """Nilai nominal: ``number * scale`` (untuk persen: angka yang tertulis)."""
        return _CTX.multiply(self.number, Decimal(self.scale))


@dataclass(frozen=True)
class NumberToken:
    text: str
    start: int
    end: int
    interpretations: tuple[NumberInterpretation, ...]


def _valid_lead(group: str) -> bool:
    """Kelompok pertama bilangan berpemisah ribuan: 1–3 digit, tanpa nol di depan."""
    return 1 <= len(group) <= 3 and not group.startswith("0")


def _valid_grouping(groups: Sequence[str]) -> bool:
    return _valid_lead(groups[0]) and all(len(g) == 3 for g in groups[1:])


def _parse_body(body: str) -> list[tuple[Decimal, int]] | None:
    """Interpretasi ``(bilangan, digit_desimal)`` tanpa tanda; ``None`` bila tidak valid."""
    groups = re.split(r"[.,]", body)
    seps = [c for c in body if c in ".,"]
    if not seps:
        return [(Decimal(body), 0)]
    if len(seps) == 1:
        head, tail = groups
        interps = [(Decimal(f"{head}.{tail}"), len(tail))]
        if len(tail) == 3 and _valid_lead(head):
            interps.insert(0, (Decimal(head + tail), 0))
        return interps
    if len(set(seps)) == 1:
        # "1.234.567" / "1,234,567": hanya pemisah ribuan.
        return [(Decimal("".join(groups)), 0)] if _valid_grouping(groups) else None
    thousands, decimal_sep = seps[:-1], seps[-1]
    if len(set(thousands)) == 1 and thousands[0] != decimal_sep and _valid_grouping(groups[:-1]):
        # "1.234.567,89" (id-ID) / "1,234,567.89" (en).
        return [(Decimal("".join(groups[:-1]) + "." + groups[-1]), len(groups[-1]))]
    return None


def extract_numbers(text: str) -> list[NumberToken]:
    """Ekstrak semua bilangan pada ``text`` beserta interpretasinya (urut kemunculan)."""
    tokens: list[NumberToken] = []
    for m in _NUMBER_RE.finditer(text):
        sign = m.group("sign")
        negative = sign in ("-", "\u2212")
        body = m.group("body")
        percent = m.group("pct") is not None
        scale_raw = m.group("scale")
        scale = 1
        if scale_raw is not None:
            key = scale_raw if scale_raw in ("M", "T") else scale_raw.lower()
            scale = SCALE_SUFFIXES[key]

        parsed = _parse_body(body)
        if parsed is not None:
            interps = tuple(
                NumberInterpretation(-n if negative else n, d, scale, percent) for n, d in parsed
            )
            tokens.append(NumberToken(m.group(0).strip(), m.start(), m.end(), interps))
            continue

        # Pemisah tidak valid: pecah per kelompok digit (tanda ke kelompok pertama,
        # sufiks ke kelompok terakhir).
        pieces = list(re.finditer(r"\d+", body))
        body_start = m.start("body")
        for i, piece in enumerate(pieces):
            first, last = i == 0, i == len(pieces) - 1
            start = m.start() if first else body_start + piece.start()
            end = m.end() if last else body_start + piece.end()
            n = Decimal(piece.group(0))
            interp = NumberInterpretation(
                -n if (first and negative) else n,
                0,
                scale if last else 1,
                percent if last else False,
            )
            tokens.append(NumberToken(text[start:end].strip(), start, end, (interp,)))
    return tokens


# ---------------------------------------------------------------------------
# Nilai yang diizinkan
# ---------------------------------------------------------------------------

AllowedSource = Literal["cell", "string_cell", "filter", "row_count"]


@dataclass(frozen=True)
class AllowedValue:
    value: Decimal
    source: AllowedSource
    row: int | None = None
    column: str | None = None


def _to_decimal(v: Any) -> Decimal | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v)) if math.isfinite(v) else None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    return None


def _values_in_string(s: str) -> list[Decimal]:
    return [interp.value for tok in extract_numbers(s) for interp in tok.interpretations]


def _scalar_values(v: Any) -> tuple[list[Decimal], bool]:
    """``(nilai, berasal_dari_string)`` untuk satu sel/nilai filter."""
    if v is None or isinstance(v, bool):
        return [], False
    num = _to_decimal(v)
    if num is not None:
        return [num], False
    if isinstance(v, (int, float, Decimal)):  # NaN / inf
        return [], False
    if isinstance(v, datetime):
        return _values_in_string(v.isoformat(sep=" ")), True
    if isinstance(v, (date, time)):
        return _values_in_string(v.isoformat()), True
    return _values_in_string(str(v)), True


def _pred_get(pred: Any, key: str) -> Any:
    if isinstance(pred, dict):
        return pred.get(key)
    return getattr(pred, key, None)


def allowed_values(
    evidence: EvidenceTable | QueryResult,
    filters: Iterable[Any] | None = None,
) -> list[AllowedValue]:
    """Semua nilai yang boleh muncul di teks insight (urutan: sel, filter, ``row_count``).

    ``filters`` berisi predikat aktif (model ``Predicate``, bentuk frozen dari
    ``core/filters.py``, atau dict dengan kunci ``start``/``end``/``values``).
    """
    out: list[AllowedValue] = []
    names = [c.name for c in evidence.columns]
    for r, row in enumerate(evidence.rows):
        for c, cell in enumerate(row):
            column = names[c] if c < len(names) else None
            values, from_string = _scalar_values(cell)
            source: AllowedSource = "string_cell" if from_string else "cell"
            out.extend(AllowedValue(v, source, r, column) for v in values)

    for pred in filters or ():
        column = _pred_get(pred, "column")
        raw: list[Any] = [_pred_get(pred, "start"), _pred_get(pred, "end")]
        raw.extend(_pred_get(pred, "values") or ())
        for item in raw:
            values, _ = _scalar_values(item)
            out.extend(AllowedValue(v, "filter", None, column) for v in values)

    out.append(AllowedValue(Decimal(evidence.row_count), "row_count"))
    return out


# ---------------------------------------------------------------------------
# Pencocokan & verifikasi
# ---------------------------------------------------------------------------


def _rounds_to(x: Decimal, decimals: int, target: Decimal) -> bool:
    exp = Decimal(1).scaleb(-decimals)
    for rounding in (ROUND_HALF_UP, ROUND_HALF_EVEN):
        try:
            if x.quantize(exp, rounding=rounding, context=_CTX) == target:
                return True
        except InvalidOperation:
            return False
    return False


def interpretation_matches(interp: NumberInterpretation, v: Decimal) -> bool:
    """Apakah ``interp`` merupakan tampilan (dibulatkan) dari nilai ``v``."""
    target = abs(interp.number)
    mag = abs(v)
    if interp.percent:
        candidates = [_CTX.multiply(mag, Decimal(100)), mag]
    else:
        candidates = [_CTX.divide(mag, Decimal(interp.scale))]
    return any(_rounds_to(x, interp.decimals, target) for x in candidates)


def verify_insight_numbers(
    text: str,
    evidence: EvidenceTable | QueryResult,
    filters: Iterable[Any] | None = None,
) -> list[NumberMatch]:
    """Cocokkan setiap bilangan di ``text`` dengan nilai yang diizinkan.

    Mengembalikan satu ``NumberMatch`` per token (provenance = nilai pertama
    yang cocok); memunculkan ``InsightNumberMismatch`` bila ada token yang
    tidak cocok.
    """
    allowed = allowed_values(evidence, filters)
    matches: list[NumberMatch] = []
    unmatched: list[str] = []
    for tok in extract_numbers(text):
        found = next(
            (
                av
                for av in allowed
                if any(interpretation_matches(i, av.value) for i in tok.interpretations)
            ),
            None,
        )
        if found is None:
            unmatched.append(tok.text)
        else:
            matches.append(
                NumberMatch(token=tok.text, value=float(found.value), row=found.row, column=found.column)
            )
    if unmatched:
        raise InsightNumberMismatch(unmatched)
    return matches


# ---------------------------------------------------------------------------
# Format id-ID
# ---------------------------------------------------------------------------


def _group_thousands(digits: str) -> str:
    head = len(digits) % 3 or 3
    parts = [digits[:head]] + [digits[i : i + 3] for i in range(head, len(digits), 3)]
    return ".".join(parts)


def format_number(v: float | int | Decimal, decimals: int = 0, scale: str | None = None) -> str:
    """Format bilangan gaya id-ID (ribuan ``.``, desimal ``,``, pembulatan half-up).

    ``scale``: ``None`` (apa adanya), sufiks pada ``SCALE_SUFFIXES``
    (``"rb"``, ``"juta"``, ``"M"``, ...), ``"%"``/``"persen"`` (nilai dikali 100),
    atau ``"auto"`` (ribu/juta/miliar/triliun sesuai besaran).

    Contoh: ``format_number(1234567.891, 2) == "1.234.567,89"``,
    ``format_number(1234567, 1, "juta") == "1,2 juta"``,
    ``format_number(0.125, 1, "%") == "12,5%"``.
    """
    if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0:
        raise ValueError("decimals harus bilangan bulat >= 0")
    num = _to_decimal(v)
    if num is None:
        raise ValueError(f"format_number membutuhkan bilangan hingga, bukan {v!r}")

    suffix = ""
    if scale == "auto":
        scale = next((name for name, mult in _AUTO_SCALES if abs(num) >= mult), None)
    if scale in _PERCENT_SCALES:
        num = _CTX.multiply(num, Decimal(100))
        suffix = "%" if scale == "%" else " persen"
    elif scale:
        key = scale if scale in ("M", "T") else scale.lower()
        if key not in SCALE_SUFFIXES:
            raise ValueError(f"scale tidak dikenal: {scale!r}")
        num = _CTX.divide(num, Decimal(SCALE_SUFFIXES[key]))
        suffix = f" {scale}"

    rounded = num.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP, context=_CTX)
    text = format(abs(rounded), "f")
    int_part, _, frac_part = text.partition(".")
    out = _group_thousands(int_part)
    if decimals:
        out += "," + frac_part
    if rounded < 0 and not rounded.is_zero():
        out = "-" + out
    return out + suffix


# ---------------------------------------------------------------------------
# Tipe insight
# ---------------------------------------------------------------------------

_INSIGHT_TYPES = frozenset(get_args(InsightType))


def check_insight_type(
    insight_type: str,
    tables_used: int | Collection[str],
    relations_used: Iterable[str] | None,
) -> None:
    """``cross_dataset_correlation`` hanya sah bila tabel >= 2 dan ada relasi (Req 14.6)."""
    table_count = tables_used if isinstance(tables_used, int) else len(set(tables_used))
    relations = list(relations_used or [])
    details = {
        "insight_type": insight_type,
        "tables_used": table_count if isinstance(tables_used, int) else sorted(set(tables_used)),
        "relations_used": relations,
    }
    if insight_type not in _INSIGHT_TYPES:
        raise InvalidInsightType(
            f"Tipe insight '{insight_type}' tidak dikenal; pilih salah satu: "
            f"{', '.join(sorted(_INSIGHT_TYPES))}.",
            details,
        )
    if insight_type == "cross_dataset_correlation" and (table_count < 2 or not relations):
        raise InvalidInsightType(
            "Insight 'cross_dataset_correlation' membutuhkan query yang menggabungkan "
            "minimal 2 tabel melalui Confirmed_Relation.",
            details,
        )


__all__ = [
    "SCALE_SUFFIXES",
    "InsightNumberMismatch",
    "InvalidInsightType",
    "NumberInterpretation",
    "NumberToken",
    "AllowedValue",
    "extract_numbers",
    "allowed_values",
    "interpretation_matches",
    "verify_insight_numbers",
    "format_number",
    "check_insight_type",
]
