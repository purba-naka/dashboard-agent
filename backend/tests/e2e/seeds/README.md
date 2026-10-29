# Seed retail untuk suite e2e

Dataset ini dibangkitkan secara deterministik oleh `generate.py`. Seed-nya
tetap, sehingga output byte-identik di setiap run. Suite e2e
(`e2e/`, lihat `docs/plan-e2e-agent-scorecard.md`) memakainya untuk menguji
JOIN multi-hop, filter status, dan insight.

```powershell
backend\.venv\Scripts\python.exe backend\tests\e2e\seeds\generate.py
```

| File | Tabel | Isi |
| --- | --- | --- |
| `wilayah.csv` | `wilayah` | 5 wilayah: `kode_wilayah`, `nama_wilayah` |
| `toko.csv` | `toko` | 25 toko: `kode`, `nama_toko`, `kota` (2 null), `kode_wilayah`, `tanggal_buka` |
| `produk.xlsx` (sheet `Produk`) | `produk` | 40 produk: `id_produk`, `nama_produk`, `kategori`, `harga_satuan` (IDR) |
| `pesanan.csv` | `pesanan` | 3.000 pesanan 2024: `no_pesanan`, `tanggal`, `kode_toko`, `status`, `metode_bayar` |
| `detail_pesanan.csv` | `detail_pesanan` | ±7.500 baris: `no_pesanan`, `id_produk`, `jumlah`, `pendapatan` (IDR) |
| `expected_facts.json` | – | Ground truth dan `reference_sql`, dihitung ulang dari file di disk |

Relasi (semua `many_to_one`, overlap 100%) membentuk rantai 3 hop:

- `detail_pesanan.no_pesanan` ke `pesanan.no_pesanan`
- `detail_pesanan.id_produk` ke `produk.id_produk`
- `pesanan.kode_toko` ke `toko.kode_toko`
- `toko.kode_wilayah` ke `wilayah.kode_wilayah`

Data ini sengaja memuat jebakan berikut:

- Status `batal` sekitar 8% dan `retur` sekitar 3%. Pendapatan yang benar
  hanya menghitung `selesai`. Ground truth menyimpan kedua versi.
- `no_pesanan` berupa integer berurutan, sehingga tidak boleh dijumlahkan.
- Sekitar 5% `tanggal` memakai format `dd/mm/yyyy`, sisanya ISO. Ingestion
  menyimpan kolom ini sebagai `string`, jadi agent harus mem-parse tanggal
  sendiri, misalnya dengan `strptime` untuk dua format.
- `toko.kota` bernilai null untuk 2 toko.
- Satu `nama_produk` berisi teks prompt injection.

Pola bisnis yang ditanam:

- Wilayah Jawa menyumbang ±47% pendapatan.
- Desember 2024 melonjak ±1,7x rata-rata bulan lain.
- Pesanan `TK-017` turun ±8% per bulan dan tidak ikut lonjakan Desember.
- `Elektronik` mendominasi pendapatan, sedangkan `Makanan` mendominasi unit.

Hasil probe ingestion (plan B1): keempat relasi terdeteksi sebagai kandidat
dengan overlap 100%. Detektor (`studio/core/relations.py:names_match`) hanya
mencocokkan nama kolom yang sama, atau pola `{tabel}_id` ke `id`. Kunci dengan
nama berbeda, misalnya `toko.kode`, tidak terdeteksi, dan API belum punya cara
membuat relasi manual. Karena itu kunci toko memakai nama `kode_toko` di kedua
tabel. Jalankan probe ulang dengan:

```powershell
cd backend; .venv\Scripts\python.exe -m tests.e2e.seeds.probe_ingestion
```

Angka pastinya ada di `expected_facts.json`. Jangan edit file data secara
manual. Ubah generator, lalu jalankan ulang. `test_seeds.py` memeriksa bahwa
fakta, SQL rujukan, dan pola tetap konsisten.
