"""Setiap contoh JSON di prompt Chart_Designer_Agent harus lolos validator.

Agent meniru contoh di prompt; contoh yang ditolak backend = agent belajar spec rusak.
"""

from __future__ import annotations

import json
import re

import pytest

from studio.agents.context import load_prompt
from studio.core.chart_spec import validate_chart_spec
from studio.core.models import ColumnInfo

COLUMNS = [
    ColumnInfo(name=n, type=t)
    for n, t in [
        ("bulan", "date"),
        ("region", "string"),
        ("revenue", "float"),
        ("qty", "integer"),
        ("harga", "float"),
        ("biaya", "float"),
        ("hari", "string"),
        ("jam", "integer"),
    ]
]

EXAMPLES = [
    json.loads(block)
    for block in re.findall(r"```json\n(.*?)```", load_prompt("chart_rules"), re.DOTALL)
]


def test_prompt_punya_contoh_untuk_semua_tipe() -> None:
    assert {e["chart_type"] for e in EXAMPLES} == {"line", "bar", "pie", "scatter", "heatmap"}


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e["option"]["title"]["text"])
def test_contoh_prompt_lolos_validator(example: dict) -> None:
    validate_chart_spec({"query_id": "q_1", **example}, COLUMNS)
