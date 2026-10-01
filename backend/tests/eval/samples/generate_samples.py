"""Generator deterministik dataset contoh multi-dataset untuk ADK eval (Req 30.4).

Menghasilkan (di folder skrip ini)::

    customers.csv        200 pelanggan (customer_id, region, city, segment, ...)
    transactions.csv     2.000 transaksi Jan–Des 2024 (relasi customer_id & product_id)
    products.xlsx        sheet "Products": 30 produk (product_id, product_name, category, price)
    expected_facts.json  angka ground-truth + SQL rujukan, dihitung ulang dengan Polars
                         dari file yang baru ditulis (dipakai eval task 22.2)

Jalankan ulang dengan::

    backend\\.venv\\Scripts\\python.exe backend\\tests\\eval\\samples\\generate_samples.py

Semua keacakan berasal dari ``random.Random(SEED)`` sehingga output identik di
setiap run (termasuk byte ``products.xlsx``: properti dokumen dipatok).

Pola yang sengaja ditanam agar insight dapat diperiksa:

- Region ``Jawa`` mendominasi pendapatan (± separuh pelanggan).
- Tren bulanan naik perlahan; **November 2024 (Harbolnas 11.11)** adalah anomali
  lonjakan (jumlah transaksi ± 2× bulan lain).
- Masalah kualitas data: ``customers.email`` kosong (null) untuk 7 pelanggan.

Hanya bergantung pada ``polars`` dan ``xlsxwriter`` (tanpa impor ``studio``).
"""

from __future__ import annotations

import json
import random
import statistics
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path
from typing import Any

import polars as pl
import xlsxwriter

SEED = 20240511
OUT_DIR = Path(__file__).resolve().parent
YEAR = 2024

N_CUSTOMERS = 200
N_NULL_EMAILS = 7
ANOMALY_MONTH = f"{YEAR}-11"

#: Jumlah transaksi per bulan (Jan..Des), total 2.000; November = lonjakan Harbolnas.
MONTHLY_TX_COUNTS = [135, 138, 142, 145, 148, 150, 153, 156, 159, 162, 340, 172]

#: region → [(kota, bobot)]; bobot relatif menentukan sebaran pelanggan.
REGIONS: dict[str, list[tuple[str, int]]] = {
    "Jawa": [
        ("Jakarta", 14), ("Bandung", 9), ("Surabaya", 10), ("Semarang", 6),
        ("Yogyakarta", 5), ("Malang", 4),
    ],
    "Sumatera": [("Medan", 7), ("Palembang", 5), ("Padang", 3), ("Pekanbaru", 3)],
    "Kalimantan": [("Balikpapan", 4), ("Pontianak", 3), ("Banjarmasin", 3)],
    "Sulawesi": [("Makassar", 6), ("Manado", 3)],
    "Bali & Nusa Tenggara": [("Denpasar", 5), ("Mataram", 3)],
}

SEGMENTS = [("Retail", 60), ("UMKM", 30), ("Korporat", 10)]
CHANNELS = [("Marketplace", 45), ("Website", 30), ("Toko Fisik", 25)]

FIRST_NAMES = [
    "Budi", "Siti", "Agus", "Dewi", "Andi", "Rina", "Joko", "Sri", "Hendra", "Putri",
    "Rizky", "Ayu", "Fajar", "Indah", "Bayu", "Lestari", "Dimas", "Wulan", "Eko", "Maya",
]
LAST_NAMES = [
    "Santoso", "Wijaya", "Pratama", "Saputra", "Hidayat", "Kusuma", "Nugroho", "Setiawan",
    "Lubis", "Siregar", "Wibowo", "Rahmawati", "Halim", "Gunawan", "Syahputra", "Utami",
]

#: kategori → (bobot popularitas, rentang harga IDR, rentang qty, nama produk)
CATEGORIES: dict[str, tuple[int, tuple[int, int], tuple[int, int], list[str]]] = {
    "Elektronik": (18, (350_000, 4_500_000), (1, 2), [
        "Earbuds Nirkabel", "Power Bank 20.000 mAh", "Smartwatch", "Speaker Bluetooth",
        "Rice Cooker Digital", "Kipas Angin Berdiri",
    ]),
    "Fashion": (24, (75_000, 450_000), (1, 3), [
        "Kemeja Batik", "Kaos Katun", "Celana Chino", "Hijab Voal", "Sepatu Sneakers",
        "Tas Selempang",
    ]),
    "Makanan & Minuman": (26, (15_000, 120_000), (2, 6), [
        "Kopi Arabika Gayo 250g", "Teh Melati", "Sambal Bawang", "Keripik Singkong",
        "Madu Hutan 500ml", "Rendang Kemasan",
    ]),
    "Kesehatan & Kecantikan": (17, (25_000, 250_000), (1, 4), [
        "Sabun Herbal", "Serum Wajah", "Minyak Kayu Putih", "Sunscreen SPF 50",
        "Masker Wajah", "Vitamin C 500mg",
    ]),
    "Rumah Tangga": (15, (40_000, 650_000), (1, 3), [
        "Set Panci", "Sapu & Pengki", "Rak Piring", "Dispenser Sabun", "Sprei Katun",
        "Lampu LED 12W",
    ]),
}


# ---------------------------------------------------------------------------
# Pembangkitan data
# ---------------------------------------------------------------------------


def _weighted(rng: random.Random, pairs: list[tuple[str, int]]) -> str:
    return rng.choices([p[0] for p in pairs], weights=[p[1] for p in pairs], k=1)[0]


def _round_to(value: float, step: int) -> int:
    return int(round(value / step)) * step


def build_customers(rng: random.Random) -> list[dict[str, Any]]:
    city_region = [(c, w, r) for r, cities in REGIONS.items() for c, w in cities]
    null_email_idx = set(rng.sample(range(N_CUSTOMERS), N_NULL_EMAILS))
    rows: list[dict[str, Any]] = []
    for i in range(N_CUSTOMERS):
        cid = f"CUST-{i + 1:04d}"
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        city, _, region = rng.choices(city_region, weights=[w for _, w, _ in city_region], k=1)[0]
        signup = date(YEAR - 1, 1, 1).toordinal() + rng.randrange(365)
        rows.append(
            {
                "customer_id": cid,
                "customer_name": f"{first} {last}",
                "email": None
                if i in null_email_idx
                else f"{first}.{last}.{i + 1:04d}@example.com".lower(),
                "city": city,
                "region": region,
                "segment": _weighted(rng, SEGMENTS),
                "signup_date": date.fromordinal(signup),
            }
        )
    return rows


def build_products(rng: random.Random) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    n = 0
    for category, (_, (lo, hi), _, names) in CATEGORIES.items():
        for name in names:
            n += 1
            rows.append(
                {
                    "product_id": f"PRD-{n:03d}",
                    "product_name": name,
                    "category": category,
                    "price": _round_to(rng.uniform(lo, hi), 1_000),
                }
            )
    return rows


def build_transactions(
    rng: random.Random, customers: list[dict[str, Any]], products: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # Kecenderungan belanja per pelanggan (lognormal) → ada pelanggan "loyal" yang menonjol.
    cust_ids = [c["customer_id"] for c in customers]
    cust_w = [rng.lognormvariate(0.0, 0.7) for _ in customers]
    prod_w = [CATEGORIES[p["category"]][0] for p in products]
    drafts: list[tuple[date, dict[str, Any]]] = []
    for month, count in enumerate(MONTHLY_TX_COUNTS, start=1):
        days = monthrange(YEAR, month)[1]
        for _ in range(count):
            product = rng.choices(products, weights=prod_w, k=1)[0]
            q_lo, q_hi = CATEGORIES[product["category"]][2]
            qty = rng.randint(q_lo, q_hi)
            drafts.append(
                (
                    date(YEAR, month, rng.randint(1, days)),
                    {
                        "customer_id": rng.choices(cust_ids, weights=cust_w, k=1)[0],
                        "product_id": product["product_id"],
                        "quantity": qty,
                        "unit_price": product["price"],
                        "amount": qty * product["price"],
                        "channel": _weighted(rng, CHANNELS),
                    },
                )
            )
    drafts.sort(key=lambda d: d[0])  # sort stabil: urutan pembangkitan dipertahankan per hari
    return [
        {"transaction_id": f"TRX-{i + 1:06d}", "transaction_date": d, **row}
        for i, (d, row) in enumerate(drafts)
    ]


def build_finance_monthly(rng: random.Random) -> list[dict[str, Any]]:
    """Laba-rugi bulanan 2023–2024 (IDR) + baris ``cancelled`` yang harus diabaikan.

    Dipakai eval Blueprint (Req 30.4): metrik ``revenue``/``net_profit`` dan
    Workspace_Instruction "abaikan status cancelled".
    """
    rows: list[dict[str, Any]] = []
    for i in range(24):
        revenue = _round_to(1_200_000_000 * (1.015**i) * rng.uniform(0.94, 1.06), 1_000)
        rows.append(
            {
                "month": date(YEAR - 1 + i // 12, i % 12 + 1, 1),
                "status": "posted",
                "revenue": revenue,
                "cogs": _round_to(revenue * rng.uniform(0.55, 0.60), 1_000),
                "opex": _round_to(revenue * rng.uniform(0.20, 0.24), 1_000),
                "interest_tax": _round_to(revenue * rng.uniform(0.05, 0.07), 1_000),
            }
        )
    # Jurnal batal bernilai besar: hasil salah bila instruksi diabaikan.
    for i in (5, 17, 23):
        rows.append({**rows[i], "status": "cancelled", "revenue": 900_000_000, "cogs": 0, "opex": 0,
                     "interest_tax": 0})
    return sorted(rows, key=lambda r: (r["month"], r["status"]))


def finance_facts(df: pl.DataFrame) -> dict[str, Any]:
    posted = df.filter(pl.col("status") != "cancelled")
    latest = posted.sort("month").row(-1, named=True)
    return {
        "file": "finance_monthly.csv",
        "rows": df.height,
        "cancelled_rows": df.height - posted.height,
        "latest_month": latest["month"].isoformat(),
        "latest_month_revenue": latest["revenue"],
        "revenue_posted": posted["revenue"].sum(),
        "revenue_posted_2024": posted.filter(pl.col("month").dt.year() == YEAR)["revenue"].sum(),
        "revenue_all_2024_wrong": df.filter(pl.col("month").dt.year() == YEAR)["revenue"].sum(),
        "net_profit_posted": (
            posted["revenue"] - posted["cogs"] - posted["opex"] - posted["interest_tax"]
        ).sum(),
    }


# ---------------------------------------------------------------------------
# Penulisan file
# ---------------------------------------------------------------------------


def write_products_xlsx(path: Path, products: list[dict[str, Any]]) -> None:
    wb = xlsxwriter.Workbook(str(path))
    # Patok metadata agar byte file stabil antar-run.
    wb.set_properties({"created": datetime(YEAR, 1, 1), "author": "Dashboard Studio samples"})
    ws = wb.add_worksheet("Products")
    header = ["product_id", "product_name", "category", "price"]
    bold = wb.add_format({"bold": True})
    ws.write_row(0, 0, header, bold)
    for r, p in enumerate(products, start=1):
        ws.write_row(r, 0, [p[h] for h in header])
    ws.set_column(0, 0, 12)
    ws.set_column(1, 2, 26)
    ws.set_column(3, 3, 12)
    wb.close()


# ---------------------------------------------------------------------------
# Ground truth (dihitung dari file yang sudah ditulis, lewat SQL Polars)
# ---------------------------------------------------------------------------

SQL: dict[str, str] = {
    "total_revenue": (
        "SELECT SUM(amount) AS revenue, COUNT(*) AS transactions, SUM(quantity) AS units "
        "FROM transactions"
    ),
    "revenue_by_month": (
        "SELECT STRFTIME(transaction_date, '%Y-%m') AS month, SUM(amount) AS revenue, "
        "COUNT(*) AS transactions FROM transactions "
        "GROUP BY STRFTIME(transaction_date, '%Y-%m') ORDER BY month"
    ),
    "revenue_by_region": (
        "SELECT c.region, SUM(t.amount) AS revenue, COUNT(*) AS transactions "
        "FROM transactions t JOIN customers c ON t.customer_id = c.customer_id "
        "GROUP BY c.region ORDER BY revenue DESC"
    ),
    "revenue_by_category": (
        "SELECT p.category, SUM(t.amount) AS revenue, SUM(t.quantity) AS units "
        "FROM transactions t JOIN products p ON t.product_id = p.product_id "
        "GROUP BY p.category ORDER BY revenue DESC"
    ),
    "revenue_by_channel": (
        "SELECT channel, SUM(amount) AS revenue, COUNT(*) AS transactions "
        "FROM transactions GROUP BY channel ORDER BY revenue DESC"
    ),
    "top_customers": (
        "SELECT c.customer_id, c.customer_name, c.region, SUM(t.amount) AS revenue "
        "FROM transactions t JOIN customers c ON t.customer_id = c.customer_id "
        "GROUP BY c.customer_id, c.customer_name, c.region "
        "ORDER BY revenue DESC, customer_id LIMIT 5"
    ),
    "top_products": (
        "SELECT p.product_id, p.product_name, p.category, SUM(t.amount) AS revenue "
        "FROM transactions t JOIN products p ON t.product_id = p.product_id "
        "GROUP BY p.product_id, p.product_name, p.category "
        "ORDER BY revenue DESC, product_id LIMIT 5"
    ),
    "customers_by_region": (
        "SELECT region, COUNT(*) AS customers FROM customers "
        "GROUP BY region ORDER BY customers DESC, region"
    ),
    "null_emails": (
        "SELECT customer_id FROM customers WHERE email IS NULL ORDER BY customer_id"
    ),
}


def _records(df: pl.DataFrame) -> list[dict[str, Any]]:
    return df.to_dicts()


def _pct(part: int, whole: int) -> float:
    return round(part * 100.0 / whole, 2)


def compute_facts(
    customers: pl.DataFrame, transactions: pl.DataFrame, products: pl.DataFrame
) -> dict[str, Any]:
    ctx = pl.SQLContext(
        frames={"customers": customers, "transactions": transactions, "products": products},
        eager=True,
    )
    q = {name: ctx.execute(sql) for name, sql in SQL.items()}

    total = q["total_revenue"].row(0, named=True)
    months = _records(q["revenue_by_month"])
    regions = _records(q["revenue_by_region"])
    categories = _records(q["revenue_by_category"])
    channels = _records(q["revenue_by_channel"])
    null_ids = q["null_emails"]["customer_id"].to_list()

    anomaly = next(m for m in months if m["month"] == ANOMALY_MONTH)
    others = [m["revenue"] for m in months if m["month"] != ANOMALY_MONTH]
    other_mean = statistics.fmean(others)

    # Invarian pola yang ditanam; gagal keras bila perubahan generator merusaknya.
    assert max(months, key=lambda m: m["revenue"])["month"] == ANOMALY_MONTH
    assert anomaly["revenue"] > 1.5 * max(others), "anomali November kurang menonjol"
    assert regions[0]["region"] == "Jawa" and regions[0]["revenue"] > 2 * regions[1]["revenue"]
    for key in ("top_customers", "top_products"):
        top = _records(q[key])
        assert top[0]["revenue"] > top[1]["revenue"], f"{key}: peringkat 1 seri"
    assert categories[0]["revenue"] > categories[1]["revenue"]

    def overlap(a: pl.Series, b: pl.Series) -> dict[str, Any]:
        da, db = set(a.drop_nulls().to_list()), set(b.drop_nulls().to_list())
        return {
            "distinct_from": len(da),
            "distinct_to": len(db),
            "intersection": len(da & db),
            "overlap_pct": _pct(len(da & db), min(len(da), len(db))),
        }

    return {
        "_note": (
            "Dibangkitkan oleh generate_samples.py (seed tetap). Uang dalam IDR (integer). "
            "Nama tabel mengikuti nama file saat diunggah: customers, transactions, products "
            "(workbook satu sheet → nama tabel dari nama file)."
        ),
        "seed": SEED,
        "tables": {
            "customers": {
                "file": "customers.csv",
                "rows": customers.height,
                "columns": customers.columns,
            },
            "transactions": {
                "file": "transactions.csv",
                "rows": transactions.height,
                "columns": transactions.columns,
                "date_range": [
                    transactions["transaction_date"].min().isoformat(),
                    transactions["transaction_date"].max().isoformat(),
                ],
            },
            "products": {
                "file": "products.xlsx",
                "sheet": "Products",
                "rows": products.height,
                "columns": products.columns,
            },
        },
        "relations": [
            {
                "from": "customers.customer_id",
                "to": "transactions.customer_id",
                "cardinality": "one_to_many",
                **overlap(customers["customer_id"], transactions["customer_id"]),
            },
            {
                "from": "products.product_id",
                "to": "transactions.product_id",
                "cardinality": "one_to_many",
                **overlap(products["product_id"], transactions["product_id"]),
            },
        ],
        "data_quality": {
            "customers.email": {
                "issue": "null",
                "null_count": len(null_ids),
                "null_pct": _pct(len(null_ids), customers.height),
                "customer_ids": null_ids,
            },
            "duplicate_rows": {
                name: df.height - df.unique().height
                for name, df in (
                    ("customers", customers), ("transactions", transactions), ("products", products)
                )
            },
        },
        "totals": {
            "revenue": total["revenue"],
            "transactions": total["transactions"],
            "units": total["units"],
            "avg_order_value": round(total["revenue"] / total["transactions"], 2),
            "active_customers": transactions["customer_id"].n_unique(),
        },
        "top_region": {
            **regions[0],
            "share_pct": _pct(regions[0]["revenue"], total["revenue"]),
        },
        "revenue_by_region": regions,
        "customers_by_region": _records(q["customers_by_region"]),
        "monthly_trend": {
            "months": months,
            "anomaly": {
                "month": ANOMALY_MONTH,
                "kind": "spike",
                "reason": "Harbolnas 11.11 (jumlah transaksi ± 2x bulan lain)",
                "revenue": anomaly["revenue"],
                "transactions": anomaly["transactions"],
                "ratio_vs_mean_other_months": round(anomaly["revenue"] / other_mean, 2),
            },
            "lowest_month": min(months, key=lambda m: m["revenue"])["month"],
        },
        "top_category": {
            **categories[0],
            "share_pct": _pct(categories[0]["revenue"], total["revenue"]),
        },
        "revenue_by_category": categories,
        "revenue_by_channel": channels,
        "top_customers": _records(q["top_customers"]),
        "top_products": _records(q["top_products"]),
        "sql": SQL,
    }


def main() -> None:
    rng = random.Random(SEED)
    customers = build_customers(rng)
    products = build_products(rng)
    transactions = build_transactions(rng, customers, products)

    cust_schema = {
        "customer_id": pl.String, "customer_name": pl.String, "email": pl.String,
        "city": pl.String, "region": pl.String, "segment": pl.String, "signup_date": pl.Date,
    }
    tx_schema = {
        "transaction_id": pl.String, "transaction_date": pl.Date, "customer_id": pl.String,
        "product_id": pl.String, "quantity": pl.Int64, "unit_price": pl.Int64,
        "amount": pl.Int64, "channel": pl.String,
    }
    pl.DataFrame(customers, schema=cust_schema).write_csv(OUT_DIR / "customers.csv")
    pl.DataFrame(transactions, schema=tx_schema).write_csv(OUT_DIR / "transactions.csv")
    write_products_xlsx(OUT_DIR / "products.xlsx", products)
    # RNG terpisah: menambah dataset ini tidak mengubah byte file lain.
    fin_schema = {
        "month": pl.Date, "status": pl.String, "revenue": pl.Int64, "cogs": pl.Int64,
        "opex": pl.Int64, "interest_tax": pl.Int64,
    }
    pl.DataFrame(build_finance_monthly(random.Random(SEED + 1)), schema=fin_schema).write_csv(
        OUT_DIR / "finance_monthly.csv"
    )

    # Hitung fakta dari file hasil (bukan dari list in-memory) agar selaras dengan ingest.
    facts = compute_facts(
        pl.read_csv(OUT_DIR / "customers.csv", try_parse_dates=True),
        pl.read_csv(OUT_DIR / "transactions.csv", try_parse_dates=True),
        pl.read_excel(OUT_DIR / "products.xlsx", sheet_name="Products"),
    )
    facts["finance_monthly"] = finance_facts(
        pl.read_csv(OUT_DIR / "finance_monthly.csv", try_parse_dates=True)
    )
    (OUT_DIR / "expected_facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    print(
        f"OK: {facts['tables']['customers']['rows']} customers, "
        f"{facts['tables']['transactions']['rows']} transactions, "
        f"{facts['tables']['products']['rows']} products; "
        f"revenue {facts['totals']['revenue']:,} IDR; top region {facts['top_region']['region']}"
    )


if __name__ == "__main__":
    main()
