# Spec: Redesign UI, HITL Lebih Ringan, Layout Tanpa Tumpang Tindih

Status: `ready-for-agent`. Dokumen ini untuk dibaca agent coding. Bagian bertanda **[SELESAI]** sudah ada di kode, jangan dikerjakan ulang. Bagian **[BELUM]** adalah pekerjaan yang tersisa.

## Problem Statement

Pengguna Dashboard Studio menghadapi tiga masalah.

1. **UI rata dan ramai.** Permukaan dark mode terlalu mirip, border ada di mana-mana, KPI tidak menonjol, panel Asisten punya area kosong besar, sidebar berisi sesi chat yang semuanya berjudul "Percakapan".
2. **Terlalu sering HITL.** Agent berhenti lalu meminta pengguna menyetujui hal yang sudah diminta secara eksplisit. Dari riwayat nyata (771 event, 15 sesi): 28 penolakan `APPROVAL_REQUIRED`, banyak giliran "lanjutkan"/"eksekusi", dan satu permintaan sering butuh dua kartu persetujuan (usulan perubahan lalu Blueprint).
3. **Layout dashboard rusak.** Item saling menimpa dan meninggalkan celah. Penyebabnya ada di data tersimpan, bukan di tampilan: backend menerima koordinat apa pun, agent menebak koordinat grid, dan penempatan otomatis selalu menaruh item di kolom 0.

## Solution

1. UI dirancang ulang dengan karakter *calm, data-first, precise*: satu aksen, elevasi lewat warna permukaan, angka `tabular-nums` berformat `id-ID`, aksi sekunder muncul saat hover/fokus, hierarki KPI → chart → insight.
2. Approval gate tetap ada, tetapi terbuka otomatis bila pesan pengguna jelas berisi perintah perubahan. Satu permintaan butuh paling banyak satu persetujuan.
3. Backend menjamin layout tersimpan tidak pernah tumpang tindih. Agent tidak lagi diminta menghitung koordinat.

## User Stories

### Tampilan dan hierarki
1. Sebagai pengguna dashboard, saya ingin KPI selalu tampil di atas chart, agar angka terpenting terbaca pertama.
2. Sebagai pengguna dashboard, saya ingin kartu insight dikelompokkan di bagian "Insight" setelah chart, agar chart tidak terputus teks naratif.
3. Sebagai pengguna, saya ingin dark dan light mode terlihat konsisten, termasuk chart, agar tidak ada elemen yang "bocor" warna.
4. Sebagai pengguna, saya ingin semua angka memakai format `id-ID` dan lebar digit tetap, agar kolom angka sejajar dan mudah dibandingkan.
5. Sebagai pengguna, saya ingin delta KPI tampil sebagai badge hijau/merah/netral, agar naik-turun terbaca sekilas.
6. Sebagai pengguna, saya ingin handle drag dan menu tile hanya muncul saat hover atau fokus keyboard, agar header tile tidak ramai.
7. Sebagai pengguna, saya ingin filter bar ringkas dan menempel di atas saat canvas di-scroll, agar filter selalu terjangkau.
8. Sebagai pengguna keyboard, saya ingin menu "Pindahkan ke atas/bawah" pada tile, agar tidak bergantung pada drag.
9. Sebagai pengguna dengan `prefers-reduced-motion`, saya ingin animasi dimatikan, agar tidak mengganggu.
10. Sebagai pengguna, saya ingin lebar sidebar dan panel Asisten bisa diatur dan diingat setelah reload, agar ruang kerja sesuai kebiasaan saya.
11. Sebagai pengguna layar sempit, saya ingin sidebar dan Asisten menjadi drawer, agar canvas tetap terbaca.
12. Sebagai pengguna, saya ingin satu tombol "Ekspor" dengan pilihan PNG dan PDF, agar topbar tidak ramai.

### Panel Asisten
13. Sebagai pengguna baru, saya ingin empty state yang menyarankan pertanyaan dari dataset saya, agar tahu cara memulai.
14. Sebagai pengguna, saya ingin pesan saya rata kanan dalam bubble dan jawaban asisten selebar penuh, agar percakapan mudah dipindai.
15. Sebagai pengguna, saya ingin melihat tool yang sedang dijalankan agent sebagai baris yang bisa dibuka, agar tahu apa yang terjadi tanpa membanjiri chat.
16. Sebagai pengguna, saya ingin kartu "Tile ditambahkan · Lihat" yang men-scroll dan menyorot tile baru, agar langsung melihat hasilnya.
17. Sebagai pengguna, saya ingin composer yang tumbuh otomatis sampai kira-kira 8 baris dengan tombol kirim/stop berbentuk ikon, agar teks tidak terpotong.
18. Sebagai pengguna, saya ingin tombol "Pesan baru" bila saya sedang membaca ke atas saat jawaban masuk, agar tidak tertarik paksa ke bawah.
19. Sebagai pengguna pembaca layar, saya ingin area chat diumumkan sopan (`aria-live="polite"`).
20. Sebagai pengguna, saya ingin tombol "Percakapan baru" di header Asisten, agar bisa mulai topik lain tanpa riwayat lama.

### Sidebar dan data
21. Sebagai pengguna, saya ingin sesi chat dikelompokkan Hari ini / Kemarin / 7 hari terakhir / Lebih lama, agar riwayat mudah dicari.
22. Sebagai pengguna, saya ingin judul sesi diambil dari pertanyaan pertama (maks 40 karakter), agar sesi bisa dibedakan.
23. Sebagai pengguna yang mengunggah dataset, saya ingin toast sukses atau gagal, agar tahu hasilnya tanpa membaca panel.
24. Sebagai pengguna, saya ingin tahu kenapa tombol "Unggah" nonaktif, agar tidak mengira rusak.
25. Sebagai analis, saya ingin melihat baris dataset (tabel dengan header menempel, sort, statistik kolom) tanpa menunggu agent, agar bisa memeriksa data sebelum bertanya. **[BELUM]**

### HITL
26. Sebagai pengguna, saya ingin perintah eksplisit ("tambahkan chart", "lanjutkan", "ya") langsung dikerjakan, agar tidak menyetujui hal yang sudah saya minta.
27. Sebagai pengguna, saya ingin pertanyaan, opini, dan pesan berisi "jangan/menurutmu/saran" tidak mengubah dashboard, agar dashboard tidak berubah tanpa niat saya.
28. Sebagai pengguna, saya ingin satu permintaan paling banyak satu kartu persetujuan, agar tidak mengklik "Setujui" dua kali untuk hal yang sama.
29. Sebagai pengguna, saya ingin agent tidak mengajukan Blueprint baru setelah saya menyetujui usulan perubahan kecil, agar pekerjaan langsung berjalan.
30. Sebagai pengguna, saya ingin persetujuan berlaku sampai tugas selesai, agar "lanjutkan" tidak minta persetujuan lagi. **[BELUM]**
31. Sebagai pemilik workspace, saya ingin opsi mode otomatis, agar agent bisa mengubah dashboard tanpa kartu (undo tetap ada). **[BELUM, butuh keputusan produk]**

### Layout
32. Sebagai pengguna, saya ingin KPI baru berjajar di baris atas, bukan bertumpuk vertikal.
33. Sebagai pengguna, saya ingin item baru atau yang dipindah tidak pernah menimpa item lain; yang bertabrakan bergeser ke bawah.
34. Sebagai pengguna, saya ingin perubahan layout otomatis tetap bisa di-undo, agar aman.
35. Sebagai pengguna, saya ingin perintah "rapikan" menutup celah tanpa tumpang tindih, dengan ukuran item tetap.
36. Sebagai agent, saya ingin menambah item tanpa menyebut koordinat, agar tidak salah menghitung grid.
37. Sebagai pengguna, saya ingin tombol "Rapikan" di UI, tidak hanya lewat agent. **[BELUM]**

### Aksesibilitas
38. Sebagai pengguna, saya ingin kontras teks minimal 4,5:1 (termasuk teks redup), agar terbaca.
39. Sebagai pengguna keyboard, saya ingin command palette `⌘K` untuk pindah halaman, tambah tile, dan bertanya ke Asisten. **[BELUM]**
40. Sebagai pengguna, saya ingin tooltip di semua tombol yang hanya berisi ikon. **[BELUM, sebagian]**

## Implementation Decisions

### Token dan tema [SELESAI]
- Nama token yang ada dipertahankan. Hanya nilai disesuaikan, dan token baru ditambah: permukaan level 3, teks subtle, latar sukses lembut, elevasi, durasi motion.
- Satu skala radius (6/8/12/penuh). Tidak ada radius ad-hoc.
- **Keputusan kontras:** nilai teks subtle pada rencana awal gagal 4,5:1. Dipakai pengganti `#82838d` (dark) dan `#6b6c75` (light). Jangan dikembalikan ke nilai awal.
- Tema ECharts membaca token CSS saat runtime dan didaftar ulang saat mode berganti. Hanya tampilan; pembentukan `option` chart tidak diubah.
- Tailwind v4 butuh pemetaan `@theme inline` eksplisit untuk setiap utilitas token baru.

### Canvas [SELESAI untuk engine `legacy`]
- Engine default adalah `legacy`. Engine `openui` punya implementasi tile sendiri dan **belum** disentuh.
- Pengelompokan KPI/chart/insight dilakukan di layer render. Data layout tersimpan tidak dimigrasi.
- Layout hanya disimpan setelah drag/resize berhenti. Jangan simpan dari callback perubahan layout grid, karena ia terpanggil saat pemadatan awal.
- Tile diberi atribut penanda id agar bisa disorot dari panel Asisten.

### Layout di backend [SELESAI]
- Invarian: layout tersimpan tidak boleh tumpang tindih.
- Penegakan dilakukan saat command di-resolve menjadi ops, **bukan** ditolak. Alasan: penolakan memaksa agent mencoba ulang (menambah giliran dan HITL) dan membuat drag pengguna gagal diam-diam.
- Aturan: item yang diminta (ditambah atau dipindah) tidak pernah bergeser. Item lain yang bertabrakan didorong ke bawah. Item yang dipindah bersamaan juga dijaga saling tidak menabrak. Hasil dikirim sebagai perubahan layout dengan `before/after`, sehingga undo/redo tetap utuh.
- Penerapan ops tetap murni dan tidak menolak overlap, supaya replay riwayat lama (yang sudah mengandung overlap) tidak pecah.
- Penempatan default memindai ke kanan lalu ke bawah (first-fit), sehingga KPI berjajar.
- Tool agent `tidy_layout` merapikan seluruh layout tanpa argumen koordinat: urutan KPI → chart → insight, ukuran dipertahankan. Tool ini mutasi, jadi lewat approval gate dan retry yang sama.
- Docstring tool tambah item dan `update_layout` menyuruh agent mengosongkan layout kecuali pengguna menyebut posisi.
- Fixture kontrak lintas bahasa harus diregenerasi dengan generator resminya setiap penempatan default berubah.

### Approval gate [SELESAI sebagian]
- Req 21.3/21.4 tetap berlaku: gate terbuka hanya lewat (a) proposal yang disetujui atau (b) permintaan eksplisit dengan evidence verbatim dari pesan giliran ini.
- Jalur baru (b) tanpa LLM: bila tool mutasi akan ditolak tetapi pesan pengguna berisi kata perintah dan tidak berisi tanda tanya atau negasi/opini, sistem mengklasifikasikan giliran itu otomatis memakai pesan sebagai evidence. Evidence tetap divalidasi lewat fungsi klasifikasi yang sama.
- Heuristik kata sengaja longgar agar "lanjut/ya/oke" cukup. Ceiling diketahui: pesan seperti "ya, tapi jelaskan dulu" membuka gate. Upgrade bila salah-buka terukur: ganti dengan klasifikasi LLM.
- Gate tetap berstatus per giliran. Properti "sisa state giliran lama tidak membuka gate baru" harus tetap benar.
- Prompt Root: satu permintaan satu persetujuan; pesan singkat berarti lanjutkan pekerjaan tertunda. Prompt Architect: usulan Blueprint maksimal sekali per giliran dan tidak diajukan setelah usulan kecil disetujui.
- **[BELUM]** Persetujuan per tugas: simpan status "disetujui" di state sesi (bukan state giliran) sampai giliran berakhir sukses tanpa error. Harus tetap lolos properti gate.
- **[BELUM]** Hentikan pemanggilan klasifikasi giliran untuk pesan non-mutasi (310 panggilan di log, sebagian besar tidak perlu).
- **[BELUM, butuh keputusan]** Mode otomatis per workspace melonggarkan Req 21.3. Jangan dibangun tanpa persetujuan eksplisit pemilik produk.

### Panel Asisten [SELESAI, kecuali yang ditandai]
- Protokol chat, endpoint, dan bentuk event SSE **tidak boleh berubah**. Perubahan hanya di sisi tampilan dan adapter.
- Library AI Elements/prompt-kit tidak dipasang. Reducer dan UI yang ada sudah cukup. Tambahkan hanya bila ada celah kompatibilitas nyata.
- Kartu "Tile ditambahkan" dideteksi dari event patch yang memuat operasi tambah item. Tombol "Lihat" mencari tile lewat penanda id di DOM. Bila tile tidak ada (canvas `openui`), tombol tidak berefek; **[BELUM]** tangani kasus ini.
- "Percakapan baru" mengosongkan sesi di parent. Panel mereset state-nya sendiri lewat aksi reducer baru dan tidak mereset saat run berjalan.
- Judul sesi diturunkan di backend dari pesan pertama saat sesi dibuat. Nilai judul saja yang berubah, bukan kontrak API.
- **[BELUM]** Dropdown sesi di header Asisten.

### Sidebar dan data
- Pengelompokan tanggal adalah fungsi murni berbasis hari kalender lokal.
- Toast memakai komponen sonner yang sudah terpasang; Toaster dipasang di layout akar.
- **[BELUM]** Dataset Preview. Backend **tidak** punya endpoint baris dataset, hanya profil kolom. Butuh endpoint baru yang menghormati `privacy_no_samples` dan Privacy_Guard (batas baris ke klien). Putuskan dulu kebijakan privasi sebelum membangun tabel TanStack + Virtual.
- **[BELUM]** Command palette butuh `cmdk` (dependency baru). Dropzone Kibo, motion, dan resizable shadcn tidak dipasang karena perilaku yang ada sudah berjalan.

## Testing Decisions

- Tes bagus hanya menguji perilaku luar: input, output, dan efek yang terlihat pengguna. Jangan menguji detail implementasi (nama fungsi internal, urutan pemanggilan).
- **Seam yang dipakai (tiga, semuanya sudah ada):**
  1. **Command → ops → content (backend, murni).** Tempat menguji invarian layout: tambah tanpa layout, tambah bertabrakan, set layout bertabrakan, tukar posisi beda ukuran, rapikan. Hasil yang dicek: tidak ada pasangan item yang overlap.
  2. **Giliran agent: awal giliran + gerbang mutasi (backend).** Tempat menguji gate: pesan perintah membuka gate, pesan tanya/negasi/opini tidak. Properti gate yang ada (Properti 26) harus tetap lolos tanpa diubah.
  3. **ChatPanel dengan klien chat palsu (frontend)** dan **Canvas dengan klien canvas palsu**. Tempat menguji kartu tile, chip saran, urutan render KPI → chart → insight, dan pemindahan keyboard.
- Fungsi murni kecil (pengelompokan tanggal, judul sesi) cukup satu tes langsung.
- Prior art: tes properti gate approval, tes `ChatPanel` dengan klien palsu, tes `Canvas`, fixture paritas `patches.json` yang dibaca juga oleh tes reducer frontend.
- Baseline terakhir: backend 369 tes lulus (unit, property, integration); frontend 256 tes lulus; tsc dan eslint bersih.
- **[BELUM]** Tes untuk "Pesan baru" dan tombol stop. jsdom tidak menghitung scroll, jadi butuh mock dimensi.
- **[BELUM]** Verifikasi di browser dan audit aksesibilitas (axe). Belum ada tooling axe di repo.
- **[BELUM]** Pengukuran HITL dengan LLM sungguhan: jalankan satu sesi nyata, lalu hitung ulang `APPROVAL_REQUIRED` dan jumlah kartu per permintaan dari riwayat sesi. Targetnya turun dibanding baseline 28 penolakan.

## Out of Scope

- Mengganti library chart (ECharts tetap) atau mengubah pembentukan `option` chart.
- Mengubah protokol/endpoint chat, skema data, atau prompt agent selain dua aturan yang disebut di atas.
- Aturan rekomendasi chart oleh AI (mis. pie dengan > 8 kategori diganti bar).
- Migrasi dashboard yang sudah ada; datanya sudah bersih dan perbaikan berlaku untuk perubahan baru.
- Layout jalur Blueprint dan canvas OpenUI.
- UI kit lain (MUI, Mantine, Chakra).
- Mode otomatis per workspace, sampai ada keputusan produk.

## Further Notes

- **Temuan investigasi:** sebagian overlap berasal dari fitur "Pindahkan ke atas/bawah" yang menukar koordinat mentah, bukan dari agent. Invarian di backend menutup kedua sumber.
- **Dugaan belum terbukti:** pola `render` lalu `patches` berulang 25 kali di log backend. Mungkin sinkronisasi layout; bila muncul tanpa drag/resize pengguna, selidiki sumber tulisan layout di frontend.
- Satu Blueprint berstatus `active` tidak pernah selesai di data lokal. Belum diselidiki.
- Perubahan prompt belum diuji dengan LLM sungguhan. Anggap efek HITL sebagai hipotesis sampai diukur.
- Konvensi repo: gunakan `bun`/`bunx`, jangan commit atau push tanpa diminta, jangan start server persisten tanpa diminta, gunakan token (bukan hex/nilai arbitrer) di className, pertahankan `data-slot` dan test id, UI berbahasa Indonesia.
- Server dev di mesin ini tidak boleh dijalankan lewat shell yang langsung selesai (proses ikut mati); jalankan lewat proses terpisah.
