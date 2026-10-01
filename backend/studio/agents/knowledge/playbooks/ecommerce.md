---
topic: playbooks/ecommerce
summary: Playbook dashboard e-commerce untuk memantau GMV, order, AOV, konversi, retur, pelanggan berulang, serta performa produk dan channel.
---

# Playbook E-commerce

## Audiens umum

- Pemilik toko / kepala e-commerce: GMV, pertumbuhan, profitabilitas promo.
- Category manager / tim growth: performa produk, kategori, channel/marketplace, promo.
- Grain lazim: hari (operasional), minggu/bulan (manajemen); default 30–90 hari terakhir.

## Pertanyaan bisnis khas

1. Berapa GMV dan jumlah order periode ini dibanding periode sebelumnya?
2. Apakah pertumbuhan berasal dari lebih banyak order atau AOV yang lebih tinggi?
3. Berapa tingkat konversi pengunjung menjadi pembeli, dan trennya?
4. Produk/kategori apa yang paling laku dan mana yang sering diretur?
5. Marketplace/channel mana yang menyumbang GMV terbesar?
6. Berapa persen pelanggan yang membeli ulang?
7. Apakah diskon menggerus pendapatan bersih?
8. Kapan (hari × jam) order paling ramai?

## KPI inti (lihat kpi_catalog)

`gmv`, `ecom_orders`, `ecom_aov`, `ecom_conversion_rate`, `net_revenue`.
Pendukung: `sessions`, `cart_abandonment_rate`, `return_rate`, `repeat_customer_rate`,
`discount_rate`.

## Struktur dashboard yang disarankan

| Slot | Section | Visual | Isi |
|---|---|---|---|
| `kpi_gmv` | `kpi_row` | kpi | GMV vs periode sebelumnya |
| `kpi_orders` | `kpi_row` | kpi | Order vs periode sebelumnya |
| `kpi_aov` | `kpi_row` | kpi | AOV vs periode sebelumnya |
| `kpi_conv` | `kpi_row` | kpi | Conversion rate vs periode sebelumnya |
| `kpi_return` | `kpi_row` | kpi | Return rate (`down`) |
| `trend_gmv_orders` | `trend` | combo bar+line | GMV (bar, Rp) + order (line, `yAxisIndex: 1`) per hari/minggu |
| `breakdown_category` | `breakdown` | bar horizontal | Top 10 kategori/produk berdasarkan GMV, cross filter `category` |
| `composition_channel` | `composition` | pie (≤ 6) atau bar stack per bulan | Porsi GMV per marketplace/channel, cross filter `channel` |
| `distribution_hour` | `distribution` | heatmap | Order per hari × jam dengan `visualMap` |

Opsional: waterfall GMV → diskon → retur → pendapatan bersih (pola di chart_selection) atau
`scatter` diskon vs qty per produk.

## Slicer umum

- Global_Filter: rentang tanggal, channel/marketplace, kategori produk.
- Cross_Filter: `category`, `channel`, `brand`, `payment_method`, `city`.

## Jebakan umum

- GMV vs pendapatan bersih tercampur; tetapkan definisi (sebelum/sesudah diskon, retur, ongkir).
- Order dihitung dari tabel order item tanpa `DISTINCT` (dobel).
- Order dibatalkan/tidak dibayar ikut terhitung; filter status di SQL dan nyatakan di asumsi.
- Konversi butuh data sesi; bila sesi dari tabel lain tanpa relasi, rasio tidak ikut filter.
- Retur tercatat di periode berbeda dari penjualan; jelaskan basis tanggalnya.
- Pie untuk ratusan produk; pakai bar horizontal top-N.
