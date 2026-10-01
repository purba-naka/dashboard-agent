"""Feature: dashboard-studio-agent, Property 19: Verifikasi angka insight.

**Validates: Requirements 14.3, 14.4**
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from studio.core.models import ColumnInfo, EvidenceTable, InPredicate
from studio.core.numbers import (
    InsightNumberMismatch,
    allowed_values,
    extract_numbers,
    format_number,
    interpretation_matches,
    verify_insight_numbers,
)

# Skala yang didukung format_number (termasuk persen dan auto).
_SCALES = [None, "rb", "ribu", "jt", "juta", "M", "miliar", "T", "triliun", "%", "persen", "auto"]
# Kata penghubung aman: tidak diawali sufiks skala (M/T/rb/juta/...).
_JOINERS = [" dan ", " serta ", "; lalu ", ", kemudian "]

_numeric = st.one_of(
    st.integers(min_value=-(10**13), max_value=10**13),
    st.floats(min_value=-1e13, max_value=1e13, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
_label = st.sampled_from(["Jawa", "Sumatra", "2024-01", "Q1", "SKU123", "Bali"])


@st.composite
def _evidence_and_filters(draw: st.DrawFn) -> tuple[EvidenceTable, list[InPredicate], list]:
    """Evidence acak + filter aktif, serta pool nilai numerik yang boleh disebut."""
    n_num = draw(st.integers(min_value=1, max_value=3))
    with_label = draw(st.booleans())
    n_rows = draw(st.integers(min_value=1, max_value=6))
    columns = [ColumnInfo(name="label", type="string")] if with_label else []
    columns += [ColumnInfo(name=f"m{i}", type="float") for i in range(n_num)]
    rows: list[list] = []
    pool: list = []
    for _ in range(n_rows):
        nums = [draw(_numeric) for _ in range(n_num)]
        pool.extend(nums)
        rows.append(([draw(_label)] if with_label else []) + nums)
    row_count = draw(st.integers(min_value=n_rows, max_value=10**6))
    pool.append(row_count)
    filter_values = draw(st.lists(st.integers(min_value=0, max_value=10**5), max_size=3))
    filters = (
        [InPredicate(table="t", column="kode", values=tuple(filter_values))] if filter_values else []
    )
    pool.extend(filter_values)
    evidence = EvidenceTable(columns=columns, rows=rows, row_count=row_count)
    return evidence, filters, pool


_formatting = st.tuples(st.integers(min_value=0, max_value=3), st.sampled_from(_SCALES))


@st.composite
def _insight_text(draw: st.DrawFn) -> tuple[EvidenceTable, list[InPredicate], str, int]:
    """Teks insight yang disusun hanya dari nilai evidence/filter via ``format_number``."""
    evidence, filters, pool = draw(_evidence_and_filters())
    k = draw(st.integers(min_value=1, max_value=5))
    parts = []
    for _ in range(k):
        value = draw(st.sampled_from(pool))
        decimals, scale = draw(_formatting)
        parts.append(format_number(value, decimals, scale))
    joiners = [draw(st.sampled_from(_JOINERS)) for _ in range(k - 1)]
    text = "Nilai " + parts[0] + "".join(j + p for j, p in zip(joiners, parts[1:])) + "."
    return evidence, filters, text, k


@given(_insight_text())
def test_text_built_from_result_values_is_accepted(
    case: tuple[EvidenceTable, list[InPredicate], str, int],
) -> None:
    """Feature: dashboard-studio-agent, Property 19: Verifikasi angka insight.

    **Validates: Requirements 14.3, 14.4**
    """
    evidence, filters, text, k = case
    matches = verify_insight_numbers(text, evidence, filters)
    # Setiap bilangan yang ditulis tercatat sebagai satu NumberMatch.
    assert len(matches) == len(extract_numbers(text)) == k


@given(
    _insight_text(),
    st.one_of(
        st.integers(min_value=-(10**12), max_value=10**12),
        st.floats(min_value=-1e12, max_value=1e12, allow_nan=False, allow_infinity=False),
    ),
    _formatting,
)
def test_foreign_number_is_rejected_and_reported(
    case: tuple[EvidenceTable, list[InPredicate], str, int],
    foreign: int | float,
    formatting: tuple[int, str | None],
) -> None:
    """Feature: dashboard-studio-agent, Property 19: Verifikasi angka insight.

    **Validates: Requirements 14.3, 14.4**
    """
    evidence, filters, text, _ = case
    decimals, scale = formatting
    foreign_text = format_number(foreign, decimals, scale)
    tokens = extract_numbers(foreign_text)
    assert len(tokens) == 1
    (foreign_tok,) = tokens

    # Bilangan harus benar-benar tidak dapat dicocokkan pada presisi tampilannya
    # (termasuk negasi nilai yang diizinkan, karena pencocokan mengabaikan tanda).
    allowed = [av.value for av in allowed_values(evidence, filters)]
    assume(
        not any(
            interpretation_matches(i, v) for i in foreign_tok.interpretations for v in allowed
        )
    )

    bad_text = text[:-1] + " serta " + foreign_text + "."
    with pytest.raises(InsightNumberMismatch) as exc_info:
        verify_insight_numbers(bad_text, evidence, filters)
    # Hanya bilangan asing itu yang tidak cocok; angka lain tetap diterima.
    assert exc_info.value.unmatched == [foreign_tok.text]
    assert exc_info.value.code == "INSIGHT_NUMBER_MISMATCH"


def test_known_examples() -> None:
    """Feature: dashboard-studio-agent, Property 19: Verifikasi angka insight.

    **Validates: Requirements 14.3, 14.4**
    """
    evidence = EvidenceTable(
        columns=[ColumnInfo(name="bulan", type="string"), ColumnInfo(name="growth", type="float")],
        rows=[["2024-01", -0.125], ["2024-02", 1234567.891]],
        row_count=2,
    )
    ok = "Pada 2024-01 penjualan turun 12,5%, lalu naik ke 1,2 juta (1.234.567,89)."
    assert len(verify_insight_numbers(ok, evidence)) == 5
    with pytest.raises(InsightNumberMismatch) as exc_info:
        verify_insight_numbers("Penjualan naik 37%.", evidence)
    assert exc_info.value.unmatched == ["37%"]
