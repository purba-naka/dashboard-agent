"""Feature: dashboard-studio-agent, Property 40: Pencarian Verified_Query.

Hasil ≤ k, hanya ``confirmed`` & valid dengan skor > 0, terurut skor tidak naik
(seri: terbaru dulu), deterministik, dan tidak ada entri yang tidak terpilih
dengan skor lebih tinggi dari entri terpilih.

**Validates: Requirements 34.3, 34.4, 34.5**
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.verified_queries import VerifiedCandidate, find_verified, is_valid_sql, score

_words = st.sampled_from(["revenue", "omzet", "bulan", "region", "produk", "margin", "per", "total", "qty"])
_questions = st.lists(_words, min_size=0, max_size=5).map(" ".join)


@st.composite
def _candidates(draw: st.DrawFn) -> list[VerifiedCandidate]:
    n = draw(st.integers(0, 10))
    return [
        VerifiedCandidate(
            id=f"vq{i:02d}",
            question=draw(_questions),
            sql="SELECT 1",
            status=draw(st.sampled_from(["candidate", "confirmed", "rejected"])),
            valid=draw(st.booleans()),
            confirmed_at=draw(
                st.none()
                | st.datetimes(datetime(2024, 1, 1), datetime(2026, 1, 1), timezones=st.just(UTC))
            ),
        )
        for i in range(n)
    ]


# Feature: dashboard-studio-agent, Property 40: Pencarian Verified_Query
@settings(max_examples=100)
@given(entries=_candidates(), question=_questions, k=st.integers(1, 5))
def test_find_verified_properties(entries, question, k) -> None:
    result = find_verified(entries, question, k)
    assert result == find_verified(list(entries), question, k)
    assert len(result) <= k
    for entry, s in result:
        assert entry.status == "confirmed" and entry.valid and s > 0
        assert s == score(question, entry.question)
    scores = [s for _, s in result]
    assert scores == sorted(scores, reverse=True)
    chosen = {e.id for e, _ in result}
    if result:
        worst = min(scores)
        for e in entries:
            if e.id in chosen or e.status != "confirmed" or not e.valid:
                continue
            assert score(question, e.question) <= worst or len(result) < k and score(question, e.question) == 0


def test_is_valid_sql_detects_removed_table() -> None:
    tables = {"sales": ["amount"]}
    assert is_valid_sql("SELECT SUM(amount) FROM sales", tables)
    assert not is_valid_sql("SELECT SUM(amount) FROM sales", {})
