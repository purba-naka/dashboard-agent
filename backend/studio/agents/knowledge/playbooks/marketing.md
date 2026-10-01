---
topic: playbooks/marketing
summary: Playbook dashboard marketing untuk memantau biaya iklan, funnel impresi-klik-konversi, efisiensi CPA dan ROAS per channel serta kampanye.
---

# Playbook Marketing

## Audiens umum

- CMO / kepala marketing: efektivitas belanja iklan dan kontribusi ke revenue.
- Performance marketer / campaign manager: optimasi per channel, kampanye, creative.
- Grain lazim: hari/minggu; default 30–90 hari terakhir.

## Pertanyaan bisnis khas

1. Berapa total belanja iklan dan hasilnya (konversi, revenue) periode ini?
2. Apakah efisiensi membaik (CPA turun, ROAS naik)?
3. Channel mana yang paling efisien dan mana yang boros?
4. Kampanye mana yang perlu dihentikan atau ditambah anggarannya?
5. Di tahap mana funnel paling banyak bocor (impresi → klik → konversi)?
6. Apakah ada hubungan antara belanja dan konversi (diminishing return)?
7. Hari/jam apa konversi paling tinggi?

## KPI inti (lihat kpi_catalog)

`ad_spend`, `conversions`, `cpa`, `roas`, `ctr`. Pendukung: `impressions`, `clicks`, `cpc`,
`conversion_rate`, `leads`.

Arah: `cpa`/`cpc` `down`; `roas`/`ctr`/`conversion_rate` `up`; `ad_spend` `neutral`
(naik belum tentu buruk).

## Struktur dashboard yang disarankan

| Slot | Section | Visual | Isi |
|---|---|---|---|
| `kpi_spend` | `kpi_row` | kpi | Ad spend vs minggu lalu (`neutral`) |
| `kpi_conv` | `kpi_row` | kpi | Konversi vs minggu lalu |
| `kpi_cpa` | `kpi_row` | kpi | CPA vs minggu lalu (`down`) |
| `kpi_roas` | `kpi_row` | kpi | ROAS vs target/minggu lalu |
| `trend_spend_conv` | `trend` | combo bar+line | Spend (bar, Rp) + konversi (line, `yAxisIndex: 1`) per minggu |
| `breakdown_channel` | `breakdown` | bar | CPA atau ROAS per channel, cross filter `channel` |
| `breakdown_funnel` | `breakdown` | bar horizontal | Impresi, klik, konversi berurutan (pengganti funnel) |
| `distribution_spend_conv` | `distribution` | scatter | Spend vs konversi per kampanye |
| `detail_campaign` | `detail` | bar horizontal | Top 10 kampanye berdasarkan konversi atau CPA, cross filter `campaign` |

Opsional: `heatmap` hari × jam konversi bila ada timestamp.

## Slicer umum

- Global_Filter: rentang tanggal, channel/platform, negara/region.
- Cross_Filter: `channel`, `campaign`, `ad_group`, `device`.

## Jebakan umum

- Merata-rata CTR/CPA per kampanye; hitung dari agregat (`SUM(clicks)/SUM(impressions)`).
- Atribusi berbeda antar platform (double counting konversi); nyatakan model atribusi di asumsi.
- Funnel dengan skala sangat berbeda (impresi jutaan vs konversi puluhan); pertimbangkan rasio
  antar tahap atau bar terpisah.
- Pie untuk banyak kampanye; pakai bar horizontal top-N.
- Spend dan revenue dari tabel berbeda tanpa Confirmed_Relation membuat ROAS tidak ikut filter.
- Data hari terakhir sering belum final (lag platform iklan); beri catatan.
