"""Templat chart berparameter: agent memilih tipe + kolom, kode membangun ``option`` ECharts.

Menghasilkan ``option`` yang lolos ``validate_chart_spec`` (tanpa data inline, series memakai
``encode`` dengan nama kolom). Warna/font tidak ditulis: tema ECharts frontend membacanya dari token.
Murni (tanpa ADK/FastAPI). Chart di luar templat (kombinasi, dua sumbu, heatmap) tetap lewat
``add_chart`` dengan ``option`` bebas.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from studio.api.errors import StudioError

Template = Literal["bar", "line", "pie", "scatter"]
TEMPLATES: tuple[str, ...] = get_args(Template)


def build_chart_option(
    template: str,
    title: str,
    x: str,
    y: str,
) -> dict[str, Any]:
    """Bangun ``option`` dari templat.

    ``x``/``y`` = nama kolom hasil query. Untuk ``pie``: ``x`` = kategori, ``y`` = nilai.
    Satu seri per chart; chart multi-seri memakai ``add_chart`` dengan ``option`` bebas.
    """
    if template not in TEMPLATES:
        raise StudioError(
            "INVALID_ARGUMENT",
            f"template '{template}' tidak dikenal; pilih salah satu: {', '.join(TEMPLATES)}.",
            {"argument": "template", "valid": list(TEMPLATES)},
            http_status=422,
        )
    for name, value in (("title", title), ("x", x), ("y", y)):
        if not isinstance(value, str) or not value.strip():
            raise StudioError(
                "INVALID_ARGUMENT", f"{name} tidak boleh kosong.", {"argument": name}, http_status=422
            )
    head: dict[str, Any] = {"title": {"text": title.strip()}, "tooltip": {"trigger": "item" if template == "pie" else "axis"}}

    if template == "pie":
        return {
            **head,
            "legend": {},
            "series": [{"type": "pie", "encode": {"itemName": x, "value": y}}],
        }
    if template == "scatter":
        return {
            **head,
            "xAxis": {"type": "value", "name": x},
            "yAxis": {"type": "value", "name": y},
            "series": [{"type": "scatter", "encode": {"x": x, "y": y}}],
        }
    # bar / line
    return {
        **head,
        "xAxis": {"type": "time" if template == "line" and _looks_temporal(x) else "category", "name": x},
        "yAxis": {"type": "value", "name": y},
        "series": [{"type": template, "encode": {"x": x, "y": y}}],
    }


def _looks_temporal(column: str) -> bool:
    # ponytail: heuristik nama kolom; ganti dengan tipe kolom dari skema query bila salah deteksi.
    return any(k in column.lower() for k in ("tanggal", "date", "waktu", "time"))


if __name__ == "__main__":
    from studio.core.chart_spec import validate_chart_spec
    from studio.core.models import ColumnInfo

    cols = [ColumnInfo(name="bulan", type="string"), ColumnInfo(name="revenue", type="float")]
    for t in TEMPLATES:
        validate_chart_spec(
            {"query_id": "q", "chart_type": t, "option": build_chart_option(t, "T", "bulan", "revenue")}, cols
        )
    print("ok")
