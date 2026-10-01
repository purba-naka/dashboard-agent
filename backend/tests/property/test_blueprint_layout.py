"""Feature: dashboard-studio-agent, Property 41: Layout_Template dan validasi Blueprint.

``place_slots`` menghasilkan layout di dalam grid, tanpa tumpang tindih (antar
slot maupun dengan item yang ada), mempertahankan layout tetap, menaruh slot
``kpi_row`` tanpa layout (tinggi 2) di atas slot tanpa layout lainnya, dan
deterministik; ``validate_blueprint`` atas hasilnya bersih dari issue layout;
``select_slots`` mempertahankan layout slot terpilih.

**Validates: Requirements 37.2, 37.4, 37.6, 37.13**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from studio.core.blueprint import finalize_blueprint, select_slots, validate_blueprint
from studio.core.layout_templates import place_slots, rects_overlap
from studio.core.models import GRID_COLUMNS, BlueprintSlot, DashboardBlueprint, LayoutRect

_SECTIONS = ["kpi_row", "trend", "breakdown", "composition", "distribution", "detail", "other"]
_VISUAL_FOR = {"kpi_row": "kpi", "trend": "line", "detail": "insight"}


@st.composite
def _rect(draw: st.DrawFn, y_min: int, y_max: int) -> LayoutRect:
    x = draw(st.integers(0, GRID_COLUMNS - 1))
    return LayoutRect(x=x, y=draw(st.integers(y_min, y_max)), w=draw(st.integers(1, GRID_COLUMNS - x)), h=draw(st.integers(1, 6)))


def _disjoint(rects: list[LayoutRect]) -> bool:
    return all(not rects_overlap(a, b) for i, a in enumerate(rects) for b in rects[i + 1 :])


@st.composite
def _scenario(draw: st.DrawFn):
    existing = draw(st.lists(_rect(0, 20), max_size=5).filter(_disjoint))
    n = draw(st.integers(1, 12))
    slots: list[BlueprintSlot] = []
    fixed: list[LayoutRect] = []
    for i in range(n):
        section = draw(st.sampled_from(_SECTIONS))
        layout = None
        if draw(st.integers(0, 4)) == 0:
            candidate = draw(_rect(0, 40))
            if all(not rects_overlap(candidate, o) for o in [*existing, *fixed]):
                layout = candidate
                fixed.append(candidate)
        slots.append(
            BlueprintSlot(
                slot_id=f"s{i}",
                section=section,  # type: ignore[arg-type]
                purpose="p",
                visual=_VISUAL_FOR.get(section, "bar"),  # type: ignore[arg-type]
                metrics=["revenue"],
                layout=layout,
            )
        )
    return existing, slots


# Feature: dashboard-studio-agent, Property 41: Layout_Template dan validasi Blueprint
@settings(max_examples=100)
@given(scenario=_scenario(), pick=st.data())
def test_place_slots_and_validate(scenario, pick) -> None:
    existing, slots = scenario
    layouts = place_slots(slots, existing)
    assert place_slots(slots, existing) == layouts
    assert list(layouts) == [s.slot_id for s in slots]

    rects = list(layouts.values())
    for r in rects:
        assert 0 <= r.x and r.x + r.w <= GRID_COLUMNS and r.w >= 1 and r.h >= 1
    assert _disjoint(rects)
    assert all(not rects_overlap(r, e) for r in rects for e in existing)
    for s in slots:
        if s.layout is not None:
            assert layouts[s.slot_id] == s.layout

    auto = [s for s in slots if s.layout is None]
    kpis = [layouts[s.slot_id] for s in auto if s.section == "kpi_row"]
    others = [layouts[s.slot_id] for s in auto if s.section != "kpi_row"]
    for k in kpis:
        assert k.h == 2
        for o in others:
            assert k.y + k.h <= o.y

    bp = DashboardBlueprint(slots=slots)
    final = finalize_blueprint(bp, existing, known_metrics={"revenue"})
    assert [s.layout for s in final.slots] == rects
    assert not [i for i in validate_blueprint(final, existing, {"revenue"}) if i.rule.startswith("LAYOUT")]

    chosen = pick.draw(st.lists(st.sampled_from([s.slot_id for s in slots]), min_size=1, unique=True))
    sub = select_slots(final, chosen)
    assert [s.slot_id for s in sub.slots] == [s.slot_id for s in final.slots if s.slot_id in set(chosen)]
    for s in sub.slots:
        assert s.layout == layouts[s.slot_id]


def test_validate_reports_rules() -> None:
    slots = [
        BlueprintSlot(slot_id="a", section="kpi_row", purpose="p", visual="pie", metrics=["nope"]),
        BlueprintSlot(slot_id="a", section="trend", purpose="p", visual="line", metrics=["revenue"], dimension="ghost",
                      layout=LayoutRect(x=0, y=0, w=4, h=2)),
    ]
    rules = {i.rule for i in validate_blueprint(DashboardBlueprint(slots=slots), [LayoutRect(x=0, y=0, w=2, h=2)], {"revenue"}, {"month"})}
    assert rules == {"DUPLICATE_SLOT_ID", "UNKNOWN_METRIC", "VISUAL_SECTION_MISMATCH", "UNKNOWN_DIMENSION", "LAYOUT_OVERLAP"}
    many = DashboardBlueprint(slots=[BlueprintSlot(slot_id=f"s{i}", section="other", purpose="p", visual="bar", metrics=["revenue"]) for i in range(17)])
    assert any(i.rule == "TOO_MANY_SLOTS" for i in validate_blueprint(many, (), {"revenue"}))
