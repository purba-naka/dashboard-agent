"""Data Card + pemilih tabel per pertanyaan (``core/data_card``)."""

from __future__ import annotations

from studio.core.data_card import TableDoc, catalog_line, select_tables, table_card, value_matches
from studio.core.models import ColumnProfile


def col(name: str, type_: str, role: str, distinct: int, **kw: object) -> ColumnProfile:
    return ColumnProfile(
        name=name, type=type_, role=role, null_count=kw.pop("null_count", 0),  # type: ignore[arg-type]
        null_pct=kw.pop("null_pct", 0.0), distinct_count=distinct, **kw,  # type: ignore[arg-type]
    )


SALES = TableDoc(
    "sales",
    100,
    (
        col("order_id", "integer", "identifier", 100, min=1, max=100),
        col("order_date", "date", "time", 31, min="2024-01-01", max="2024-01-31"),
        col("region", "string", "dimension", 3, top_values=[("Jakarta", 50), ("Bandung", 30), ("Surabaya", 20)]),
        col("amount", "float", "measure", 90, min=1.0, max=500.0, mean=120.5),
    ),
)
SECRET = TableDoc(
    "customers",
    10,
    (col("customer_id", "integer", "identifier", 10), col("city", "string", "dimension", 2, top_values=[("Jakarta", 6)], min="A", max="Z")),
    no_samples=True,
)
OTHERS = [TableDoc(f"t{i}", 5, (col("x", "integer", "measure", 5),)) for i in range(4)]


def test_card_memuat_grain_waktu_nilai_dan_statistik() -> None:
    card = table_card(SALES)
    assert card.splitlines()[0] == "sales: 100 baris; 1 baris per order_id; waktu order_date 2024-01-01..2024-01-31 (harian)"
    assert "- region string dimension | 3 nilai: Jakarta, Bandung, Surabaya" in card
    assert "- amount float measure | 90 nilai; 1..500; rata 120.5" in card
    assert catalog_line(SALES).endswith("kolom: order_id, order_date, region, amount")


def test_no_samples_menyembunyikan_nilai_string() -> None:
    card = table_card(SECRET)
    assert "Jakarta" not in card and "A..Z" not in card
    assert value_matches("omzet di Jakarta", [SECRET]) == []


def test_pemilih_tabel_pakai_nama_nilai_dan_relasi() -> None:
    docs = [*OTHERS, SALES, SECRET]
    assert value_matches("omzet di jakarta", docs) == ["sales.region = 'Jakarta'"]
    # Nilai "Bandung" mengarah ke sales; relasi menarik customers sebagai tetangga.
    picked = select_tables("total di Bandung", docs, [("customers", "sales")])
    assert picked[:2] == ["sales", "customers"]
    assert select_tables("pertanyaan tanpa kata yang cocok", docs) == []
    assert select_tables("apa saja", [SALES]) == ["sales"]  # workspace kecil → semua
