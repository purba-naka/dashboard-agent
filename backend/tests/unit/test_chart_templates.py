"""Templat chart: pembentuk murni + tool agent (gate, retry, kolom salah, kompatibilitas)."""

import pytest

from studio.agents.tools.dashboard_tools import CHART_SPEC_TOOLS, DASHBOARD_TOOL_NAMES
from studio.agents.turn_policy import MUTATING_TOOLS
from studio.api.errors import StudioError
from studio.core.chart_spec import validate_chart_spec
from studio.core.chart_templates import TEMPLATES, build_chart_option
from studio.core.models import ColumnInfo

COLS = [ColumnInfo(name="region", type="string"), ColumnInfo(name="revenue", type="float")]


@pytest.mark.parametrize("template", TEMPLATES)
def test_option_lolos_validator(template):
    option = build_chart_option(template, "Revenue", "region", "revenue")
    spec = validate_chart_spec({"query_id": "q", "chart_type": template, "option": option}, COLS)
    assert spec.option == option


def test_kolom_tidak_ada_ditolak_validator():
    option = build_chart_option("bar", "T", "region", "tidak_ada")
    with pytest.raises(Exception) as exc:
        validate_chart_spec({"query_id": "q", "chart_type": "bar", "option": option}, COLS)
    assert "UNKNOWN_COLUMN" in str(exc.value) or getattr(exc.value, "code", "") == "UNKNOWN_COLUMN"


@pytest.mark.parametrize("args", [("radar", "T", "a", "b"), ("bar", "", "a", "b"), ("bar", "T", " ", "b")])
def test_argumen_salah(args):
    with pytest.raises(StudioError):
        build_chart_option(*args)


def test_option_jauh_lebih_ringkas_dari_add_chart_penuh():
    # Yang ditulis agent: 4 argumen pendek, bukan struktur ECharts bersarang.
    agent_args = len("bar") + len("Revenue") + len("region") + len("revenue")
    assert agent_args < 40 < len(str(build_chart_option("bar", "Revenue", "region", "revenue")))


def test_terdaftar_di_semua_tempat():
    assert "add_chart_from_template" in DASHBOARD_TOOL_NAMES
    assert "add_chart_from_template" in MUTATING_TOOLS  # approval gate
    assert "add_chart_from_template" in CHART_SPEC_TOOLS  # jatah retry chart_spec
