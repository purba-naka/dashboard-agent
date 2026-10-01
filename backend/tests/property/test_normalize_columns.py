"""Feature: dashboard-studio-agent, Property 1: Normalisasi nama kolom menghasilkan nama unik dan tidak kosong.

**Validates: Requirements 2.5**
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from studio.core.identifiers import normalize_columns

# Pool nama "berbahaya": kosong, whitespace, duplikat, dan nama yang menyerupai
# hasil generate (``column_1``, ``a_2``) agar tabrakan sufiks ikut teruji.
_TRICKY = st.sampled_from(
    ["", " ", "\t", "  \n", "a", " a ", "a_2", "a_3", "column_1", "column_2", "column_1_2", "B", "b"]
)
_column_name = st.one_of(_TRICKY, st.text(max_size=12))
_column_lists = st.lists(_column_name, max_size=15)


@given(_column_lists)
def test_normalize_columns_unique_nonempty_and_idempotent(names: list[str]) -> None:
    """Feature: dashboard-studio-agent, Property 1: Normalisasi nama kolom menghasilkan nama unik dan tidak kosong.

    **Validates: Requirements 2.5**
    """
    normalized, mapping = normalize_columns(names)

    # Panjang sama, setiap nama tidak kosong, dan semua unik.
    assert len(normalized) == len(names)
    assert all(n.strip() for n in normalized)
    assert len(set(normalized)) == len(normalized)

    # Mapping sejajar per posisi.
    assert len(mapping) == len(names)
    for original, entry, norm in zip(names, mapping, normalized, strict=True):
        assert entry == {"original": original, "normalized": norm}

    # Nama yang sudah unik dan tidak kosong (setelah trim) tidak berubah.
    trimmed = [n.strip() for n in names]
    for i, name in enumerate(trimmed):
        if name and trimmed.count(name) == 1:
            assert normalized[i] == name

    # Idempoten: normalisasi dua kali sama dengan sekali.
    again, _ = normalize_columns(normalized)
    assert again == normalized


def test_normalize_columns_examples() -> None:
    """Contoh konkret: kosong → column_i, duplikat → sufiks yang tidak bertabrakan."""
    normalized, _ = normalize_columns(["a", " a ", "", "a_2"])
    assert normalized == ["a", "a_3", "column_3", "a_2"]
