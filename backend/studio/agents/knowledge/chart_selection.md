---
topic: chart_selection
summary: Panduan memilih tipe visual (line, bar, pie, scatter, heatmap, KPI, pola waterfall) dari tujuan analisis dan bentuk kolom hasil query.
---

# Panduan Pemilihan Chart

`chart_type` yang didukung hanya: `line`, `bar`, `pie`, `scatter`, `heatmap`. Selain itu ada:

- **KPI** (bukan chart): kartu angka besar dibuat via tool `add_kpi`.
- **Waterfall** (bukan `chart_type` baru): pola `bar` bertumpuk, lihat bagian Waterfall.

Peta, sankey, treemap, funnel, radar, gauge, word cloud, dan diagram tidak didukung. Tawarkan
alternatif terdekat: funnel → `bar` horizontal berurutan, treemap → `bar` horizontal top-N,
gauge → KPI dengan pembanding target, peta → `bar` per wilayah.

## Tabel tujuan → kolom hasil query → pilihan

| Tujuan | Kolom hasil query | Pilihan |
|---|---|---|
| Satu angka ringkas + pembanding | tepat 1 baris: kolom nilai (+ kolom pembanding) | KPI via `add_kpi` |
| Tren dari waktu ke waktu | 1 time + 1..n measure | `line` |
| Tren komposisi / kumulatif | time + beberapa measure | `line` dengan `areaStyle` dan `stack` sama |
| Dua measure beda skala/satuan | time/dimension + 2 measure | combo `bar` + `line`, dua `yAxis`, series kedua `yAxisIndex: 1` |
| Perbandingan antar kategori (≤ 8) | 1 dimension + 1..n measure | `bar` vertikal |
| Kategori banyak (> 8) atau label panjang | dimension + measure | `bar` horizontal (`yAxis` category, `xAxis` value), urut desc, top-N |
| Komposisi per kategori | dimension + beberapa measure, atau 2 dimension + measure | `bar` dengan `stack` sama |
| Proporsi dari keseluruhan | 1 dimension (≤ 8 nilai, ideal ≤ 6) + 1 measure | `pie` (donut `radius: ["40%","70%"]`) |
| Hubungan / korelasi | 2 measure numerik (+ dimension opsional) | `scatter` |
| Intensitas dua dimensi | 2 dimension + 1 measure | `heatmap` dengan `visualMap` |
| Distribusi (histogram) | kolom bin + count (bin dibuat di SQL) | `bar` |
| Jembatan dari total ke total (P&L, varians) | step + base + delta | pola waterfall (`bar` stack) |

Aturan tambahan:

- `pie` > 8 kategori ditolak backend; > 6 memicu `PIE_TOO_MANY_SLICES`. Pakai `bar`.
- Jangan pakai `pie` untuk nilai yang mirip besarnya atau untuk tren waktu.
- Dua sumbu Y hanya bila satuan/skala berbeda (Rp vs %, Rp vs qty).
- Bila bentuk hasil query belum sesuai (perlu agregasi, bin, pivot, base/delta), minta Query_Agent
  membuat query baru. Chart_Designer tidak mengubah data.

## Aturan Chart_Spec (ringkas)

- `option` hanya boleh memakai key: `title`, `legend`, `tooltip`, `grid`, `xAxis`, `yAxis`,
  `series`, `color`, `dataZoom`, `visualMap`, `toolbox`.
- Tanpa data inline: tidak ada `dataset`, `series[].data`, `xAxis.data`. Setiap series memakai
  `encode` dengan nama kolom hasil query persis.
- Tanpa fungsi/kode di string (mis. formatter fungsi).
- `line`/`bar`/`scatter`/`heatmap` wajib `xAxis` dan `yAxis` (dengan `name`); `pie` tanpa sumbu.
- `tooltip.trigger`: `"axis"` untuk line/bar, `"item"` untuk pie/scatter/heatmap.
- Legenda bila > 1 series yang terlihat.

## KPI (via `add_kpi`)

KPI bukan chart dan bukan insight. Syarat:

- Query menghasilkan **tepat satu baris** (selain itu render error `KPI_SHAPE`).
- `value_column`: kolom numerik nilai. `comparison_column` (opsional, sangat dianjurkan): kolom
  numerik pembanding (periode sebelumnya atau target) di baris yang sama.
- `comparison_label` mis. "vs bulan lalu"; `good_direction` `up`/`down`/`neutral`;
  `format` `{style: currency|percent|number, decimals, compact}`; `metric_name` dari
  kpi_catalog/Semantic_Model.

Contoh SQL KPI net profit bulan terakhir vs bulan sebelumnya:

```sql
SELECT
  SUM(CASE WHEN month = '2024-12-01' THEN revenue - cogs - opex - interest_tax ELSE 0 END) AS net_profit,
  SUM(CASE WHEN month = '2024-11-01' THEN revenue - cogs - opex - interest_tax ELSE 0 END) AS prev_net_profit
FROM finance_monthly
```

Spec KPI: `value_column: "net_profit"`, `comparison_column: "prev_net_profit"`,
`comparison_label: "vs bulan lalu"`, `good_direction: "up"`,
`format: {"style": "currency", "decimals": 1, "compact": true}`.

## Combo bar + line (dua skala)

Contoh net profit (Rp) dan net margin (%) per bulan:

```json
{"chart_type": "bar", "option": {
  "title": {"text": "Bagaimana laba bersih dan marginnya bergerak tiap bulan?"},
  "tooltip": {"trigger": "axis"},
  "legend": {},
  "xAxis": {"type": "category", "name": "Bulan"},
  "yAxis": [{"type": "value", "name": "Net Profit (Rp)"}, {"type": "value", "name": "Net Margin (%)"}],
  "series": [
    {"type": "bar", "name": "Net Profit", "encode": {"x": "bulan", "y": "net_profit"}},
    {"type": "line", "name": "Net Margin", "yAxisIndex": 1, "encode": {"x": "bulan", "y": "net_margin_pct"}}
  ]
}}
```

## Pola WATERFALL

Waterfall menunjukkan bagaimana satu total berubah menjadi total lain melalui langkah tambah dan
kurang (mis. Revenue → Net Profit). Ini **bukan** `chart_type` baru, melainkan:

- `chart_type: "bar"`, satu `xAxis` category (langkah) dan satu `yAxis` value.
- Dua series `bar` dengan `stack` yang sama (mis. `"total"`):
  1. **base**: transparan (`itemStyle: {"color": "transparent"}`), boleh
     `tooltip: {"show": false}`. Mengangkat batang delta ke posisi yang benar.
  2. **delta**: batang terlihat, tinggi = nilai absolut langkah.
- Kolom `step`, `base`, `delta` (dan `step_order` untuk urutan) dihitung di **SQL**:
  - `delta` = nilai absolut langkah, selalu ≥ 0.
  - `base` = total kumulatif sebelum langkah untuk langkah penambah; untuk langkah pengurang,
    base = kumulatif sebelum langkah dikurangi delta (yaitu kumulatif sesudah langkah), sehingga
    batang menggantung dari level sebelumnya ke level baru.
  - Langkah total (Revenue, Gross Profit, EBIT, Net Profit): `base = 0`, `delta = total`.
- Pola ini mengasumsikan semua level kumulatif ≥ 0. Bila laba bisa negatif, pakai `bar` biasa
  per komponen atau jelaskan keterbatasannya.
- `step` adalah alias hasil SQL, jadi jangan dijadikan `cross_filter_column`.

### Contoh SQL waterfall finance

Tabel `finance_monthly` dengan kolom `revenue`, `cogs`, `opex`, `interest_tax` (beban positif).
Global_Filter tanggal tetap berlaku karena agregasi dilakukan atas tabel sumber.

```sql
WITH t AS (
  SELECT
    SUM(revenue) AS rev,
    SUM(cogs) AS cogs,
    SUM(opex) AS opex,
    SUM(interest_tax) AS tax
  FROM finance_monthly
)
SELECT 1 AS step_order, 'Revenue' AS step, 0 AS base, rev AS delta FROM t
UNION ALL
SELECT 2, 'COGS', rev - cogs, cogs FROM t
UNION ALL
SELECT 3, 'Gross Profit', 0, rev - cogs FROM t
UNION ALL
SELECT 4, 'Opex', rev - cogs - opex, opex FROM t
UNION ALL
SELECT 5, 'EBIT', 0, rev - cogs - opex FROM t
UNION ALL
SELECT 6, 'Interest & Tax', rev - cogs - opex - tax, tax FROM t
UNION ALL
SELECT 7, 'Net Profit', 0, rev - cogs - opex - tax FROM t
ORDER BY step_order
```

### Contoh Chart_Spec waterfall

```json
{"chart_type": "bar", "option": {
  "title": {"text": "Dari Revenue ke Net Profit: ke mana uang pergi?"},
  "tooltip": {"trigger": "axis"},
  "legend": {"show": false},
  "grid": {"left": 80, "right": 24, "bottom": 48},
  "xAxis": {"type": "category", "name": "Langkah"},
  "yAxis": {"type": "value", "name": "Nilai (Rp)"},
  "color": ["#3b82f6"],
  "series": [
    {"type": "bar", "name": "base", "stack": "total",
     "itemStyle": {"color": "transparent"}, "tooltip": {"show": false},
     "encode": {"x": "step", "y": "base"}},
    {"type": "bar", "name": "Nilai", "stack": "total",
     "label": {"show": true, "position": "top"},
     "encode": {"x": "step", "y": "delta"}}
  ]
}}
```

Catatan: `option` hanya memakai key yang diizinkan, tanpa `data` inline, tanpa formatter fungsi.
Urutan langkah mengikuti urutan baris hasil query (`ORDER BY step_order`).

## Contoh singkat tipe lain

- `line`: `xAxis {"type": "time"|"category", "name": "Bulan"}`, series
  `{"type": "line", "encode": {"x": "bulan", "y": "revenue"}}`.
- `bar` horizontal: `xAxis {"type": "value"}`, `yAxis {"type": "category", "inverse": true}`,
  `encode {"x": "revenue", "y": "produk"}`.
- `bar` stack: beberapa series dengan `"stack": "s"` dan `encode` y berbeda.
- `pie`: `encode {"itemName": "region", "value": "revenue"}`, tanpa sumbu.
- `scatter`: `encode {"x": "spend", "y": "conversions"}`, `tooltip.trigger: "item"`.
- `heatmap`: dua sumbu category, `visualMap {"min": .., "max": .., "calculable": true}`,
  `encode {"x": "jam", "y": "hari", "value": "orders"}`.
