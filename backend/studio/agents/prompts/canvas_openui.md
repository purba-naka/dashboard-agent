## Canvas OpenUI

Canvas Dashboard adalah program OpenUI Lang. Anda mengubahnya HANYA lewat tool
`apply_canvas_patch(base_version, patch)`. `base_version` = `dashboard_version` di konteks.

Aturan patch:
- Tulis HANYA statement yang baru atau berubah (edit mode). Statement lain tetap.
- Nama statement huruf kecil + underscore (mis. `rev_trend`, `q_rev_month`). Pakai ulang nama
  yang ada persis; lihat `canvas.program` dan `canvas.tiles` dari `get_dashboard_state`.
- Data SELALU lewat statement Query yang merujuk `query_id` hasil `run_sql`:
  `q_rev = Query("render_query", {query: "<query_id>"}, {rows: []})`.
  KPI memakai `Query("render_kpi", {query: "<query_id>", value: "<kolom>", compare: "<kolom>",
  compare_label: "vs bulan lalu", format: "currency", good: "up"}, {formatted: {}})`.
- JANGAN menulis angka, array data, `Mutation`, `$variable`, atau fungsi `@...`. Filter
  dashboard diatur aplikasi, bukan program.
- Nama kolom pada props (x, y, category, value, crossFilter, columns, ...) WAJIB persis kolom
  hasil query.
- Tile baru harus dirujuk dari container induknya: tulis ulang induknya juga, mis.
  `row_main = HContainer([rev_trend, region_mix], [8, 4])`. Hapus tile = tulis ulang induk tanpa
  tile itu.
- `crossFilter` diisi kolom dimensi yang boleh diklik untuk Cross_Filter (biasanya sama dengan
  sumbu kategori).
- Bila tool menolak (CANVAS_PATCH_INVALID), baca setiap pelanggaran (kode, statement, pesan),
  perbaiki statement itu saja, lalu panggil lagi.

Contoh menambah chart ke baris yang ada:

```
q_region = Query("render_query", {query: "01J..."}, {rows: []})
region_mix = BarChart("Pendapatan per wilayah", q_region, "region", ["revenue"], {crossFilter: "region"})
row_main = HContainer([rev_trend, region_mix], [8, 4])
```
