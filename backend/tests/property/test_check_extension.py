"""Feature: dashboard-studio-agent, Property 2: Gerbang format file.

**Validates: Requirements 2.4**
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from studio.core.identifiers import SUPPORTED_EXTENSIONS, UnsupportedFormat, check_extension


def _random_case(ext: str) -> st.SearchStrategy[str]:
    return st.lists(st.booleans(), min_size=len(ext), max_size=len(ext)).map(
        lambda flags: "".join(c.upper() if f else c for c, f in zip(ext, flags, strict=True))
    )


_EXTENSIONS = st.sampled_from(
    [".csv", ".xlsx", ".xls", ".txt", ".parquet", ".json", ".csv.exe", ".xlsx.zip", ".csvx", "csv", ""]
).flatmap(_random_case)
_stem = st.one_of(st.text(max_size=20), st.sampled_from(["data", "report.csv", "a.xlsx", ".", ""]))
_filenames = st.one_of(st.builds(lambda s, e: s + e, _stem, _EXTENSIONS), st.text(max_size=30))


def _expected_extension(name: str) -> str | None:
    """Oracle: sufiks ASCII case-insensitive ``.csv`` / ``.xlsx``."""
    if name[-4:].lower() == ".csv":
        return ".csv"
    if name[-5:].lower() == ".xlsx":
        return ".xlsx"
    return None


@given(_filenames)
def test_check_extension_accepts_iff_supported(name: str) -> None:
    """Feature: dashboard-studio-agent, Property 2: Gerbang format file.

    **Validates: Requirements 2.4**
    """
    expected = _expected_extension(name)
    if expected is not None:
        assert check_extension(name) == expected
    else:
        with pytest.raises(UnsupportedFormat) as exc_info:
            check_extension(name)
        err = exc_info.value
        assert err.code == "UNSUPPORTED_FORMAT"
        assert err.http_status == 415
        assert err.details["supported"] == list(SUPPORTED_EXTENSIONS)
        for ext in SUPPORTED_EXTENSIONS:
            assert ext in err.message


def test_check_extension_examples() -> None:
    assert check_extension("Penjualan.CSV") == ".csv"
    assert check_extension("laporan.Xlsx") == ".xlsx"
    with pytest.raises(UnsupportedFormat):
        check_extension("lama.xls")
