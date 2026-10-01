"""Unit test jalur hemat memori ``compute_column_profiles`` (task 23.3).

Jalur Dataset besar (``_profile_row_heavy``) dipaksa lewat monkeypatch
``PROFILE_HEAVY_MIN_ROWS``; hasilnya dibandingkan persis dengan jalur dataset
kecil (satu query eksak) atas data uji yang sama. Kompromi jalur berat hanya
berlaku untuk data yang lebih besar dari sampel/top-k, sehingga pada data uji
kecil kedua jalur harus identik (distinct, top_values, duplikat via hash 64-bit
tanpa tabrakan, statistik murah).
"""

from __future__ import annotations

import pytest

import polars as pl

from studio.core.models import ColumnInfo
from studio.data.profiler import compute_column_profiles


@pytest.mark.parametrize(
    "data",
    [
        # Kolom unik penuh (jalur bottom_k) + kolom kategorikal berulang.
        {
            "id": [f"C{i:05d}" for i in range(50)],
            "region": ["Jawa"] * 30 + ["Bali"] * 20,
            "amount": list(range(50)),
        },
        # Null, duplikat baris, dan string campuran angka/teks.
        {
            "kode": ["A", "1", None, "1", "A", None],
            "nilai": [1.5, 2.5, 1.5, None, 2.5, 9.0],
        },
        # Satu kolom, semua nilai sama.
        {"satu": ["x"] * 7},
        # Tipe tanggal.
        {
            "tanggal": ["2024-01-01", "2024-01-02", None, "2024-01-01"],
            "qty": [1, 2, 3, 4],
        },
    ],
)
def test_jalur_berat_identik_dengan_jalur_kecil(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, list[object]]
) -> None:
    schema = [
        ColumnInfo(name=name, type=type_)  # type: ignore[arg-type]
        for name, type_ in (
            ("id", "string"),
            ("region", "string"),
            ("amount", "integer"),
            ("kode", "string"),
            ("nilai", "float"),
            ("satu", "string"),
            ("tanggal", "date"),
            ("qty", "integer"),
        )
        if name in data
    ]
    casted = data.copy()
    if "tanggal" in casted:
        casted["tanggal"] = [
            None if v is None else __import__("datetime").date.fromisoformat(v)
            for v in casted["tanggal"]
        ]
    df = pl.DataFrame(casted).lazy()

    kecil = compute_column_profiles(df, schema)

    import studio.data.profiler as mod

    monkeypatch.setattr(mod, "PROFILE_HEAVY_MIN_ROWS", 0)
    berat = compute_column_profiles(df, schema)

    assert berat.row_count == kecil.row_count
    assert [p.model_dump() for p in berat.columns] == [p.model_dump() for p in kecil.columns]
    assert berat.quality == kecil.quality


def test_jalur_berat_duplikat_via_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """``duplicate_rows`` jalur berat (hash 64-bit) benar untuk data uji kecil."""
    schema = [ColumnInfo(name="a", type="integer"), ColumnInfo(name="b", type="string")]
    df = pl.DataFrame({"a": [1, 1, 2, 2, 3], "b": ["x", "x", "y", "y", "z"]}).lazy()

    import studio.data.profiler as mod

    kecil = compute_column_profiles(df, schema)
    monkeypatch.setattr(mod, "PROFILE_HEAVY_MIN_ROWS", 0)
    berat = compute_column_profiles(df, schema)
    assert kecil.quality["duplicate_rows"] == 2
    assert berat.quality["duplicate_rows"] == 2


def test_jalur_berat_sampel_top_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """``top_values`` kolom non-unik dihitung dari sampel awal deterministik."""
    schema = [ColumnInfo(name="kota", type="string")]
    # 6 nilai unik x 10 baris; sampel dibatasi 40 baris pertama → kota "F" dan
    # sebagian "E" tidak masuk sampel (perilaku perkiraan yang terdokumentasi).
    kota = ["A", "B", "C", "D", "E", "F"]
    df = pl.DataFrame({"kota": [kota[i % 6] for i in range(60)]}).lazy()

    import studio.data.profiler as mod

    monkeypatch.setattr(mod, "PROFILE_HEAVY_MIN_ROWS", 0)
    monkeypatch.setattr(mod, "PROFILE_TOP_VALUES_SAMPLE", 40)
    result = compute_column_profiles(df, schema)

    (prof,) = result.columns
    assert prof.distinct_count == 6
    # 40 baris pertama siklus 6 nilai: A-D 7x, E-F 6x → 5 teratas A-E.
    assert [v for v, _ in prof.top_values] == ["A", "B", "C", "D", "E"]
    assert [c for _, c in prof.top_values] == [7, 7, 7, 7, 6]
