---
topic: playbooks/finance
summary: Playbook dashboard finance (Financial Analysis) dengan KPI laba rugi, combo net profit dan net margin, serta waterfall Revenue ke Net Profit.
---

# Playbook Finance

## Audiens umum

- CFO, direksi, pemilik usaha: ringkasan profitabilitas bulanan/kuartalan.
- Finance manager / controller: analisis beban, margin, dan varians anggaran.
- Grain lazim: bulan; rentang default 12 bulan terakhir atau tahun berjalan.

## Pertanyaan bisnis khas

1. Berapa revenue, beban, dan laba bersih periode ini dibanding periode sebelumnya?
2. Bagaimana tren laba bersih dan margin bersih per bulan?
3. Dari revenue, ke mana saja uang terpakai sampai menjadi laba bersih?
4. Pos beban mana yang tumbuh paling cepat?
5. Unit bisnis / cabang / produk mana yang paling menguntungkan?
6. Apakah realisasi sesuai anggaran?
7. Apakah margin kotor tergerus (kenaikan COGS lebih cepat dari revenue)?

## KPI inti (lihat kpi_catalog)

`revenue`, `expenses`, `gross_profit`, `ebit`, `net_profit` sebagai baris KPI. Pendukung:
`gross_margin`, `net_margin`, `ebit_margin`, `opex`, `opex_ratio`, `cogs`, `budget_variance`.

Arah baik: revenue/profit/margin `up`; expenses/opex/cogs `down`. Format currency IDR ringkas
(1 desimal) untuk nilai uang, percent 1 desimal untuk margin.

## Struktur dashboard "Financial Analysis"

| Slot | Section | Visual | Isi | Layout |
|---|---|---|---|---|
| `kpi_revenue` | `kpi_row` | kpi | Revenue vs bulan lalu | otomatis: 5 kartu w=2 (sisa ke kartu terakhir), h=2 |
| `kpi_expenses` | `kpi_row` | kpi | Expenses vs bulan lalu (`down`) | |
| `kpi_gross_profit` | `kpi_row` | kpi | Gross Profit vs bulan lalu | |
| `kpi_ebit` | `kpi_row` | kpi | EBIT vs bulan lalu | |
| `kpi_net_profit` | `kpi_row` | kpi | Net Profit vs bulan lalu | |
| `trend_np_margin` | `trend` | bar (combo bar+line) | Net Profit (bar) dan Net Margin % (line, `yAxisIndex: 1`) per bulan | w=8, h=6 |
| `waterfall_pnl` | `breakdown` | waterfall | Revenue → COGS → Gross Profit → Opex → EBIT → Interest & Tax → Net Profit | otomatis w=4 di samping tren (slot berikutnya setelah tren) |
| `breakdown_unit` | `breakdown` | bar horizontal | Net profit / revenue per unit bisnis atau cabang, cross filter | w=6, h=6 |
| `composition_expense` | `composition` | bar stack | Beban per bulan ditumpuk per kategori (COGS, Opex, Interest & Tax) | w=6, h=6 |
| `insight_summary` | `other` | insight | 1–2 kalimat penyebab perubahan laba | opsional |

Total 9–10 item, sesuai batas 7–9 (maks 12).

Catatan pembangunan:

- KPI: satu baris per query dengan kolom nilai bulan terakhir dan bulan sebelumnya
  (pola di kpi_catalog). `comparison_label: "vs bulan lalu"`, atau "vs target" bila ada anggaran.
- Combo: query per bulan menghasilkan `bulan`, `net_profit`, `net_margin_pct`
  (`net_profit / NULLIF(revenue, 0) * 100`). Lihat contoh combo di chart_selection.
- Waterfall: query UNION ALL di chart_selection (`step`, `base`, `delta`, `step_order`). Waterfall
  ikut Global_Filter tanggal, jadi judul sebaiknya menyebut "periode terpilih".
- Expense stack: query `bulan`, `cogs`, `opex`, `interest_tax` lalu tiga series `bar` dengan
  `stack` sama, atau bentuk panjang `bulan, kategori_beban, nilai` bila data per akun.

## Slicer umum

- Global_Filter: rentang tanggal (12 bulan terakhir), unit bisnis / entitas / cabang.
- Cross_Filter: `business_unit`, `branch`, `cost_center`, `expense_category` pada bar breakdown.
- Jangan cross filter dari waterfall (`step` adalah alias SQL).

## Jebakan umum

- Margin dihitung sebagai rata-rata margin bulanan. Selalu `SUM(profit) / SUM(revenue)`.
- Tanda beban tidak konsisten (sebagian negatif). Pastikan beban positif sebelum dikurangkan.
- Definisi "expenses" ambigu (opex saja vs semua beban). Nyatakan di asumsi Design_Brief.
- Global_Filter satu bulan membuat pembanding "bulan lalu" kosong; default rentang harus
  mencakup periode pembanding (lihat interaction).
- Waterfall dengan laba negatif menghasilkan batang salah; jelaskan atau pakai bar biasa.
- Mencampur data akrual dan kas, atau bulan berjalan yang belum tutup buku; tandai dengan
  insight bila bulan terakhir belum lengkap.
- Terlalu banyak KPI rasio di baris atas; cukup 5 KPI nilai, rasio di tren.
