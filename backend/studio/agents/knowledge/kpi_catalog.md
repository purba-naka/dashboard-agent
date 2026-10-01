---
topic: kpi_catalog
summary: Katalog KPI per domain (sales, finance, marketing, operations, hr, ecommerce) dengan rumus SQL agregat, grain, arah nilai baik, format, dan kolom yang dibutuhkan.
---

# Katalog KPI

Rumus ditulis sebagai ekspresi SQL agregat generik. Nama kolom adalah **contoh**; petakan ke kolom
nyata lewat Semantic_Model (Business_Metric `expr`, sinonim) sebelum dipakai. Rasio selalu memakai
`NULLIF(..., 0)` agar tidak terjadi pembagian nol.

Keterangan kolom tabel:

- **Arah**: `up` = makin besar makin baik, `down` = makin kecil makin baik, `neutral` = tergantung
  konteks. Dipakai sebagai `good_direction` KPI_Card.
- **Format**: `currency` (Rp, ringkas rb/jt/M/T), `percent` (rasio 0–1 ditampilkan sebagai %),
  `number`.
- **Grain**: grain waktu yang lazim untuk tren dan pembanding.

Catatan umum:

- Rasio dihitung dari agregat (`SUM(a) / SUM(b)`), **bukan** rata-rata rasio per baris.
- Untuk KPI_Card, query menghasilkan tepat satu baris, mis. kolom `value` dan `prev_value`.
- Bila data hanya punya satu kolom `amount` dengan kolom `type`/`account`, ganti `SUM(x)` dengan
  `SUM(CASE WHEN account_type = 'x' THEN amount ELSE 0 END)`.

## Sales

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `sales_revenue` | Penjualan | `SUM(revenue)` | bulan | up | currency | revenue |
| `orders` | Jumlah Order | `COUNT(DISTINCT order_id)` | hari/bulan | up | number | order_id |
| `units_sold` | Unit Terjual | `SUM(quantity)` | bulan | up | number | quantity |
| `avg_order_value` | Rata-rata Nilai Order | `SUM(revenue) / NULLIF(COUNT(DISTINCT order_id), 0)` | bulan | up | currency | revenue, order_id |
| `avg_selling_price` | Harga Jual Rata-rata | `SUM(revenue) / NULLIF(SUM(quantity), 0)` | bulan | neutral | currency | revenue, quantity |
| `sales_vs_target` | Pencapaian Target | `SUM(revenue) / NULLIF(SUM(target), 0)` | bulan/kuartal | up | percent | revenue, target |
| `active_customers` | Pelanggan Aktif | `COUNT(DISTINCT customer_id)` | bulan | up | number | customer_id |
| `win_rate` | Win Rate | `SUM(CASE WHEN stage = 'won' THEN 1 ELSE 0 END) * 1.0 / NULLIF(SUM(CASE WHEN stage IN ('won','lost') THEN 1 ELSE 0 END), 0)` | bulan/kuartal | up | percent | stage |
| `sales_gross_margin` | Margin Penjualan | `(SUM(revenue) - SUM(cogs)) / NULLIF(SUM(revenue), 0)` | bulan | up | percent | revenue, cogs |

## Finance

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `revenue` | Revenue | `SUM(revenue)` | bulan | up | currency | revenue |
| `cogs` | HPP (COGS) | `SUM(cogs)` | bulan | down | currency | cogs |
| `opex` | Biaya Operasional | `SUM(opex)` | bulan | down | currency | opex |
| `expenses` | Total Beban | `SUM(cogs) + SUM(opex) + SUM(interest_tax)` | bulan | down | currency | cogs, opex, interest_tax |
| `gross_profit` | Laba Kotor | `SUM(revenue) - SUM(cogs)` | bulan | up | currency | revenue, cogs |
| `gross_margin` | Margin Kotor | `(SUM(revenue) - SUM(cogs)) / NULLIF(SUM(revenue), 0)` | bulan | up | percent | revenue, cogs |
| `ebit` | EBIT | `SUM(revenue) - SUM(cogs) - SUM(opex)` | bulan | up | currency | revenue, cogs, opex |
| `ebit_margin` | Margin EBIT | `(SUM(revenue) - SUM(cogs) - SUM(opex)) / NULLIF(SUM(revenue), 0)` | bulan | up | percent | revenue, cogs, opex |
| `net_profit` | Laba Bersih | `SUM(revenue) - SUM(cogs) - SUM(opex) - SUM(interest_tax)` | bulan | up | currency | revenue, cogs, opex, interest_tax |
| `net_margin` | Margin Bersih | `(SUM(revenue) - SUM(cogs) - SUM(opex) - SUM(interest_tax)) / NULLIF(SUM(revenue), 0)` | bulan | up | percent | revenue, cogs, opex, interest_tax |
| `opex_ratio` | Rasio Opex | `SUM(opex) / NULLIF(SUM(revenue), 0)` | bulan | down | percent | revenue, opex |
| `budget_variance` | Selisih vs Anggaran | `SUM(actual) - SUM(budget)` | bulan | neutral | currency | actual, budget |

Catatan finance:

- Bila tabel sudah punya kolom `profit` / `net_profit`, pakai langsung, mis.
  `SUM(profit) / NULLIF(SUM(revenue), 0)` untuk margin.
- `expenses` = semua beban (COGS + opex + bunga & pajak). Bila definisi perusahaan berbeda
  (mis. hanya opex), catat sebagai asumsi di Design_Brief.
- Nilai beban disimpan positif; tanda minus hanya dipakai saat menghitung laba.

## Marketing

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `ad_spend` | Biaya Iklan | `SUM(spend)` | hari/minggu | neutral | currency | spend |
| `impressions` | Impresi | `SUM(impressions)` | hari/minggu | up | number | impressions |
| `clicks` | Klik | `SUM(clicks)` | hari/minggu | up | number | clicks |
| `ctr` | CTR | `SUM(clicks) * 1.0 / NULLIF(SUM(impressions), 0)` | minggu | up | percent | clicks, impressions |
| `cpc` | Biaya per Klik | `SUM(spend) / NULLIF(SUM(clicks), 0)` | minggu | down | currency | spend, clicks |
| `conversions` | Konversi | `SUM(conversions)` | hari/minggu | up | number | conversions |
| `conversion_rate` | Tingkat Konversi | `SUM(conversions) * 1.0 / NULLIF(SUM(clicks), 0)` | minggu | up | percent | conversions, clicks |
| `cpa` | Biaya per Akuisisi | `SUM(spend) / NULLIF(SUM(conversions), 0)` | minggu/bulan | down | currency | spend, conversions |
| `roas` | ROAS | `SUM(attributed_revenue) / NULLIF(SUM(spend), 0)` | minggu/bulan | up | number | attributed_revenue, spend |
| `leads` | Leads | `COUNT(DISTINCT lead_id)` | minggu | up | number | lead_id |

## Operations

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `orders_processed` | Order Diproses | `COUNT(DISTINCT order_id)` | hari | up | number | order_id |
| `on_time_rate` | Pengiriman Tepat Waktu | `SUM(CASE WHEN delivered_at <= promised_at THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0)` | hari/minggu | up | percent | delivered_at, promised_at |
| `avg_lead_time_days` | Lead Time Rata-rata (hari) | `AVG(lead_time_days)` | minggu | down | number | lead_time_days |
| `defect_rate` | Tingkat Cacat | `SUM(defect_qty) * 1.0 / NULLIF(SUM(produced_qty), 0)` | hari/minggu | down | percent | defect_qty, produced_qty |
| `utilization` | Utilisasi Kapasitas | `SUM(used_hours) / NULLIF(SUM(available_hours), 0)` | minggu | up | percent | used_hours, available_hours |
| `backlog` | Backlog | `SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END)` | hari | down | number | status |
| `cost_per_unit` | Biaya per Unit | `SUM(operating_cost) / NULLIF(SUM(produced_qty), 0)` | bulan | down | currency | operating_cost, produced_qty |
| `inventory_turnover` | Perputaran Persediaan | `SUM(cogs) / NULLIF(AVG(inventory_value), 0)` | bulan | up | number | cogs, inventory_value |

## HR

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `headcount` | Jumlah Karyawan | `COUNT(DISTINCT CASE WHEN status = 'active' THEN employee_id END)` | bulan (snapshot) | neutral | number | employee_id, status |
| `new_hires` | Karyawan Baru | `SUM(CASE WHEN event = 'hire' THEN 1 ELSE 0 END)` | bulan | neutral | number | event |
| `terminations` | Karyawan Keluar | `SUM(CASE WHEN event = 'exit' THEN 1 ELSE 0 END)` | bulan | down | number | event |
| `turnover_rate` | Tingkat Turnover | `SUM(CASE WHEN event = 'exit' THEN 1 ELSE 0 END) * 1.0 / NULLIF(AVG(headcount), 0)` | bulan/tahun | down | percent | event, headcount |
| `absenteeism_rate` | Tingkat Absensi | `SUM(absent_days) * 1.0 / NULLIF(SUM(scheduled_days), 0)` | bulan | down | percent | absent_days, scheduled_days |
| `avg_tenure_years` | Masa Kerja Rata-rata (tahun) | `AVG(tenure_years)` | bulan (snapshot) | up | number | tenure_years |
| `avg_time_to_hire_days` | Waktu Rekrut Rata-rata (hari) | `AVG(days_to_hire)` | bulan | down | number | days_to_hire |
| `payroll_cost` | Biaya Gaji | `SUM(salary)` | bulan | neutral | currency | salary |
| `training_hours_per_employee` | Jam Pelatihan per Karyawan | `SUM(training_hours) / NULLIF(COUNT(DISTINCT employee_id), 0)` | kuartal | up | number | training_hours, employee_id |

## E-commerce

| Nama | Label | Rumus SQL | Grain | Arah | Format | Kolom |
|---|---|---|---|---|---|---|
| `gmv` | GMV | `SUM(price * quantity)` | hari/bulan | up | currency | price, quantity |
| `net_revenue` | Pendapatan Bersih | `SUM(price * quantity) - SUM(discount) - SUM(refund_amount)` | bulan | up | currency | price, quantity, discount, refund_amount |
| `ecom_orders` | Jumlah Order | `COUNT(DISTINCT order_id)` | hari | up | number | order_id |
| `ecom_aov` | AOV | `SUM(price * quantity) / NULLIF(COUNT(DISTINCT order_id), 0)` | minggu/bulan | up | currency | price, quantity, order_id |
| `sessions` | Sesi | `SUM(sessions)` | hari | up | number | sessions |
| `ecom_conversion_rate` | Tingkat Konversi | `COUNT(DISTINCT order_id) * 1.0 / NULLIF(SUM(sessions), 0)` | hari/minggu | up | percent | order_id, sessions |
| `cart_abandonment_rate` | Keranjang Ditinggal | `1 - SUM(checkouts) * 1.0 / NULLIF(SUM(carts_created), 0)` | minggu | down | percent | checkouts, carts_created |
| `return_rate` | Tingkat Retur | `SUM(returned_qty) * 1.0 / NULLIF(SUM(quantity), 0)` | bulan | down | percent | returned_qty, quantity |
| `repeat_customer_rate` | Pelanggan Berulang | `COUNT(DISTINCT CASE WHEN order_seq > 1 THEN customer_id END) * 1.0 / NULLIF(COUNT(DISTINCT customer_id), 0)` | bulan | up | percent | customer_id, order_seq |
| `discount_rate` | Rasio Diskon | `SUM(discount) / NULLIF(SUM(price * quantity), 0)` | bulan | down | percent | discount, price, quantity |

## Pola SQL KPI dengan pembanding

Satu baris, nilai periode berjalan dan periode sebelumnya (contoh grain bulan, `month` bertipe
tanggal awal bulan). Cek dulu periode terakhir (`SELECT MAX(month) ...`), lalu tulis literalnya;
cara ini portabel dan tidak bergantung pada fungsi tanggal dialek tertentu:

```sql
SELECT
  SUM(CASE WHEN month = '2024-12-01' THEN revenue ELSE 0 END) AS value,
  SUM(CASE WHEN month = '2024-11-01' THEN revenue ELSE 0 END) AS prev_value
FROM finance_monthly
```

Rasio dengan pembanding: hitung pembilang dan penyebut per periode lalu bagi di luar, mis.
`SUM(CASE WHEN cur THEN profit END) / NULLIF(SUM(CASE WHEN cur THEN revenue END), 0)`.
Dengan pembanding target: `SUM(revenue) AS value, SUM(target) AS target_value`.
