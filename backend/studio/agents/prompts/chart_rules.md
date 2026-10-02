## Pemilihan tipe chart

`chart_type` hanya boleh: `line`, `bar`, `pie`, `scatter`, `heatmap`. Pilih dari tujuan analisis
dan tipe kolom hasil query, bukan dari selera.

| Tujuan | Kolom hasil query | Pilihan |
|---|---|---|
| Tren dari waktu ke waktu | 1 kolom time + 1..n measure | `line` |
| Tren kumulatif / komposisi dari waktu ke waktu | time + beberapa measure | `line` dengan `areaStyle` dan `stack` |
| Dua measure dengan skala berbeda (mis. revenue vs qty) | time/dimension + 2 measure | `bar` + `line` (combo), dua `yAxis`, series kedua `yAxisIndex: 1` |
| Perbandingan antar kategori | 1 dimension + 1..n measure | `bar` |
| Kategori banyak (> 8) atau label panjang | dimension + measure | `bar` horizontal (`yAxis` category, `xAxis` value) |
| Komposisi per kategori | dimension + beberapa measure | `bar` dengan `stack` yang sama |
| Proporsi dari keseluruhan | 1 dimension (≤ 8 nilai) + 1 measure | `pie` |
| Hubungan / korelasi dua measure | 2 measure numerik | `scatter` |
| Intensitas pada dua dimensi (mis. hari × jam) | 2 dimension + 1 measure | `heatmap` dengan `visualMap` (isi `min`/`max` dari rentang nilai hasil query) |
| Distribusi (histogram) | butuh query yang sudah di-bin | `bar` dari hasil bin |

Aturan tambahan:
- Pie lebih dari 8 kategori ditolak backend. Pakai `bar`.
- Jangan pakai pie untuk membandingkan nilai yang mirip besarnya; `bar` lebih mudah dibaca.
- Satu angka ringkas (KPI) bukan chart: buat dengan `add_kpi` (value_column, comparison_column,
  comparison_label mis. "vs bulan lalu", format_style currency/percent/number, good_direction
  up untuk revenue/profit, down untuk biaya). Query KPI harus tepat satu baris.
- Waterfall (jembatan total, mis. Revenue → Net Profit) bukan tipe baru: `chart_type` `bar`
  dengan dua series ber-`stack` sama, series pertama `base` transparan
  (`"itemStyle": {"color": "transparent"}`, encode y = `base`) dan series kedua `delta`
  (encode y = `delta`), sumbu x = `step`. Kolom `step`, `base`, `delta`, `step_order` dihitung
  di SQL. Jangan jadikan `step` sebagai `cross_filter_column`.
- Bila hasil query belum berbentuk yang dibutuhkan (perlu agregasi, bin, atau pivot), butuh
  query baru.
- Peta, sankey, treemap, funnel, radar, word cloud, dan diagram tidak didukung. Tawarkan
  alternatif terdekat dari tabel di atas.

## Aturan Chart_Spec

- `option` hanya boleh berisi: `title`, `legend`, `tooltip`, `grid`, `xAxis`, `yAxis`, `series`,
  `color`, `dataZoom`, `visualMap`, `toolbox`.
- Jangan pernah menulis data: tidak ada `dataset`, `series[].data`, atau `xAxis.data`. Setiap series
  memakai `encode` yang menyebut nama kolom hasil query persis (pie: `itemName` + `value`;
  heatmap: `x`, `y`, `value`). Backend mengisi data.
- `line`, `bar`, `scatter`, `heatmap` wajib punya `xAxis` dan `yAxis`. `pie` tidak boleh punya sumbu.
- Tidak boleh ada fungsi atau kode di string (mis. formatter berupa fungsi).

Setiap chart wajib:
- `title.text` deskriptif (judul tampil di header kartu); `title.subtext` opsional untuk satu baris konteks,
- label sumbu (X dan Y) yang jelas, lewat `name` pada sumbu,
- `tooltip` (`trigger: "axis"` untuk line/bar, `"item"` untuk pie/scatter/heatmap).

Tata letak diatur frontend. Jangan tulis `grid`, posisi `legend`, `label.textBorder*`, `axisLabel.rotate`, atau ukuran font.

Multi-series dari satu measure (mis. satu garis per kelompok): hasil query berbentuk long
(`periode, kelompok, nilai`). Buat satu series per nilai `kelompok` dengan `name` persis sama dengan nilai
kolom itu dan `encode` yang sama. Backend memisahkan datanya per `name`. Jangan beri `name` yang tidak ada di kolom.

## Contoh (satu per tipe; nama kolom ilustrasi)

```json
{"chart_type": "line", "option": {"title": {"text": "Revenue per bulan"}, "tooltip": {"trigger": "axis"}, "xAxis": {"type": "time", "name": "Bulan"}, "yAxis": {"type": "value", "name": "Revenue"}, "series": [{"type": "line", "encode": {"x": "bulan", "y": "revenue"}}]}}
```

```json
{"chart_type": "bar", "option": {"title": {"text": "Revenue dan qty per bulan"}, "tooltip": {"trigger": "axis"}, "legend": {}, "xAxis": {"type": "category", "name": "Bulan"}, "yAxis": [{"type": "value", "name": "Revenue"}, {"type": "value", "name": "Qty"}], "series": [{"type": "bar", "name": "Revenue", "encode": {"x": "bulan", "y": "revenue"}}, {"type": "line", "name": "Qty", "yAxisIndex": 1, "encode": {"x": "bulan", "y": "qty"}}]}}
```

```json
{"chart_type": "pie", "option": {"title": {"text": "Porsi revenue per region"}, "tooltip": {"trigger": "item"}, "legend": {}, "series": [{"type": "pie", "radius": ["40%", "70%"], "encode": {"itemName": "region", "value": "revenue"}}]}}
```

```json
{"chart_type": "scatter", "option": {"title": {"text": "Harga vs biaya"}, "tooltip": {"trigger": "item"}, "xAxis": {"type": "value", "name": "Harga"}, "yAxis": {"type": "value", "name": "Biaya"}, "series": [{"type": "scatter", "encode": {"x": "harga", "y": "biaya"}}]}}
```

```json
{"chart_type": "heatmap", "option": {"title": {"text": "Qty per hari dan jam"}, "tooltip": {"trigger": "item"}, "xAxis": {"type": "category", "name": "Jam"}, "yAxis": {"type": "category", "name": "Hari"}, "visualMap": {"min": 0, "max": 100}, "series": [{"type": "heatmap", "encode": {"x": "jam", "y": "hari", "value": "qty"}}]}}
```

Bila tool menolak spec (mis. `UNKNOWN_COLUMN`, `AXIS_STRUCTURE`), baca `path` dan `detail` di
error, perbaiki bagian itu saja, lalu coba lagi. Bila VERSION_CONFLICT, pakai versi baru dari
error lalu coba lagi.

<!-- Panduan pemilihan chart diadaptasi dari skill chart-visualization
(antvis/chart-visualization-skills, lisensi MIT), dipetakan ke ECharts. -->
