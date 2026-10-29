"""Bandingkan laporan eval orkestrasi terbaru dengan baseline.

    python -m tests.eval.compare_runs   (dari folder backend)

Baseline = results/baseline-orchestration.json (salin manual dari orchestration-latest.json
SEBELUM optimasi; skrip ini tidak pernah menimpanya).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
KEYS = ["avg_seconds", "avg_transfer_count", "avg_classify_turn_count", "avg_tool_calls", "avg_agent_switches",
        "routing", "trajectory", "approval_gate", "grounding"]


def table(base: dict, new: dict) -> str:
    rows = [f"{'metrik':28}{'baseline':>12}{'terbaru':>12}{'selisih':>12}"]
    for k in KEYS:
        b, n = base["summary"].get(k), new["summary"].get(k)
        if b is None or n is None:
            continue
        rows.append(f"{k:28}{b:>12}{n:>12}{round(n - b, 3):>12}")
    rows.append(f"{'lulus':28}{base['passed']}/{base['total']:>10}{new['passed']}/{new['total']:>10}")
    return "\n".join(rows)


def _selfcheck() -> None:
    s = {"summary": {"avg_seconds": 10.0, "routing": 1.0}, "passed": 1, "total": 1}
    t = {"summary": {"avg_seconds": 7.5, "routing": 1.0}, "passed": 1, "total": 1}
    assert "-2.5" in table(s, t) and "avg_tool_calls" not in table(s, t)


if __name__ == "__main__":
    _selfcheck()
    base_p, new_p = RESULTS / "baseline-orchestration.json", RESULTS / "orchestration-latest.json"
    if not base_p.exists() or not new_p.exists():
        sys.exit(f"Butuh {base_p.name} dan {new_p.name} di {RESULTS}")
    print(table(json.loads(base_p.read_text(encoding="utf-8")), json.loads(new_p.read_text(encoding="utf-8"))))
