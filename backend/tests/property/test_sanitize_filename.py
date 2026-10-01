"""Feature: dashboard-studio-agent, Property 3: Sanitasi nama file mengurung file di folder workspace.

**Validates: Requirements 29.5**
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from studio.core.identifiers import UnsafePath, safe_join, sanitize_filename

_ULID_PREFIX = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}_")

_DANGEROUS = st.sampled_from(
    [
        "..",
        "../",
        "..\\",
        "/",
        "\\",
        "/etc/passwd",
        "C:\\Windows\\system32\\",
        "C:",
        "\\\\server\\share\\",
        "~",
        ".",
        " ",
        "\x00",
        "\n",
        "\x1f",
        "\u202e",  # right-to-left override (format char)
        "\ufeff",
        "文件",
        "données",
        "💾",
        ":",
        "*?<>|\"",
        "CON",
        "data.csv",
    ]
)
_filenames = st.lists(st.one_of(_DANGEROUS, st.text(max_size=10)), max_size=10).map("".join)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(_filenames)
def test_sanitized_name_stays_inside_workspace(tmp_path: Path, name: str) -> None:
    """Feature: dashboard-studio-agent, Property 3: Sanitasi nama file mengurung file di folder workspace.

    **Validates: Requirements 29.5**
    """
    workspace_dir = tmp_path / "data" / "uploads" / "ws_01"
    safe = sanitize_filename(name)

    assert _ULID_PREFIX.match(safe)
    assert "/" not in safe and "\\" not in safe
    assert ".." not in safe
    assert not any(unicodedata.category(ch) in ("Cc", "Cf", "Cs") for ch in safe)

    target = safe_join(workspace_dir, safe)
    base = workspace_dir.resolve()
    assert target.is_relative_to(base)
    assert target != base
    assert target.parent == base


def test_safe_join_rejects_escaping_names(tmp_path: Path) -> None:
    for bad in ["../evil.csv", "..", "", "."]:
        with pytest.raises(UnsafePath):
            safe_join(tmp_path, bad)


def test_sanitize_filename_example() -> None:
    safe = sanitize_filename("../../etc/pass..wd.csv")
    assert safe.endswith("_passwd.csv")
