---
topic: playbooks/operations
summary: Playbook dashboard operations untuk memantau throughput, ketepatan waktu, lead time, kualitas, utilisasi, backlog, dan biaya per unit.
---

# Playbook Operations

## Audiens umum

- Kepala operasional / COO: ketepatan layanan dan efisiensi biaya.
- Supervisor gudang, produksi, logistik: kondisi harian per lokasi, lini, shift.
- Grain lazim: hari/minggu; default 30–90 hari terakhir.

## Pertanyaan bisnis khas

1. Berapa order/unit yang diproses dan apakah throughput sesuai rencana?
2. Berapa persen pengiriman tepat waktu, dan trennya?
3. Berapa lead time rata-rata dan di lokasi mana paling lama?
4. Berapa tingkat cacat/defect per lini atau pemasok?
5. Apakah kapasitas terpakai optimal (utilisasi)?
6. Berapa backlog terbuka dan apakah menumpuk?
7. Kapan (hari/jam/shift) beban kerja puncak terjadi?
8. Berapa biaya per unit dan apakah membaik?

## KPI inti (lihat kpi_catalog)

`orders_processed`, `on_time_rate`, `avg_lead_time_days`, `defect_rate`, `backlog`.
Pendukung: `utilization`, `cost_per_unit`, `inventory_turnover`.

Arah: `on_time_rate`/`utilization` `up`; `avg_lead_time_days`/`defect_rate`/`backlog`/
`cost_per_unit` `down`.

## Struktur dashboard yang disarankan

| Slot | Section | Visual | Isi |
|---|---|---|---|
| `kpi_throughput` | `kpi_row` | kpi | Order diproses vs minggu lalu |
| `kpi_ontime` | `kpi_row` | kpi | On-time rate vs target (mis. 95 %) |
| `kpi_leadtime` | `kpi_row` | kpi | Lead time rata-rata vs minggu lalu (`down`) |
| `kpi_defect` | `kpi_row` | kpi | Defect rate vs minggu lalu (`down`) |
| `kpi_backlog` | `kpi_row` | kpi | Backlog terbuka vs kemarin (`down`) |
| `trend_volume_ontime` | `trend` | combo bar+line | Volume (bar) + on-time rate % (line, `yAxisIndex: 1`) per hari/minggu |
| `breakdown_site` | `breakdown` | bar horizontal | Lead time atau on-time per lokasi/gudang, cross filter `site` |
| `breakdown_defect` | `breakdown` | bar | Defect per lini/pemasok/jenis cacat, cross filter `line` |
| `distribution_load` | `distribution` | heatmap | Volume per hari × jam (atau shift) dengan `visualMap` |

Total 9 item.

## Slicer umum

- Global_Filter: rentang tanggal, lokasi/gudang/pabrik, jenis layanan.
- Cross_Filter: `site`, `line`, `carrier`, `supplier`, `shift`.

## Jebakan umum

- Rata-rata lead time disembunyikan outlier; pertimbangkan histogram (`bar` dari bin) atau
  median bila didukung.
- On-time rate dihitung dari order yang belum selesai (`delivered_at` kosong); filter hanya
  order selesai, nyatakan aturannya.
- Backlog adalah snapshot, bukan penjumlahan; jangan `SUM` lintas hari.
- Utilisasi > 100 % menandakan data jam tersedia tidak lengkap.
- Heatmap tanpa `visualMap.min/max` yang sesuai rentang data sulit dibaca.
- Membandingkan lokasi dengan volume sangat berbeda tanpa normalisasi (pakai rasio, bukan jumlah).
