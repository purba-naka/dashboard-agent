# Spec: Kinerja Agent dengan Latensi Rendah

Status: `ready-for-agent`. Untuk dibaca dan dikerjakan agent coding. Kerjakan **per fase, berurutan**. Tiap fase harus lulus syarat "Selesai jika" sebelum fase berikutnya.

## Problem Statement

Pengguna menunggu terlalu lama untuk jawaban dan perubahan dashboard. Dari riwayat nyata (58 giliran agent, Postgres lokal):

| Ukuran | Nilai |
|---|---|
| Median satu giliran | 35 detik |
| p90 | 200 detik |
| Terlama | 1051 detik (28 event, 15 panggilan tool) |
| Jeda rata-rata sebelum Chart agent memanggil tool | 25,8 detik (101 kali, total 2603 detik, penyumbang terbesar) |
| Insight agent / Query agent / Root agent | 50,7 / 11,7 / 6,3 detik per panggilan tool |
| `transfer_to_agent` | 134 panggilan, lebih banyak dari `run_sql` (69) |
| Event dengan lebih dari satu tool call (paralel) | 29 dari 354 (8%) |
| Argumen `add_chart` | rata-rata 621 karakter, maksimum 2126 |
| `classify_turn` | 310 panggilan, hampir semuanya tidak perlu |

Penyebab yang terverifikasi di kode:
1. Setiap pertanyaan lewat Root lalu `transfer_to_agent` lalu sub-agent lalu kembali ke Root. Tiap hop adalah satu panggilan LLM penuh dengan prompt dan tool berbeda.
2. Chart agent menulis opsi ECharts lengkap sebagai argumen tool. Struktur bersarang itu lambat ditulis dan rawan salah, sehingga memicu percobaan ulang (`chart_spec`, maksimum 3).
3. Prompt Chart agent **melarang** panggilan paralel ("berurutan, bukan paralel").
4. Semua agent memakai `reasoning_effort=medium` (Root, Profiler, Query, Chart). Insight tidak diatur.
5. Tidak ada metrik langkah (jumlah transfer, jumlah panggilan LLM) di harness eval, jadi tidak ada baseline.

## Solution

Enam perubahan kecil, masing-masing diukur terhadap baseline yang sama:

1. **Ukur dulu.** Tambahkan metrik langkah dan latensi ke eval live yang sudah ada. Simpan baseline.
2. **Kurangi hop.** Jalankan pemeriksaan "perlu LLM atau tidak" di kode sebelum menyentuh LLM. Hentikan `classify_turn` untuk giliran non-mutasi.
3. **Chart ringkas.** Tambahkan templat chart berparameter. Agent hanya memilih tipe dan kolom, server membangun opsi ECharts.
4. **Paralel.** Izinkan panggilan tool independen dalam satu respons.
5. **Reasoning per agent.** Turunkan untuk agent yang hanya merutekan atau menulis teks.
6. **Latensi yang terasa.** Beri umpan balik awal sebelum transfer.

## User Stories

### Pengukuran
1. Sebagai developer, saya ingin eval live mencatat jumlah transfer per giliran, agar tahu apakah routing efisien.
2. Sebagai developer, saya ingin eval live mencatat jumlah panggilan LLM per giliran, agar tahu seberapa banyak bolak-balik.
3. Sebagai developer, saya ingin durasi per agent per giliran, agar tahu bottleneck.
4. Sebagai developer, saya ingin ukuran argumen tool (karakter) tercatat, agar pengurangan output chart terbukti.
5. Sebagai developer, saya ingin baseline sebelum optimasi disimpan terpisah dan tidak tertimpa, agar semua perubahan dibandingkan dengan titik awal yang sama.
6. Sebagai developer, saya ingin skrip yang membandingkan laporan terbaru dengan baseline, agar dampak tiap fase terlihat dalam satu tabel.

### Kecepatan yang dirasakan
7. Sebagai pengguna, saya ingin pertanyaan angka sederhana dijawab tanpa hop yang tidak perlu, agar jawaban datang lebih cepat.
8. Sebagai pengguna, saya ingin chart standar (bar, line, pie, scatter) muncul dalam waktu singkat, agar tidak menunggu agent menulis JSON panjang.
9. Sebagai pengguna, saya ingin beberapa KPI dibuat sekaligus, bukan satu per satu.
10. Sebagai pengguna, saya ingin pesan pendek dari Root sebelum pekerjaan panjang dimulai, agar tidak ada jeda hening.
11. Sebagai pengguna, saya ingin pertanyaan opini ("menurutmu chart apa yang cocok") tetap dijawab Root tanpa mengubah dashboard.

### Keamanan perilaku
12. Sebagai pemilik produk, saya ingin approval gate (Req 21.3/21.4) tidak berubah oleh optimasi apa pun.
13. Sebagai pemilik produk, saya ingin chart lama yang berisi opsi ECharts penuh tetap tampil dan bisa diedit.
14. Sebagai developer, saya ingin setiap fase bisa di-rollback sendiri lewat satu perubahan konfigurasi atau satu revert.
15. Sebagai developer, saya ingin akurasi routing dan hasil data tetap diverifikasi oleh evalset yang sudah ada.

## Implementation Decisions

Semua nama di bawah dicek terhadap kode saat spec ini ditulis. Cari simbolnya (`grep`), jangan mengandalkan nomor baris.

### Fase 0: Baseline dan metrik (wajib pertama)

- Harness yang dipakai adalah eval live orkestrasi yang sudah ada (`tests/eval/test_orchestration_live.py`, marker `live`, berbiaya). Ia sudah mengukur durasi dan menulis laporan JSON ke `tests/eval/results/orchestration-latest.json`. **Jangan membuat harness baru.**
- Tambahkan ke laporan per kasus:
  - `transfer_count`: jumlah panggilan `transfer_to_agent`.
  - `llm_calls`: jumlah event model non-parsial (setara jumlah `agent.active` ditambah panggilan lanjutan; hitung dari stream SSE yang sudah dibaca harness).
  - `tool_calls`: jumlah `tool.call`.
  - `parallel_events`: jumlah event dengan lebih dari satu tool call (jika stream SSE tidak membedakannya, hitung dari riwayat event sesi ADK).
  - `args_chars`: total karakter argumen per nama tool (khususnya `add_chart`, `add_kpi`).
  - `agent_seconds`: jeda per agent (selisih timestamp antar event, dikelompokkan per `author`).
- Simpan baseline pertama sebagai berkas terpisah di folder `results/` bernama `baseline-orchestration.json`. Skrip tidak boleh menimpanya. Perubahan baseline hanya lewat penghapusan manual.
- Tambahkan skrip kecil pembanding (CLI, tanpa dependency baru) yang mencetak tabel "baseline vs terbaru" untuk metrik di atas.
- Metrik latensi **tidak boleh menjadi gate lulus/gagal** di tes. Latensi LLM nondeterministik. Catat saja. Yang boleh menjadi gate adalah invarian deterministik (mis. jumlah transfer untuk jalur yang di-bypass kode).
- Ukuran konteks: kode runner sudah mencatat `llm_usage` (token prompt dan output per agent) dengan `logger.info`. Pastikan baris itu muncul di log saat eval berjalan dan ikut dirangkum ke laporan bila tersedia.

### Fase 1: Kurangi hop dan panggilan LLM

Dua langkah. Keduanya tidak boleh mengubah gate persetujuan.

**1a. Hentikan `classify_turn` untuk giliran non-mutasi.**
- Pintu persetujuan sekarang punya jalur otomatis tanpa LLM: bila tool mutasi ditolak tetapi pesan pengguna berisi perintah eksplisit, gate dibuka lewat klasifikasi otomatis dengan evidence verbatim.
- Akibatnya panggilan `classify_turn` oleh Root tidak lagi diperlukan untuk kasus mutasi eksplisit. Ubah prompt Root agar **tidak** memanggilnya kecuali pesan ambigu. Tool tetap terdaftar (kompatibilitas dan properti gate tetap lulus).
- Tujuan terukur: `classify_turn` turun drastis dari baseline (310 panggilan).

**1b. Jawab langsung tanpa balik ke Root.**
- Untuk giliran yang murni pertanyaan data, sub-agent Query sudah menulis jawaban. Ubah prompt Query dan Profiler agar menjawab pengguna langsung dan tidak menyuruh Root merangkum ulang bila tidak ada langkah lanjutan.
- Perubahan hanya di prompt dan, bila perlu, satu aturan kecil di prompt Root: "bila sub-agent sudah menjawab pengguna, akhiri giliran".
- **Pertimbangan routing berbasis kode (opsional, hanya bila 1a dan 1b belum cukup):** jangan menambah router regex baru secara default. Regex mudah salah rute ("berapa kualitas total revenue?"). Bila diperlukan, buat sebagai **fallback yang bisa dimatikan** lewat satu env, aktifkan hanya untuk pola yang sangat jelas, dan ukur salah-rute di evalset orkestrasi sebelum diaktifkan. Router yang salah lebih mahal daripada satu hop.

### Fase 2: Templat chart berparameter

- Tujuan: mengurangi keluaran Chart agent dari ratusan token menjadi puluhan.
- Tool `add_chart` saat ini menerima `option` (ECharts tanpa data inline, divalidasi `validate_chart_spec`). **Pembentukan `option` yang sudah ada tidak boleh diubah.** Templat bersifat **tambahan**.
- Tambah **tool baru** `add_chart_from_template` (jangan mengubah tanda tangan `add_chart`), dengan parameter: `base_version`, `title`, `template` (enum: `bar`, `line`, `pie`, `scatter`), `x`, `y` (nama kolom hasil query), opsional `series` (kolom pembeda), `sort`, `query_id`, `cross_filter_column`, `slot_id`. Layout dikosongkan seperti `add_chart`.
- Server membangun `option` dari templat lalu meneruskannya ke jalur yang sama dengan `add_chart` (validasi, gerbang persetujuan, retry, versi). Dengan begitu semua invarian yang ada tetap berlaku, termasuk perilaku Chart_Spec dan lineage kolom.
- `option` hasil templat memakai konvensi yang sudah dipakai validator: series memakai `encode` dengan nama kolom, tanpa data inline. Warna dan font tidak ditulis di `option`: tema ECharts di frontend membacanya dari token.
- Validasi parameter di tool: kolom harus ada di skema query terakhir (gunakan jalur yang sama dengan `get_query_schema`). Kolom tidak ada, hasilnya error terstruktur dengan daftar kolom valid dan masuk hitungan retry `chart_spec`.
- Templat disimpan sebagai `option` biasa di Chart_Spec. Tidak ada perubahan skema database, model Chart_Spec, atau kontrak API. Chart lama tetap kompatibel.
- Chart yang tidak tercakup templat (gabungan bar+line, dua sumbu, heatmap, waterfall) tetap memakai `add_chart` dengan `option`.
- Prompt Chart: pilih templat bila chart standar, selain itu `add_chart`.
- Daftar tool agent harus diperbarui di semua tempat yang mengaturnya (tuple nama tool, set tool mutasi untuk approval gate, set tool yang memakan retry `chart_spec`, dan penugasan tool ke agent Chart serta Slot_Builder). Lewatkan salah satunya dan tool akan terlihat tapi tidak lolos gate atau retry.

### Fase 3: Paralel

- Prompt Chart saat ini berisi larangan eksplisit "berurutan (bukan paralel)". **Ganti, bukan tambah**, supaya tidak ada instruksi yang bertentangan.
- Aturan baru: beberapa `add_kpi` atau `add_chart` yang independen boleh dipanggil dalam satu respons. Pertahankan aturan "jangan panggil ulang untuk chart yang sama" dan penanganan `VERSION_CONFLICT`.
- **Risiko yang harus ditangani sebelum mengaktifkan:** semua tool mutasi memakai `base_version` yang sama. Dua mutasi paralel dengan versi dasar sama akan membuat yang kedua kena `VERSION_CONFLICT`. Pilih salah satu:
  - (a) Jalankan paralel hanya untuk langkah **non-mutasi** (mis. beberapa `run_sql` atau `get_query_schema`), atau
  - (b) Serialkan mutasi di sisi server (kunci per dashboard) dan buat versi dasar diperbarui otomatis untuk mutasi yang tidak saling bertentangan.
  Kerjakan (a) lebih dulu karena aman. (b) hanya bila terukur perlu.
- Blueprint: pembangun slot adalah loop kode berurutan dan tiap slot memanggil LLM Slot_Builder. Paralelisasi slot membutuhkan perubahan di agent pembangun (bukan prompt). Anggap **di luar fase ini** kecuali hasil Fase 0 menunjukkan slot adalah bottleneck.

### Fase 4: `reasoning_effort` per agent

- Mekanisme sudah ada: `MODEL_<AGENT>_REASONING` dan `MODEL_REASONING_DEFAULT` di environment. **Tidak ada perubahan kode.**
- **Hasil pengukuran (2026-10-29): DITOLAK.** Percobaan `low` untuk Root/Profiler/Insight memberi 4/7 kasus eval; `medium` memberi 5/7; selisihnya di dalam varians model, dan tidak ada bukti `low` aman. Semua agent dikembalikan ke `medium`. Jangan mengulang percobaan ini tanpa 3 ulangan per kondisi.
- Agent Architect dan Slot_Builder: ikuti nilai saat ini.
- Ubah lewat `.env` lokal dan dokumentasikan di `.env.example`. Jangan hardcode.
- Terima perubahan hanya bila evalset orkestrasi (routing, trajectory, grounding, approval gate) tidak turun.
- Catatan: nilai yang valid bergantung provider. Endpoint saat ini memakai model DeepSeek lewat LiteLLM. Bila provider mengabaikan `low`, tidak ada efek. Itu tercermin di metrik, bukan error.

### Fase 5: Latensi yang terasa

- Frontend sudah menampilkan `text.delta` streaming dan baris tool aktif. Tambahkan di prompt Root satu kalimat pembuka singkat sebelum transfer ke sub-agent untuk pekerjaan panjang ("Saya hitung dulu"). Jangan lakukan untuk pertanyaan sederhana.
- Tidak ada perubahan protokol SSE.

### Caching konteks (diselidiki, bukan dikerjakan di spec ini)
- ADK `ContextCacheConfig` hanya didokumentasikan untuk model Gemini. Model aktif adalah DeepSeek lewat LiteLLM, jadi manfaatnya **belum terbukti**. ADK sendiri memberi peringatan saat startup bahwa tidak ada `context_cache_config`.
- Tugas: satu eksperimen kecil, bukan fitur. Cek apakah endpoint mendukung prefix caching otomatis lewat token cache pada `usage`. Hanya bila terbukti, buat PRD terpisah.

## Testing Decisions

- **Tes yang baik** menguji perilaku luar (jumlah langkah, hasil data, ada tidaknya mutasi), bukan detail implementasi. Jangan menguji urutan pemanggilan internal.
- **Seam yang dipakai (sudah ada, tidak ada seam baru):**
  1. **Eval live orkestrasi** (`tests/eval/test_orchestration_live.py`, `-m live`): rute, trajectory, approval gate, grounding. Tempat metrik baru. Berbiaya, jalankan sengaja, bukan di CI default.
  2. **Unit/integrasi tool dengan model skrip** (`tests/unit/test_dashboard_turn_tools.py`, `tests/unit/test_agent_tools.py`, dan `tests/eval/test_eval.py` dengan model mock): tempat menguji tool templat tanpa LLM.
  3. **Properti approval gate** (`tests/property/test_approval_gate.py`): wajib tetap lulus tanpa perubahan.
- Fungsi pembentuk `option` dari templat adalah fungsi murni: tes langsung dengan beberapa kasus per templat (kolom ada, kolom tidak ada, series pembeda, sort). Pastikan hasilnya lolos `validate_chart_spec`.
- Tes kompatibilitas: Chart_Spec lama (`option` penuh) tetap valid dan tetap bisa `update_chart`.
- Tes tool baru: terdaftar di semua tempat (tuple nama, set mutasi, set retry), ditolak `APPROVAL_REQUIRED` tanpa persetujuan, dan menghitung retry saat kolom salah.
- Metrik latensi: dicatat dan dibandingkan manual. Bukan gate.
- Baseline kondisi sebelum perubahan: backend 369 tes lulus (unit, property, integration); frontend 269 tes lulus.
- Prior art: `tests/unit/test_layout_invariant.py` dan `tests/unit/test_auto_classify.py` (tes murni kecil), `tests/property/test_approval_gate.py`.

## Out of Scope

- Router berbasis embedding atau ML.
- Mengganti model atau provider, atau matriks model per agent (rencana terpisah di `docs/plan-e2e-agent-scorecard.md`).
- Paralelisasi slot Blueprint di level agen pembangun.
- Perubahan protokol chat/SSE, skema database, kontrak API, atau gerbang persetujuan.
- Mengubah pembentukan `option` ECharts yang sudah ada atau tema frontend.
- Caching konteks (hanya eksperimen penyelidikan).
- Dashboard visual untuk metrik, integrasi CI untuk perbandingan performa.
- Mode otomatis per workspace (butuh keputusan produk, melonggarkan Req 21.3).

## Further Notes

- **Urutan wajib:** Fase 0, lalu 1, lalu 2, lalu 3(a), lalu 4, lalu 5. Setelah tiap fase jalankan eval live dan bandingkan dengan baseline. Hentikan dan laporkan bila metrik memburuk atau evalset turun.
- **Kondisi selesai per fase:**
  - Fase 0: laporan eval memuat metrik baru, baseline tersimpan, skrip pembanding mencetak tabel.
  - Fase 1: `classify_turn` turun jauh dari baseline; `transfer_count` untuk pertanyaan data tidak bertambah; evalset routing/grounding/gate tidak turun.
  - Fase 2: tool templat terdaftar di semua tempat, tes murni lulus, ukuran argumen chart turun terukur; chart lama tidak berubah.
  - Fase 3: tidak ada kenaikan `VERSION_CONFLICT`; proporsi event paralel naik; larangan lama di prompt sudah dihapus.
  - Fase 4: nilai env terdokumentasi; evalset tidak turun.
  - Fase 5: tidak ada perubahan protokol; tes frontend tetap lulus.
- Semua angka target di PRD awal (mis. "turun jadi <10 detik") adalah **hipotesis**. Angka nyata hanya dari eval live di mesin ini.
- Konvensi repo: `bun`/`bunx` untuk JS, python di `backend/.venv/Scripts/python.exe`, jangan commit atau push tanpa diminta, jangan menyalakan server persisten tanpa diminta. Server di mesin ini harus dijalankan lewat proses terpisah. Tool `read_file` kadang mengembalikan output keliru, gunakan terminal untuk verifikasi.
- Data latensi berasal dari riwayat lokal yang mencakup giliran gagal dan sesi uji, jadi median bisa bias. Jeda per event mencakup waktu tool, bukan hanya LLM.
- Eval live memanggil model sungguhan dan berbiaya. Jangan menjalankannya tanpa persetujuan pengguna.


## Catatan hasil diagnosis (2026-10-29)

Eval live orkestrasi: **7/7 lulus, semua skor 1.0**, dua run berturut-turut (17.4 s dan 16.1 s rata-rata).
Baseline baru disimpan di `tests/eval/results/baseline-orchestration.json`.

Tiga bug nyata ditemukan dan diperbaiki (bukan soal prompt; prompt sudah melarang, model tetap melanggar):

1. **Pengembalian kendali diserahkan ke LLM.** Setelah `run_sql`, Query harus "transfer kembali"
   ke Chart, tetapi target transfer dipilih model dan model tidak tahu pemanggilnya. Akibatnya
   giliran berakhir di Dashboard_Architect (kartu `propose_changes`) atau buntu. Perbaikan:
   Query menjadi tool inline (`mode="single_turn"`) milik Chart **dan** Insight, dengan
   `disallow_transfer_to_peers` pada pemakainya. Kendali kembali tanpa keputusan LLM.
2. **Giliran saran bisa mentransfer ke pelaksana mutasi.** Perbaikan: `make_root_transfer_guard`
   (before_tool_callback Root) menolak transfer ke Chart/Insight/Builder saat giliran saran.
3. **`NO_QUERY` membuat giliran buntu.** Perbaikan: `make_no_query_recovery` mengganti hasil tool
   dengan langkah lanjut eksplisit, sekali per giliran.

Bug keempat (permintaan satu chart dikirim ke Dashboard_Architect sehingga muncul kartu usulan
alih-alih chart) diperbaiki `make_root_route_guard`: transfer ke Architect ditolak bila pesan
meminta SATU chart/KPI/insight tanpa kata perencanaan ("rancang/usulkan/lengkapi/beberapa").

Evaluasi usang yang ikut diperbarui: entri trajektori `add_chart` menjadi
`add_chart|add_chart_from_template` (dua tool setara; sintaks alternatif di `in_order`, dengan
kasus negatif di self-check agar tidak melonggarkan penilaian).

Catatan penting: guard "blokir transfer Root→Query untuk giliran chart" **dicoba lalu dibatalkan**
karena rute Root→Query→Chart memang sah (dipakai tes berskrip dan evalset). Yang salah bukan
rutenya, melainkan ketiadaan pengembalian kendali.
