---
topic: principles
summary: Prinsip desain dashboard BI, mulai dari tujuan dan audiens sampai hierarki visual, perbandingan KPI, warna, format angka, dan judul.
---

# Prinsip Desain Dashboard BI

Dashboard yang baik menjawab pertanyaan bisnis tertentu untuk audiens tertentu dengan cepat.
Gunakan prinsip di bawah saat menyusun Design_Brief, Dashboard_Blueprint, dan saat review.

## 1. Mulai dari tujuan dan audiens

Sebelum memilih chart, tetapkan:

- **Tujuan**: keputusan apa yang didukung dashboard ini (mis. "memantau profitabilitas bulanan").
- **Audiens**: siapa pembacanya dan seberapa sering.
  - Eksekutif: butuh ringkasan, KPI + tren, sedikit visual, tanpa tabel detail panjang.
  - Manajer: butuh KPI, tren, dan breakdown per dimensi utama untuk mencari penyebab.
  - Analis/operasional: butuh breakdown lebih dalam, detail, dan banyak filter.
- **Pertanyaan bisnis kunci**: 3–7 pertanyaan konkret. Setiap visual harus menjawab minimal satu.
  Visual yang tidak menjawab pertanyaan apa pun sebaiknya dibuang.
- **Periode dan grain**: rentang waktu default (mis. 12 bulan terakhir) dan grain (hari, minggu,
  bulan, kuartal, tahun). Grain eksekutif biasanya bulan atau kuartal.

Bila informasi ini tidak diberikan, nyatakan asumsi secara eksplisit di Design_Brief.

## 2. Hierarki: overview → tren → breakdown → detail

Susun dari umum ke khusus, mengikuti cara orang bertanya:

| Lapis | Pertanyaan | Section | Visual umum |
|---|---|---|---|
| Overview | "Bagaimana kondisi sekarang?" | `kpi_row` | KPI (via `add_kpi`) |
| Tren | "Naik atau turun? Sejak kapan?" | `trend` | `line`, combo `bar`+`line` |
| Breakdown | "Di mana / siapa penyebabnya?" | `breakdown`, `composition` | `bar`, `bar` horizontal, `bar` stack, `pie`, `waterfall` |
| Distribusi / hubungan | "Bagaimana sebarannya? Apa yang berkorelasi?" | `distribution` | `scatter`, `heatmap`, histogram `bar` |
| Detail | "Item mana persisnya?" | `detail` | `bar` horizontal top-N, insight teks |

## 3. Aturan 5 detik

Pembaca harus bisa menangkap pesan utama dalam sekitar 5 detik. Artinya:

- KPI terpenting terlihat langsung tanpa scroll.
- Tidak ada visual yang butuh legenda panjang untuk dipahami.
- Satu visual = satu pesan. Jangan menumpuk 6 series di satu chart bila 2 cukup.

## 4. Jumlah visual

- Target **±7–9 visual per halaman**, termasuk KPI.
- Maksimal **12 item**. Lebih dari itu memicu temuan `TOO_MANY_VISUALS`; pecah menjadi
  beberapa dashboard (mis. "Ringkasan" dan "Detail Produk").
- Baris KPI idealnya 3–6 kartu. Lebih dari 6 KPI biasanya tanda belum memprioritaskan.

## 5. Pola baca Z dan tata letak

Pembaca memindai dari kiri atas ke kanan, lalu turun diagonal ke kiri bawah (pola Z).

- **Baris paling atas: KPI** (`kpi_row`). KPI yang berada di bawah chart lain memicu
  `KPI_NOT_ON_TOP`.
- Baris kedua: tren utama di kiri (lebar 8 dari 12 kolom), visual pendamping di kanan (lebar 4).
- Baris berikutnya: breakdown berpasangan (lebar 6 + 6).
- Paling bawah: detail (lebar 12).
- Visual terpenting di kiri atas; visual yang saling terkait diletakkan berdekatan.

## 6. Setiap KPI wajib punya pembanding

Angka tanpa konteks tidak bisa dinilai. Setiap KPI sebaiknya punya `comparison_column`:

- **Periode sebelumnya** (bulan lalu, kuartal lalu, tahun lalu/YoY), dengan `comparison_label`
  seperti "vs bulan lalu".
- **Target/anggaran** bila tersedia kolom target, dengan label "vs target".
- Tetapkan `good_direction`: `up` untuk revenue/profit, `down` untuk biaya/churn/lead time,
  `neutral` bila naik-turun tidak otomatis baik atau buruk.

KPI tanpa pembanding memicu `KPI_NO_COMPARISON`. Bila memang tidak ada pembanding yang masuk
akal, nyatakan alasannya sebagai asumsi.

## 7. Warna konsisten dan bermakna

- Satu metrik = satu warna di seluruh dashboard (mis. Revenue selalu biru).
- Pakai warna netral (abu/biru) sebagai default; warna mencolok hanya untuk penekanan.
- Hijau = baik, merah = buruk, sesuai `good_direction`; jangan pakai merah untuk hal netral.
- Untuk kategori, cukup palet 6–8 warna yang mudah dibedakan. Hindari pelangi.
- Heatmap memakai gradasi satu warna (terang = rendah, gelap = tinggi) lewat `visualMap`.
- Jangan mengandalkan warna saja: label, legenda, dan tooltip tetap wajib (aksesibilitas).

## 8. Hindari chart junk

- Tanpa efek 3D, bayangan, gradasi dekoratif, atau ikon yang tidak membawa informasi.
- Kurangi gridline; cukup gridline horizontal tipis pada sumbu nilai.
- Sumbu nilai `bar` dimulai dari nol. `line` boleh tidak dari nol bila fokus pada perubahan.
- Jangan pakai `pie` untuk lebih dari 6–8 kategori atau nilai yang mirip besarnya.
- Jangan pakai dua sumbu Y kecuali dua measure memang berbeda satuan/skala.
- Hindari `dataZoom` dan `toolbox` kecuali data panjang (mis. harian > 1 tahun).

## 9. Format angka konsisten (id-ID)

- Pemisah ribuan titik, desimal koma: `1.234.567,89`.
- Mata uang: `Rp` di depan, mis. `Rp 1,2 M`.
- Skala ringkas: `rb` (ribu), `jt` (juta), `M` (miliar), `T` (triliun). Gunakan `compact: true`
  untuk KPI.
- Persen: `12,5 %`, umumnya 1 desimal.
- Satu metrik harus memakai format yang sama di semua KPI dan chart; beda format memicu
  `INCONSISTENT_METRIC_FORMAT`.
- Jumlah desimal secukupnya: uang ringkas 1 desimal, hitungan 0 desimal, rasio 1 desimal.

## 10. Judul berupa pertanyaan atau insight

- Judul menjelaskan apa yang dijawab, bukan sekadar nama kolom.
  - Kurang: "revenue by month".
  - Lebih baik: "Bagaimana tren revenue 12 bulan terakhir?" atau
    "Revenue naik 3 bulan berturut-turut".
- Judul insight (berisi kesimpulan) hanya bila kesimpulannya stabil terhadap filter; bila
  filter bisa mengubah kesimpulan, pakai judul pertanyaan.
- Setiap item wajib punya judul (`MISSING_TITLE`).

## 11. Label sumbu dan satuan

- Setiap chart cartesian memberi `name` pada `xAxis` dan `yAxis` (`MISSING_AXIS_NAME`).
- Sertakan satuan di nama sumbu: "Revenue (Rp)", "Durasi (hari)", "Margin (%)".
- Legenda wajib bila ada lebih dari satu series (`MULTI_SERIES_NO_LEGEND`).
- Tooltip: `trigger: "axis"` untuk line/bar, `"item"` untuk pie/scatter/heatmap.

## 12. Kapan memakai insight teks

Insight_Card (teks berbasis bukti) cocok bila:

- Ada temuan penting yang tidak terlihat jelas dari chart (mis. anomali, kontribusi Pareto,
  perubahan tren).
- Audiens eksekutif butuh ringkasan naratif 1–3 kalimat.
- Menjelaskan konteks atau asumsi (mis. "Data Desember belum lengkap").

Jangan memakai insight teks untuk menggantikan KPI. Satu angka ringkas dengan pembanding dibuat
sebagai KPI via `add_kpi`. Insight harus menyebut angka yang bisa ditelusuri ke query sumber,
dan maksimal 1–2 insight per halaman agar tidak menjadi dinding teks.

## 13. Checklist review cepat

- [ ] Tujuan, audiens, dan periode jelas.
- [ ] Setiap pertanyaan kunci terjawab oleh minimal satu item.
- [ ] KPI di baris atas, masing-masing dengan pembanding dan arah baik.
- [ ] Ada tren waktu bila data punya kolom waktu (`NO_TIME_TREND`).
- [ ] ≤ 12 item, idealnya 7–9.
- [ ] Minimal satu chart kategori punya `cross_filter_column` bila ada ≥ 2 chart.
- [ ] Judul, label sumbu, satuan, legenda, dan format angka konsisten.
