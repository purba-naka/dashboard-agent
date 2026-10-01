---
topic: playbooks/sales
summary: Playbook dashboard sales untuk memantau penjualan, target, order, pelanggan, dan kontribusi per region, produk, serta sales rep.
---

# Playbook Sales

## Audiens umum

- Kepala penjualan / direksi: pencapaian target dan tren penjualan.
- Sales manager / area manager: performa per region, cabang, tim, sales rep.
- Grain lazim: bulan (eksekutif), minggu/hari (operasional); default 12 bulan terakhir atau
  tahun berjalan untuk target tahunan.

## Pertanyaan bisnis khas

1. Berapa penjualan periode ini dan apakah sesuai target?
2. Bagaimana tren penjualan per bulan, naik atau turun dibanding tahun lalu?
3. Region / cabang mana yang menyumbang penjualan terbesar dan mana yang tertinggal?
4. Produk atau kategori apa yang paling laku?
5. Siapa sales rep dengan performa terbaik dan terendah?
6. Apakah pertumbuhan berasal dari lebih banyak order atau nilai order yang lebih besar?
7. Berapa pelanggan aktif dan apakah bertambah?

## KPI inti (lihat kpi_catalog)

`sales_revenue`, `sales_vs_target`, `orders`, `avg_order_value`, `active_customers`.
Pendukung: `units_sold`, `avg_selling_price`, `sales_gross_margin`, `win_rate` (bila ada
pipeline).

## Struktur dashboard yang disarankan

| Slot | Section | Visual | Isi |
|---|---|---|---|
| `kpi_sales` | `kpi_row` | kpi | Penjualan vs bulan lalu (atau vs target) |
| `kpi_target` | `kpi_row` | kpi | Pencapaian target (%) |
| `kpi_orders` | `kpi_row` | kpi | Jumlah order vs bulan lalu |
| `kpi_aov` | `kpi_row` | kpi | AOV vs bulan lalu |
| `trend_sales` | `trend` | line atau combo bar+line | Penjualan per bulan (bar) + target/order (line, `yAxisIndex: 1` bila beda skala) |
| `breakdown_region` | `breakdown` | bar | Penjualan per region, cross filter `region` |
| `breakdown_product` | `breakdown` | bar horizontal | Top 10 produk/kategori, cross filter `category` |
| `composition_channel` | `composition` | pie (≤ 6) atau bar stack | Porsi penjualan per channel |
| `detail_rep` | `detail` | bar horizontal | Penjualan vs target per sales rep (top-N) |

Total 9 item.

Variasi:

- Ada pipeline/CRM: tambahkan `win_rate` di KPI dan `bar` horizontal per stage sebagai pengganti
  funnel.
- Musiman kuat: `heatmap` bulan × tahun atau hari × minggu.

## Slicer umum

- Global_Filter: rentang tanggal, region/cabang, channel.
- Cross_Filter: `region`, `category`, `channel`, `sales_rep` pada chart breakdown.

## Jebakan umum

- Membandingkan bulan berjalan yang belum selesai dengan bulan penuh; beri catatan atau pakai
  bulan terakhir yang lengkap.
- Target hanya tersedia per bulan per region; jangan dibagi rata ke harian tanpa asumsi.
- Menghitung order dengan `COUNT(*)` pada tabel order line (dobel); gunakan
  `COUNT(DISTINCT order_id)`.
- Pie untuk puluhan produk; pakai bar horizontal top-N.
- Revenue kotor vs bersih (diskon, retur) tidak dijelaskan; tetapkan definisi di asumsi.
- Ranking sales rep tanpa normalisasi wilayah/target bisa menyesatkan; tampilkan vs target.
