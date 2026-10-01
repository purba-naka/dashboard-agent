# Dataset contoh ADK eval (Req 30.4)

Dibangkitkan secara deterministik (seed tetap, output byte-identik) oleh `generate_samples.py`:

```powershell
backend\.venv\Scripts\python.exe backend\tests\eval\samples\generate_samples.py
```

| File | Tabel | Isi |
| --- | --- | --- |
| `customers.csv` | `customers` | 200 pelanggan: `customer_id`, `customer_name`, `email`, `city`, `region`, `segment`, `signup_date` |
| `transactions.csv` | `transactions` | 2.000 transaksi 2024-01-01..2024-12-31: `transaction_id`, `transaction_date`, `customer_id`, `product_id`, `quantity`, `unit_price`, `amount` (IDR), `channel` |
| `products.xlsx` (sheet `Products`) | `products` | 30 produk: `product_id`, `product_name`, `category`, `price` (IDR) |
| `finance_monthly.csv` | `finance_monthly` | 24 bulan 2023-2024 + 3 baris `cancelled`: `month`, `status`, `revenue`, `cogs`, `opex`, `interest_tax` (IDR). Dipakai `blueprint.evalset.json` |
| `expected_facts.json` | – | Ground truth untuk eval: total, per region/bulan/kategori/channel, top 5 pelanggan & produk, relasi, kualitas data, plus SQL rujukan |

Relasi yang harus diusulkan Profiler (keduanya `one_to_many`, overlap 100%):
`customers.customer_id ↔ transactions.customer_id` dan `products.product_id ↔ transactions.product_id`.

Pola yang sengaja ditanam:

- Region `Jawa` menyumbang ± 47% pendapatan, lebih dari 2× region berikutnya (perlu JOIN ke `customers`).
- November 2024 (Harbolnas 11.11) melonjak ± 2,1× rata-rata bulan lain.
- Masalah kualitas data: `customers.email` null untuk 7 pelanggan (3,5%).

Angka pasti ada di `expected_facts.json`. Jangan edit file data secara manual; ubah generator lalu jalankan ulang.
Nama tabel mengikuti nama file saat diunggah.
