---
topic: interaction
summary: Desain interaksi dashboard, yaitu kapan memakai Global_Filter atau Cross_Filter, memilih kolom cross_filter, reset filter, dan menjaga KPI pembanding tetap jelas saat difilter.
---

# Desain Interaksi Filter

Interaksi membuat dashboard bisa dieksplorasi tanpa menambah visual. Studio punya dua mekanisme:

- **Global_Filter**: filter tingkat dashboard (rentang tanggal, slicer kategori) yang berlaku ke
  semua item lewat tabel sumber dan dipropagasikan melalui Confirmed_Relation. Diatur dengan
  `default_filters` di Dashboard_Blueprint atau `set_global_filters`.
- **Cross_Filter**: terbentuk saat pengguna mengklik elemen chart (bar, slice, titik, sel) yang
  terikat ke kolom dimensi. Item lain ikut terfilter. Diaktifkan lewat `cross_filter_column` pada
  slot/chart.

## Kapan Global_Filter

Pakai Global_Filter untuk konteks yang berlaku ke **seluruh** dashboard dan sering diganti:

- **Rentang tanggal**: hampir selalu ada bila data punya kolom `time`. Default yang lazim:
  12 bulan terakhir (eksekutif/finance), 30–90 hari terakhir (operasional, marketing harian),
  tahun berjalan (HR, target tahunan).
- **Slicer kategori dimensi utama**: dimensi yang membagi organisasi dan dipakai hampir semua
  pertanyaan, mis. region, cabang, unit bisnis, channel, departemen, kategori produk.
  - Pilih 1–3 slicer. Lebih dari itu membuat panel filter ramai.
  - Pilih dimensi dengan jumlah nilai unik kecil-sedang (≤ 50, cocok dengan penanda enum di
    Semantic_Model).
  - Jangan jadikan slicer untuk kolom identitas (order_id, customer_id) atau kolom dengan ribuan
    nilai unik.

## Kapan Cross_Filter

Pakai Cross_Filter untuk eksplorasi "klik untuk fokus" pada chart **kategori**:

- Bar per region/kategori/channel → klik satu bar, tren dan breakdown lain ikut fokus.
- Pie komposisi → klik slice.
- Heatmap hari × jam → klik sel (kolom cross_filter salah satu dimensinya).
- Line tren biasanya **bukan** sumber cross filter; pakai Global_Filter tanggal untuk waktu.
- KPI_Card tidak bisa diklik sebagai sumber Cross_Filter, tetapi ikut terfilter.

Dashboard dengan ≥ 2 chart tanpa satu pun `cross_filter_column` memicu temuan `NO_CROSS_FILTER`.
Targetkan 1–3 chart breakdown sebagai sumber cross filter.

## Kolom cross_filter yang baik

Kolom yang baik:

- Bertipe dimensi kategori, sama dengan sumbu kategori chart tersebut (mis. chart revenue per
  `region` → `cross_filter_column: "region"`).
- Kardinalitas rendah-sedang (3–50 nilai) sehingga setiap elemen bisa diklik.
- Ada di tabel sumber atau bisa dicapai lewat Confirmed_Relation agar chart lain benar-benar
  terfilter. Bila tidak terhubung, chart lain tampil sebagai "tidak terpengaruh filter".
- Bermakna bisnis dan konsisten dengan slicer (mis. slicer `region` + cross filter `category`).

Hindari:

- Kolom hasil perhitungan/alias di SQL yang tidak ada di tabel sumber (mis. `bulan` hasil
  `DATE_TRUNC`, `step` di waterfall). Filter diterapkan pada tabel sumber.
- Kolom measure numerik (revenue, qty).
- Kolom yang sama dengan Global_Filter aktif; dua mekanisme pada kolom yang sama membingungkan.

## Reset filter

- Canvas menyediakan aksi **"Reset semua filter"** yang mengosongkan Global_Filter dan menghapus
  semua Cross_Filter dalam satu aksi.
- Klik elemen yang sama kedua kali atau hapus penanda Cross_Filter untuk melepas satu filter.
- Saat menjelaskan dashboard ke pengguna, sebutkan filter default dan cara reset-nya.
- `default_filters` sebaiknya ringan (mis. hanya rentang tanggal) agar tampilan awal mewakili
  gambaran umum.

## KPI pembanding dan filter

KPI_Card ikut terfilter seperti chart. Rancang query KPI agar filter tidak membuat pembanding
menyesatkan:

- **Rentang tanggal vs pembanding periode sebelumnya.** Bila Global_Filter tanggal memotong
  data ke bulan ini saja, kolom `prev_value` (bulan lalu) menjadi 0/kosong sehingga delta
  tampak +100 %. Pilihan:
  - Default rentang tanggal mencakup periode pembanding (mis. 12 bulan terakhir untuk KPI
    "bulan ini vs bulan lalu").
  - Atau pakai pembanding target/anggaran yang berada di baris yang sama.
  - Nyatakan di `comparison_label` apa pembandingnya ("vs bulan lalu", "vs target", "vs YoY").
- **Cross_Filter pada kategori** berlaku sama ke nilai dan pembanding (keduanya dari tabel yang
  sama), sehingga perbandingan tetap apple-to-apple. Ini perilaku yang diharapkan.
- Jangan mencampur sumber: nilai dari tabel A (terfilter) dan pembanding dari tabel B yang tidak
  terhubung relasi (tidak terfilter) menghasilkan delta palsu.
- Rasio (margin, CTR) dihitung ulang dari agregat setelah filter, bukan dirata-rata.
- Bila sebuah KPI sengaja tidak boleh berubah oleh filter (mis. target tahunan perusahaan),
  jelaskan di judul atau asumsi, dan jangan letakkan sebagai pembanding KPI yang terfilter.

## Pola rekomendasi

| Kebutuhan | Mekanisme | Contoh |
|---|---|---|
| Ganti periode analisis | Global_Filter tanggal | 12 bulan terakhir |
| Lihat satu wilayah/unit | Global_Filter slicer | region, unit bisnis |
| Eksplorasi ad-hoc dari chart | Cross_Filter | klik bar kategori produk |
| Bandingkan dua segmen berdampingan | Bukan filter; buat chart dengan dimensi segmen | bar stack per channel |
| Drill ke daftar item | Chart detail top-N + Cross_Filter dari breakdown | top 10 produk |
