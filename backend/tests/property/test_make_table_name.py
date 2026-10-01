"""Feature: dashboard-studio-agent, Property 4: Nama tabel Dataset unik dan valid.

**Validates: Requirements 10.1**
"""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from studio.core.identifiers import SQL_KEYWORDS, make_table_name

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")

_TRICKY_SOURCES = st.sampled_from(
    [
        "",
        "   ",
        "Penjualan 2024.xlsx",
        "penjualan_2024.csv",
        "select.csv",
        "SELECT",
        "table",
        "from.xlsx",
        "123.csv",
        "__init__",
        "Sheet1",
        "sheet1",
        "données.csv",
        "文件.csv",
        "a-b-c",
        "a" * 80,
    ]
)
_sources = st.lists(st.one_of(_TRICKY_SOURCES, st.text(max_size=20)), min_size=1, max_size=12)
_existing = st.sets(
    st.one_of(
        st.sampled_from(["penjualan_2024", "PENJUALAN_2024_2", "Sheet1", "table_data", "t_123", "select_data"]),
        st.text(max_size=12),
    ),
    max_size=10,
)


@given(_sources, _existing)
def test_make_table_name_valid_and_unique(sources: list[str], existing: set[str]) -> None:
    """Feature: dashboard-studio-agent, Property 4: Nama tabel Dataset unik dan valid.

    **Validates: Requirements 10.1**
    """
    taken = set(existing)
    produced: list[str] = []
    for source in sources:
        name = make_table_name(source, taken)
        assert _IDENT.fullmatch(name), name
        assert name not in SQL_KEYWORDS
        assert name.lower() not in {t.lower() for t in taken}
        produced.append(name)
        taken.add(name)

    assert len(set(produced)) == len(produced)
