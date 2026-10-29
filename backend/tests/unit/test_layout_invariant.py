"""Layout tersimpan tidak boleh tumpang tindih, apa pun yang diminta agent/pengguna."""

from itertools import combinations

import pytest

from studio.api.errors import StudioError

from studio.core.layout_templates import rects_overlap
from studio.core.models import AddKpiCommand, TidyLayoutCommand, DashboardContent, KpiSpec, LayoutRect, SetLayoutCommand
from studio.core.patches import apply_ops, invert_ops, resolve_command

_spec = KpiSpec(query_id="q", value_column="v")


def _no_overlap(content: DashboardContent) -> bool:
    return not any(rects_overlap(a, b) for a, b in combinations(content.layout.values(), 2))


def _run(content: DashboardContent, command) -> DashboardContent:
    return apply_ops(content, resolve_command(content, command))


def test_kpi_tanpa_layout_berjajar_di_baris_atas():
    c = DashboardContent(title="t")
    for i in range(4):
        c = _run(c, AddKpiCommand(title=f"k{i}", spec=_spec))
    assert _no_overlap(c)
    assert sorted((r.x, r.y) for r in c.layout.values()) == [(0, 0), (3, 0), (6, 0), (9, 0)]


def test_add_bertabrakan_mendorong_item_lain():
    c = _run(DashboardContent(title="t"), AddKpiCommand(title="a", spec=_spec, layout=LayoutRect(x=0, y=0, w=3, h=2)))
    c = _run(c, AddKpiCommand(title="b", spec=_spec, layout=LayoutRect(x=0, y=0, w=6, h=4)))
    assert _no_overlap(c)


def test_tukar_posisi_item_beda_ukuran_tanpa_overlap():
    c = _run(DashboardContent(title="t"), AddKpiCommand(title="a", spec=_spec, layout=LayoutRect(x=0, y=0, w=4, h=7)))
    c = _run(c, AddKpiCommand(title="b", spec=_spec, layout=LayoutRect(x=6, y=0, w=6, h=10)))
    a, b = list(c.layout)
    ra, rb = c.layout[a], c.layout[b]
    c = _run(c, SetLayoutCommand(changes={a: rb.model_copy(update={"w": ra.w, "h": ra.h}), b: ra.model_copy(update={"w": rb.w, "h": rb.h, "x": 3})}))
    assert _no_overlap(c)


def test_set_layout_bertabrakan_mendorong_item_lain():
    c = DashboardContent(title="t")
    for i in range(3):
        c = _run(c, AddKpiCommand(title=f"k{i}", spec=_spec))
    first, second = list(c.layout)[:2]
    c = _run(c, SetLayoutCommand(changes={first: LayoutRect(x=3, y=0, w=6, h=6)}))
    assert _no_overlap(c)
    assert c.layout[first] == LayoutRect(x=3, y=0, w=6, h=6)  # item yang dipindah tidak bergeser


def test_tidy_layout_menutup_celah_tanpa_overlap():
    from studio.core.patches import tidy_layout

    c = DashboardContent(title="t")
    for i, rect in enumerate([LayoutRect(x=0, y=0, w=3, h=2), LayoutRect(x=0, y=9, w=6, h=6), LayoutRect(x=6, y=20, w=6, h=6)]):
        c = _run(c, AddKpiCommand(title=f"k{i}", spec=_spec, layout=rect))
    c = c.model_copy(update={"layout": tidy_layout(c)})
    assert _no_overlap(c)
    assert max(r.y + r.h for r in c.layout.values()) == 12  # A(3x2)+B(6x6) sebaris, C(6x6) di bawah B: tanpa celah

def test_command_tidy_layout_rapi_dan_undo_mengembalikan_awal():
    c = DashboardContent(title="t")
    for i, rect in enumerate([LayoutRect(x=0, y=0, w=3, h=2), LayoutRect(x=0, y=9, w=6, h=6), LayoutRect(x=6, y=20, w=6, h=6)]):
        c = _run(c, AddKpiCommand(title=f"k{i}", spec=_spec, layout=rect))
    ops = resolve_command(c, TidyLayoutCommand())
    tidy = apply_ops(c, ops)
    assert _no_overlap(tidy)
    assert max(r.y + r.h for r in tidy.layout.values()) == 12
    assert apply_ops(tidy, invert_ops(ops)).layout == c.layout
    with pytest.raises(StudioError) as e:  # sudah rapi: tidak ada perubahan
        resolve_command(tidy, TidyLayoutCommand())
    assert e.value.code == "NO_CHANGES"

