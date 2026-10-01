# Requirements Document

## Introduction

Dashboard Studio Agent adalah aplikasi studio lokal tempat pengguna membuat dan menghasilkan dashboard berisi chart kustom dan insight dari file CSV atau XLSX yang mereka unggah. Pengguna mendesain dashboard secara hybrid: melalui percakapan dengan agent (dibangun dengan Google ADK Python) yang membuat dan mengubah dashboard, serta melalui canvas editor tempat pengguna dapat men-drag, me-resize, mengganti tipe chart, dan menghapus elemen secara manual. Agent selalu mengetahui perubahan manual tersebut.

Satu workspace dapat menampung beberapa dataset. Agent mendeteksi kandidat relasi antar-dataset, pengguna mengonfirmasi relasi tersebut, dan relasi yang terkonfirmasi disimpan di semantic layer workspace untuk digunakan dalam JOIN, sehingga chart dan insight dapat menggabungkan beberapa dataset.

Data besar (>100 MB) didukung dengan mengonversi file ke Parquet saat upload dan melakukan query secara lazy menggunakan Polars. Agent menghasilkan SQL yang dieksekusi melalui `pl.SQLContext` (tanpa eksekusi kode arbitrer). Setiap insight berbasis bukti: terhubung ke query SQL dan hasil numerik dari eksekusi.

Tech stack: frontend Next.js (React + TypeScript) dengan echarts-for-react dan react-grid-layout (package manager bun); backend Python FastAPI yang meng-host agent ADK, streaming respons melalui SSE, dijalankan dengan `python main.py`. Model diakses melalui LiteLLM (wrapper `google.adk.models.lite_llm.LiteLlm`) dan dikonfigurasi per agent melalui `.env`. Semua penyimpanan bersifat lokal (SQLite + folder lokal), single-user, tanpa autentikasi.

Di luar cakupan saat ini: integrasi Google Sheets, cloud storage/deploy, multi-user/login, share link, dan BigQuery.

## Glossary

- **Studio**: Keseluruhan aplikasi Dashboard Studio Agent (frontend dan backend).
- **Frontend**: Aplikasi Next.js (React + TypeScript) yang menampilkan Chat_Panel, Canvas_Editor, dan panel dataset.
- **Backend_API**: Server Python FastAPI yang meng-host agent ADK, menyediakan REST API dan endpoint SSE, serta menjadi sumber kebenaran tunggal untuk state dashboard.
- **Chat_Panel**: Komponen Frontend untuk percakapan antara pengguna dan Root_Agent.
- **Canvas_Editor**: Komponen Frontend berbasis grid (react-grid-layout) tempat chart dan Insight_Card ditampilkan dan diedit secara manual.
- **Root_Agent**: Agent orkestrator ADK (Studio agent) yang menangani chat, mengenali intent pengguna, dan mendelegasikan tugas ke sub-agent.
- **Data_Profiler_Agent**: Sub-agent ADK yang menganalisis skema, tipe data, kualitas data, dan kandidat relasi antar-dataset.
- **Query_Agent**: Sub-agent ADK yang menghasilkan SQL dan mengeksekusinya melalui tool.
- **Chart_Designer_Agent**: Sub-agent ADK yang memilih tipe chart dan menghasilkan Chart_Spec.
- **Insight_Agent**: Sub-agent ADK yang menghasilkan Insight_Card dari hasil query.
- **Agent_Tools**: Kumpulan tool ADK yang dipanggil agent untuk membaca data dan mengubah dashboard (misalnya `run_sql`, `add_chart`, `update_chart`, `remove_chart`, `add_insight`, `update_layout`).
- **Model_Gateway**: Lapisan konfigurasi model berbasis LiteLLM melalui wrapper `LiteLlm` ADK.
- **Workspace**: Unit kerja yang menampung beberapa Dataset, Semantic_Layer, satu atau lebih Dashboard, dan sesi chat.
- **Dataset**: Satu file CSV atau XLSX (atau satu sheet XLSX) yang telah diunggah dan dikonversi ke Parquet, terdaftar sebagai satu tabel di Workspace.
- **Ingestion_Service**: Komponen Backend_API yang menerima upload, membaca CSV/XLSX, dan mengonversinya ke Parquet.
- **Data_Engine**: Komponen Backend_API berbasis Polars yang membaca Parquet secara lazy (`scan_parquet`) dan mengeksekusi SQL melalui `pl.SQLContext`.
- **SQL_Validator**: Komponen Backend_API yang memvalidasi SQL sebelum dieksekusi (read-only SELECT, pembentukan LazyFrame, `collect_schema`).
- **Column_Profile**: Statistik per kolom (tipe data, jumlah null, jumlah nilai unik, min, max, mean, nilai terbanyak) hasil profiling.
- **Sample_Rows**: Sejumlah baris contoh dari Dataset (default 5) yang dapat dikirim ke LLM.
- **Relation_Candidate**: Usulan relasi antar-kolom dari dua Dataset (misalnya `customer_id`) yang dideteksi Data_Profiler_Agent dan belum dikonfirmasi pengguna.
- **Confirmed_Relation**: Relation_Candidate yang telah dikonfirmasi pengguna.
- **Semantic_Layer**: Penyimpanan per Workspace berisi Confirmed_Relation dan metadata dataset yang digunakan untuk JOIN dan propagasi filter.
- **Dashboard**: Kumpulan chart, Insight_Card, layout, dan Global_Filter dalam satu Workspace.
- **Dashboard_Store**: Komponen Backend_API yang menyimpan state Dashboard beserta riwayat versinya di SQLite.
- **Dashboard_Version**: Nomor versi bilangan bulat Dashboard yang bertambah satu pada setiap perubahan.
- **Patch_Event**: Event perubahan Dashboard (tambah/ubah/hapus chart, insight, layout, filter) yang diterapkan oleh Dashboard_Store dan disiarkan melalui SSE.
- **User_Edit_Event**: Patch_Event yang berasal dari aksi manual pengguna di Canvas_Editor.
- **Chart_Spec**: Objek JSON opsi Apache ECharts yang dihasilkan Chart_Designer_Agent dan mereferensikan hasil query (data diikat oleh Backend_API, tidak disisipkan oleh LLM).
- **Chart_Spec_Validator**: Komponen Backend_API yang memvalidasi Chart_Spec sebelum disimpan.
- **Insight_Card**: Kartu insight berbasis bukti yang berisi teks insight, tipe insight, SQL sumber, hasil numerik, dan filter yang aktif saat dihitung.
- **Global_Filter**: Filter tingkat Dashboard (misalnya rentang tanggal atau filter kategorikal) yang berlaku pada tabel sumber.
- **Cross_Filter**: Filter yang terbentuk ketika pengguna mengklik elemen chart sehingga chart lain terfilter.
- **Filter_Engine**: Komponen Backend_API yang menerapkan Global_Filter dan Cross_Filter pada tingkat tabel sumber dan mempropagasikannya melalui Confirmed_Relation.
- **Export_Service**: Komponen Studio yang mengekspor Dashboard ke PNG dan PDF.
- **Privacy_Guard**: Komponen Backend_API yang membatasi data yang dikirim ke LLM.
- **Metadata_Store**: Database SQLite satu file yang menyimpan metadata Workspace, Dataset, relasi, Dashboard, layout, Chart_Spec, Insight_Card, dan versi.
- **Session_Service**: `DatabaseSessionService` ADK yang menyimpan riwayat chat di SQLite.
- **Semantic_Model**: Bagian Semantic_Layer yang menyimpan arti bisnis data Workspace: Semantic_Column, Business_Metric, Glossary_Term, Workspace_Instruction, dan Verified_Query.
- **Semantic_Column**: Metadata bisnis satu kolom Dataset: label, deskripsi, sinonim, agregasi default, format angka, dan penanda enum.
- **Business_Metric**: Metrik bisnis bernama (misalnya revenue, gross margin) yang didefinisikan sebagai ekspresi agregat SQL beserta sinonim, format, dan arah nilai yang baik.
- **Glossary_Term**: Istilah bisnis, singkatan, atau jargon beserta definisi dan sinonimnya.
- **Workspace_Instruction**: Aturan bisnis singkat berbentuk teks yang wajib diikuti agent (misalnya "status cancelled tidak dihitung sebagai penjualan").
- **Verified_Query**: Pasangan pertanyaan bahasa alami dan SQL yang telah diverifikasi, dipakai agent sebagai contoh untuk pertanyaan serupa.
- **Semantic_Entry**: Satu entri Semantic_Model (Semantic_Column, Business_Metric, Glossary_Term, Workspace_Instruction, atau Verified_Query) dengan status `candidate`, `confirmed`, atau `rejected`.
- **Semantic_Drafter**: Komponen Backend_API yang otomatis menyusun draft Semantic_Model setelah profiling, gabungan heuristik deterministik dan pengayaan LLM.
- **Dashboard_Architect_Agent**: Sub-agent ADK yang merancang, mengarahkan, dan mereview Dashboard berdasarkan pengetahuan BI; tidak menulis SQL maupun Chart_Spec.
- **BI_Knowledge_Pack**: Kumpulan file Markdown lokal berisi prinsip desain dashboard, katalog KPI per domain, playbook domain, desain interaksi, dan panduan pemilihan chart.
- **Design_Brief**: Maksud desain Dashboard: tujuan, audiens, pertanyaan bisnis kunci, KPI beserta pembandingnya, bagian, dan asumsi.
- **Dashboard_Blueprint**: Rancangan Dashboard terstruktur berisi Design_Brief, daftar Blueprint_Slot, dan Global_Filter bawaan yang diusulkan Dashboard_Architect_Agent.
- **Blueprint_Slot**: Satu rencana elemen Dashboard pada Dashboard_Blueprint: bagian, tujuan, tipe visual, metrik, dimensi, dan posisi layout.
- **Layout_Template**: Fungsi penempatan otomatis Blueprint_Slot pada grid 12 kolom berdasarkan perannya.
- **KPI_Card**: Item Dashboard yang menampilkan satu angka utama beserta pembanding dan perubahan (delta).
- **Design_Rules**: Aturan deterministik yang memeriksa Dashboard terhadap prinsip desain BI dan menghasilkan temuan review.

## Requirements

### Requirement 1: Manajemen Workspace

**User Story:** Sebagai pengguna, saya ingin membuat dan mengelola workspace, sehingga saya dapat mengelompokkan beberapa dataset dan dashboard terkait dalam satu tempat kerja.

#### Acceptance Criteria

1. WHEN pengguna membuat Workspace baru dengan nama yang diberikan, THE Backend_API SHALL menyimpan Workspace tersebut di Metadata_Store dengan identifier unik, nama, waktu pembuatan, dan `owner_id`.
2. WHEN pengguna membuka Workspace, THE Frontend SHALL menampilkan daftar Dataset, Dashboard, dan riwayat chat milik Workspace tersebut.
3. WHEN pengguna mengganti nama Workspace, THE Backend_API SHALL menyimpan nama baru dan mengembalikan Workspace yang telah diperbarui.
4. WHEN pengguna menghapus Workspace dan mengonfirmasi penghapusan, THE Backend_API SHALL menghapus metadata Workspace, file Parquet, file upload, dan sesi chat milik Workspace tersebut.
5. THE Backend_API SHALL mengizinkan satu Workspace menampung lebih dari satu Dataset.

### Requirement 2: Upload dan Parsing File CSV

**User Story:** Sebagai pengguna, saya ingin mengunggah file CSV, sehingga datanya dapat dianalisis di workspace.

#### Acceptance Criteria

1. WHEN pengguna mengunggah file berekstensi `.csv` ke Workspace, THE Ingestion_Service SHALL menyimpan file asli di folder `data/uploads/{workspace_id}/` dan membaca file tersebut menggunakan Polars.
2. WHEN file CSV berhasil dibaca, THE Ingestion_Service SHALL menyimpulkan tipe data setiap kolom (integer, float, string, boolean, date, datetime).
3. IF file CSV tidak dapat di-parse (encoding tidak didukung, jumlah kolom tidak konsisten, atau file kosong), THEN THE Ingestion_Service SHALL menolak upload dan mengembalikan pesan error yang menyebutkan penyebab dan nomor baris pertama yang bermasalah bila tersedia.
4. IF pengguna mengunggah file dengan ekstensi selain `.csv` atau `.xlsx`, THEN THE Ingestion_Service SHALL menolak upload dan mengembalikan pesan error yang menyebutkan format yang didukung.
5. WHEN file CSV memiliki nama kolom duplikat atau kosong, THE Ingestion_Service SHALL menormalisasi nama kolom menjadi unik dan tidak kosong, lalu melaporkan pemetaan nama kolom asli ke nama kolom hasil normalisasi.

### Requirement 3: Upload dan Parsing File XLSX

**User Story:** Sebagai pengguna, saya ingin mengunggah file Excel (XLSX), sehingga saya dapat menganalisis data spreadsheet tanpa mengonversinya secara manual.

#### Acceptance Criteria

1. WHEN pengguna mengunggah file berekstensi `.xlsx`, THE Ingestion_Service SHALL membaca file tersebut menggunakan `polars.read_excel` dengan engine calamine (fastexcel).
2. WHEN file XLSX berisi lebih dari satu sheet, THE Ingestion_Service SHALL menampilkan daftar sheet dan mendaftarkan setiap sheet yang dipilih pengguna sebagai Dataset terpisah.
3. IF sheet XLSX yang dipilih tidak berisi baris data, THEN THE Ingestion_Service SHALL menolak sheet tersebut dan mengembalikan pesan error yang menyebutkan nama sheet.
4. IF file XLSX rusak atau terenkripsi, THEN THE Ingestion_Service SHALL menolak upload dan mengembalikan pesan error yang menyebutkan bahwa file tidak dapat dibaca.

### Requirement 4: Konversi ke Parquet

**User Story:** Sebagai pengguna, saya ingin data saya disimpan dalam format yang efisien, sehingga query tetap cepat untuk file besar.

#### Acceptance Criteria

1. WHEN file CSV atau sheet XLSX berhasil di-parse, THE Ingestion_Service SHALL mengonversi data tersebut ke file Parquet di folder `data/uploads/{workspace_id}/` dan mendaftarkannya sebagai Dataset di Metadata_Store.
2. THE Ingestion_Service SHALL menyimpan skema Dataset (nama kolom, tipe data) dan jumlah baris di Metadata_Store.
3. FOR ALL tabel data valid yang dapat diwakili dalam CSV, menulis tabel ke CSV lalu mem-parse CSV tersebut lalu mengonversinya ke Parquet lalu membaca Parquet tersebut SHALL menghasilkan tabel dengan nama kolom, tipe data, jumlah baris, dan nilai sel yang ekuivalen dengan tabel asli (round-trip property).
4. FOR ALL tabel data valid, mengonversi tabel ke Parquet lalu membacanya kembali SHALL menghasilkan tabel yang identik dengan tabel sebelum konversi (round-trip property).

### Requirement 5: Dukungan Data Besar

**User Story:** Sebagai pengguna, saya ingin mengunggah file berukuran lebih dari 100 MB, sehingga saya dapat menganalisis dataset besar.

#### Acceptance Criteria

1. THE Ingestion_Service SHALL menerima file upload berukuran hingga minimal 1 GB.
2. WHEN file berukuran lebih dari 100 MB diunggah, THE Ingestion_Service SHALL memproses file secara streaming atau per batch ke Parquet sehingga penggunaan memori puncak proses Backend_API tetap di bawah 2 kali ukuran file.
3. WHILE proses upload dan konversi berlangsung, THE Frontend SHALL menampilkan indikator progres dalam persentase yang diperbarui minimal setiap 2 detik.
4. THE Data_Engine SHALL membaca Dataset menggunakan `scan_parquet` (lazy) dan hanya melakukan materialisasi hasil akhir query.
5. IF upload terputus atau konversi gagal, THEN THE Ingestion_Service SHALL menghapus file parsial dan melaporkan kegagalan tanpa mendaftarkan Dataset.

### Requirement 6: Profiling Data

**User Story:** Sebagai pengguna, saya ingin agent memahami struktur dan kualitas data saya secara otomatis, sehingga saya dapat segera melihat gambaran dataset.

#### Acceptance Criteria

1. WHEN Dataset baru berhasil didaftarkan, THE Data_Profiler_Agent SHALL menjalankan profiling otomatis dan menghasilkan Column_Profile untuk setiap kolom.
2. THE Data_Profiler_Agent SHALL melaporkan masalah kualitas data yang terdeteksi, meliputi persentase nilai null per kolom, baris duplikat, dan kolom dengan tipe campuran.
3. THE Data_Profiler_Agent SHALL mengklasifikasikan setiap kolom sebagai salah satu peran: dimensi, ukuran (measure), waktu, atau identifier.
4. WHEN profiling selesai, THE Backend_API SHALL menyimpan hasil profiling di Metadata_Store dan THE Root_Agent SHALL menampilkan ringkasan profil di Chat_Panel.
5. THE Data_Engine SHALL menghitung statistik Column_Profile secara deterministik dari data, dan THE Data_Profiler_Agent SHALL menggunakan statistik hasil perhitungan Data_Engine tersebut tanpa mengarang nilai statistik.

### Requirement 7: Deteksi dan Konfirmasi Relasi Antar-Dataset

**User Story:** Sebagai pengguna, saya ingin agent mendeteksi hubungan antar-dataset di workspace, sehingga saya dapat menggabungkan insight dari beberapa dataset.

#### Acceptance Criteria

1. WHEN Workspace memiliki dua atau lebih Dataset dan profiling Dataset baru selesai, THE Data_Profiler_Agent SHALL mengusulkan Relation_Candidate berdasarkan kecocokan nama kolom, kompatibilitas tipe data, dan persentase overlap nilai.
2. THE Data_Profiler_Agent SHALL menyertakan pada setiap Relation_Candidate: tabel dan kolom sumber, tabel dan kolom tujuan, kardinalitas yang diperkirakan (one-to-one, one-to-many, many-to-many), dan persentase overlap nilai yang dihitung oleh Data_Engine.
3. WHEN Relation_Candidate diusulkan, THE Chat_Panel SHALL menampilkan kandidat tersebut dengan aksi konfirmasi dan penolakan.
4. WHEN pengguna mengonfirmasi Relation_Candidate, THE Backend_API SHALL menyimpan relasi tersebut sebagai Confirmed_Relation di Semantic_Layer Workspace.
5. WHEN pengguna menolak Relation_Candidate, THE Backend_API SHALL menandai kandidat tersebut sebagai ditolak sehingga Data_Profiler_Agent tidak mengusulkan kandidat yang sama untuk pasangan kolom tersebut lagi.
6. THE Query_Agent SHALL hanya menggunakan Confirmed_Relation sebagai kondisi JOIN antar-Dataset.
7. IF SQL yang dihasilkan Query_Agent berisi JOIN antar-Dataset dengan kondisi yang tidak terdapat di Confirmed_Relation, THEN THE SQL_Validator SHALL menolak SQL tersebut dan mengembalikan error yang menyebutkan pasangan kolom yang belum dikonfirmasi.
8. WHEN pengguna menghapus Confirmed_Relation, THE Backend_API SHALL menandai chart dan Insight_Card yang bergantung pada relasi tersebut sebagai tidak valid dan menampilkan status tersebut di Canvas_Editor.

### Requirement 8: Arsitektur Multi-Agent

**User Story:** Sebagai pengguna, saya ingin berinteraksi dengan satu agent studio yang mendelegasikan tugas ke agent spesialis, sehingga setiap tugas ditangani oleh agent yang tepat.

#### Acceptance Criteria

1. THE Backend_API SHALL mengimplementasikan Root_Agent, Data_Profiler_Agent, Query_Agent, Chart_Designer_Agent, dan Insight_Agent menggunakan Google ADK Python, dengan Root_Agent sebagai agent induk dari keempat sub-agent.
2. WHEN pengguna mengirim pesan di Chat_Panel, THE Root_Agent SHALL mengklasifikasikan intent pesan (profiling, query/analisis, desain chart, insight, modifikasi dashboard, atau pertanyaan umum) dan mendelegasikan tugas ke sub-agent yang sesuai.
3. THE Root_Agent SHALL mengubah Dashboard hanya melalui Agent_Tools.
4. THE Backend_API SHALL mengeksekusi hanya tool yang terdaftar di Agent_Tools, tanpa eksekusi kode arbitrer yang dihasilkan LLM.
5. IF sub-agent gagal menyelesaikan tugas, THEN THE Root_Agent SHALL menyampaikan penyebab kegagalan kepada pengguna di Chat_Panel.

### Requirement 9: Konfigurasi Model melalui LiteLLM

**User Story:** Sebagai pengguna, saya ingin memilih model LLM untuk setiap agent melalui file konfigurasi, sehingga saya dapat menyeimbangkan biaya dan kualitas.

#### Acceptance Criteria

1. THE Model_Gateway SHALL membuat model untuk setiap agent menggunakan wrapper `google.adk.models.lite_llm.LiteLlm`.
2. THE Model_Gateway SHALL membaca nama model dari variabel `.env` berikut: `MODEL_ROOT` untuk Root_Agent, `MODEL_PROFILER` untuk Data_Profiler_Agent, `MODEL_QUERY` untuk Query_Agent, `MODEL_CHART` untuk Chart_Designer_Agent, dan `MODEL_INSIGHT` untuk Insight_Agent.
3. IF variabel model untuk suatu agent tidak diset, THEN THE Model_Gateway SHALL menggunakan nilai `MODEL_DEFAULT` untuk agent tersebut.
4. IF variabel model untuk suatu agent dan `MODEL_DEFAULT` sama-sama tidak diset, THEN THE Backend_API SHALL menghentikan proses startup dan menampilkan pesan error yang menyebutkan nama variabel yang hilang.
5. IF model yang dikonfigurasi tidak mendukung tool calling menurut informasi kapabilitas LiteLLM, THEN THE Backend_API SHALL menghentikan proses startup dan menampilkan pesan error yang menyebutkan nama model dan agent terkait.

### Requirement 10: Pembuatan dan Eksekusi SQL

**User Story:** Sebagai pengguna, saya ingin agent menjawab pertanyaan data saya dengan query yang dieksekusi pada data sebenarnya, sehingga hasil analisis akurat.

#### Acceptance Criteria

1. THE Data_Engine SHALL mendaftarkan setiap Dataset di Workspace sebagai tabel di `pl.SQLContext` dengan nama tabel yang unik di dalam Workspace.
2. WHEN Query_Agent membutuhkan data, THE Query_Agent SHALL menghasilkan SQL dan mengeksekusinya melalui tool `run_sql` yang disediakan Backend_API.
3. THE SQL_Validator SHALL hanya mengizinkan pernyataan SQL tunggal bertipe SELECT (termasuk CTE `WITH ... SELECT`).
4. IF SQL berisi pernyataan selain SELECT (misalnya INSERT, UPDATE, DELETE, DROP, CREATE, COPY) atau lebih dari satu pernyataan, THEN THE SQL_Validator SHALL menolak SQL tersebut tanpa mengeksekusinya dan mengembalikan error yang menyebutkan alasan penolakan.
5. WHEN SQL berhasil dieksekusi, THE Data_Engine SHALL mengembalikan hasil berupa nama kolom, tipe data, baris hasil, dan jumlah baris total.
6. IF eksekusi SQL melebihi batas waktu 60 detik, THEN THE Data_Engine SHALL membatalkan eksekusi dan mengembalikan error timeout.
7. THE Backend_API SHALL menyimpan setiap SQL yang dieksekusi beserta ringkasan hasilnya sehingga dapat direferensikan oleh Chart_Spec dan Insight_Card.

### Requirement 11: Validasi SQL dan Retry

**User Story:** Sebagai pengguna, saya ingin agent memperbaiki SQL yang salah secara otomatis, sehingga saya tidak perlu menangani error teknis.

#### Acceptance Criteria

1. WHEN SQL diterima oleh tool `run_sql`, THE SQL_Validator SHALL membangun LazyFrame dari SQL tersebut dan memanggil `collect_schema` sebelum eksekusi.
2. IF pembentukan LazyFrame atau `collect_schema` gagal karena SQL tidak valid atau tidak didukung Polars, THEN THE SQL_Validator SHALL mengembalikan pesan error Polars beserta daftar tabel dan kolom yang tersedia kepada Query_Agent.
3. WHEN Query_Agent menerima error validasi, THE Query_Agent SHALL menghasilkan SQL perbaikan menggunakan pesan error tersebut, dengan maksimal 3 kali percobaan ulang per permintaan.
4. IF SQL masih gagal setelah 3 kali percobaan ulang, THEN THE Root_Agent SHALL memberi tahu pengguna bahwa query tidak dapat dibuat dan menampilkan error terakhir.

### Requirement 12: Desain Chart dengan ECharts

**User Story:** Sebagai pengguna, saya ingin agent membuat chart yang sesuai dengan data dan pertanyaan saya, sehingga visualisasi mudah dipahami.

#### Acceptance Criteria

1. WHEN pengguna meminta chart, THE Chart_Designer_Agent SHALL memilih tipe chart berdasarkan tipe kolom dan bentuk hasil query (misalnya line untuk deret waktu, bar untuk perbandingan kategori, pie untuk proporsi dengan kategori maksimal 8, scatter untuk hubungan dua ukuran).
2. THE Chart_Designer_Agent SHALL menghasilkan Chart_Spec berupa opsi ECharts JSON yang mereferensikan identifier query dan nama kolom hasil query untuk binding data.
3. WHEN chart dirender, THE Backend_API SHALL mengeksekusi query yang direferensikan Chart_Spec dan mengikat hasilnya ke dataset/series ECharts sebelum mengirimkannya ke Frontend.
4. THE Frontend SHALL merender Chart_Spec menggunakan echarts-for-react.
5. THE Chart_Designer_Agent SHALL menyertakan judul, label sumbu, dan legenda pada Chart_Spec sesuai tipe chart.
6. WHEN pengguna meminta perubahan chart melalui chat (misalnya tipe chart, warna, judul, pengelompokan), THE Chart_Designer_Agent SHALL memperbarui Chart_Spec melalui tool `update_chart`.

### Requirement 13: Validasi Chart_Spec

**User Story:** Sebagai pengguna, saya ingin chart yang disimpan selalu valid, sehingga dashboard tidak rusak saat dibuka.

#### Acceptance Criteria

1. WHEN Chart_Spec akan disimpan, THE Chart_Spec_Validator SHALL memvalidasi Chart_Spec terhadap skema Chart_Spec yang didukung (tipe series yang diizinkan, struktur sumbu, dan referensi binding data).
2. IF Chart_Spec berisi data yang disisipkan langsung (inline) pada series atau dataset, THEN THE Chart_Spec_Validator SHALL menolak Chart_Spec dan mengembalikan error yang meminta binding ke hasil query.
3. IF Chart_Spec mereferensikan kolom yang tidak ada pada skema hasil query, THEN THE Chart_Spec_Validator SHALL menolak Chart_Spec dan mengembalikan error yang menyebutkan nama kolom tersebut.
4. IF Chart_Spec berisi nilai bertipe fungsi JavaScript atau string yang dimaksudkan untuk dieksekusi sebagai kode, THEN THE Chart_Spec_Validator SHALL menolak Chart_Spec tersebut.
5. WHEN Chart_Spec_Validator menolak Chart_Spec dari agent, THE Chart_Designer_Agent SHALL memperbaiki Chart_Spec menggunakan pesan error dengan maksimal 3 kali percobaan ulang.
6. FOR ALL Chart_Spec yang valid, men-serialisasi Chart_Spec ke JSON lalu mem-parse JSON tersebut lalu memvalidasinya SHALL menghasilkan Chart_Spec yang ekuivalen dan tetap valid (round-trip property).

### Requirement 14: Insight Card Berbasis Bukti

**User Story:** Sebagai pengguna, saya ingin insight yang dapat dipertanggungjawabkan, sehingga saya bisa memercayai angka yang ditampilkan.

#### Acceptance Criteria

1. WHEN pengguna meminta insight, THE Insight_Agent SHALL menghasilkan Insight_Card dengan salah satu tipe: tren, anomali, perbandingan, kontributor teratas/terbawah, atau korelasi lintas-dataset.
2. THE Insight_Agent SHALL menautkan setiap Insight_Card ke SQL sumber dan hasil numerik yang dihasilkan dari eksekusi SQL tersebut.
3. THE Insight_Agent SHALL menggunakan hanya angka yang berasal dari hasil eksekusi query pada teks Insight_Card.
4. IF teks Insight_Card mengandung angka yang tidak dapat dicocokkan dengan nilai pada hasil query tertaut (setelah pembulatan tampilan), THEN THE Backend_API SHALL menolak Insight_Card tersebut dan mengembalikan error kepada Insight_Agent yang menyebutkan angka yang tidak tercocokkan.
5. THE Backend_API SHALL menyimpan pada setiap Insight_Card: filter yang aktif saat insight dihitung, waktu perhitungan, dan Dataset yang digunakan.
6. WHERE Insight_Card bertipe korelasi lintas-dataset, THE Insight_Agent SHALL menggunakan SQL yang menggabungkan Dataset hanya melalui Confirmed_Relation.
7. WHEN pengguna membuka detail Insight_Card, THE Frontend SHALL menampilkan SQL sumber dan tabel hasil numerik yang menjadi bukti insight.

### Requirement 15: Refresh Insight

**User Story:** Sebagai pengguna, saya ingin memperbarui insight setelah filter atau data berubah, sehingga insight tetap relevan.

#### Acceptance Criteria

1. WHEN pengguna meminta refresh Insight_Card, THE Backend_API SHALL mengeksekusi ulang SQL sumber dengan filter yang saat ini aktif dan memperbarui hasil numerik Insight_Card.
2. WHEN hasil numerik berubah setelah refresh, THE Insight_Agent SHALL menyusun ulang teks Insight_Card berdasarkan hasil numerik yang baru.
3. WHILE filter yang aktif di Dashboard berbeda dari filter yang tersimpan pada Insight_Card, THE Canvas_Editor SHALL menampilkan penanda bahwa Insight_Card dihitung dengan filter yang berbeda.

### Requirement 16: Interaksi Chat dan Streaming SSE

**User Story:** Sebagai pengguna, saya ingin melihat respons agent secara bertahap, sehingga saya tahu agent sedang bekerja.

#### Acceptance Criteria

1. WHEN pengguna mengirim pesan di Chat_Panel, THE Backend_API SHALL men-stream respons agent ke Frontend melalui Server-Sent Events.
2. WHEN Backend_API menerima pesan pengguna, THE Backend_API SHALL mengirim event SSE pertama dalam waktu 2 detik.
3. THE Backend_API SHALL mengirim event SSE terpisah untuk potongan teks agent, pemanggilan tool, hasil tool, Patch_Event, dan error.
4. WHILE agent memproses pesan, THE Chat_Panel SHALL menampilkan status agent yang aktif dan tool yang sedang dipanggil.
5. IF koneksi SSE terputus, THEN THE Frontend SHALL menyambung ulang dan meminta state Dashboard terbaru dari Backend_API berdasarkan Dashboard_Version terakhir yang diketahui.
6. WHEN pengguna menekan tombol berhenti selama agent memproses pesan, THE Backend_API SHALL menghentikan pemrosesan agent untuk pesan tersebut dan mengirim event SSE penghentian.

### Requirement 17: Canvas Editor Manual

**User Story:** Sebagai pengguna, saya ingin mengedit dashboard secara manual, sehingga saya dapat menyempurnakan hasil agent sesuai keinginan.

#### Acceptance Criteria

1. THE Canvas_Editor SHALL menampilkan chart dan Insight_Card dalam layout grid menggunakan react-grid-layout.
2. WHEN pengguna men-drag atau me-resize elemen di Canvas_Editor, THE Frontend SHALL mengirim perubahan layout ke Backend_API sebagai User_Edit_Event.
3. WHEN pengguna mengganti tipe chart melalui Canvas_Editor, THE Frontend SHALL mengirim perubahan tersebut ke Backend_API sebagai User_Edit_Event, dan THE Chart_Spec_Validator SHALL memvalidasi Chart_Spec hasil perubahan sebelum disimpan.
4. WHEN pengguna menghapus elemen di Canvas_Editor, THE Frontend SHALL mengirim penghapusan tersebut ke Backend_API sebagai User_Edit_Event.
5. WHEN User_Edit_Event diterima, THE Dashboard_Store SHALL menerapkan perubahan melalui API yang sama dengan yang digunakan Agent_Tools.

### Requirement 18: State Dashboard, Versioning, dan Patch

**User Story:** Sebagai pengguna, saya ingin perubahan dari agent dan dari saya selalu konsisten, sehingga dashboard tidak pernah berada dalam keadaan yang saling bertentangan.

#### Acceptance Criteria

1. THE Dashboard_Store SHALL menjadi satu-satunya sumber kebenaran untuk state Dashboard.
2. WHEN Agent_Tools atau User_Edit_Event mengubah Dashboard, THE Dashboard_Store SHALL menerapkan perubahan sebagai Patch_Event dan menaikkan Dashboard_Version tepat satu.
3. WHEN Patch_Event diterapkan, THE Backend_API SHALL menyiarkan Patch_Event beserta Dashboard_Version baru ke Frontend melalui SSE.
4. THE Dashboard_Store SHALL menyimpan setiap Patch_Event beserta sumbernya (agent atau pengguna), waktu, dan Dashboard_Version di Metadata_Store.
5. IF permintaan perubahan menyertakan Dashboard_Version dasar yang lebih lama dari Dashboard_Version saat ini, THEN THE Dashboard_Store SHALL menolak perubahan dengan error konflik yang menyertakan Dashboard_Version saat ini.
6. WHEN Frontend menerima error konflik, THE Frontend SHALL mengambil state Dashboard terbaru dan memberi tahu pengguna bahwa perubahannya ditolak karena state sudah berubah.
7. FOR ALL urutan Patch_Event yang valid, menerapkan urutan tersebut ke state Dashboard awal SHALL menghasilkan state yang sama dengan state yang direkonstruksi dari riwayat Patch_Event yang tersimpan (invariant replay).
8. FOR ALL urutan Patch_Event yang diterapkan, Dashboard_Version akhir SHALL sama dengan Dashboard_Version awal ditambah jumlah Patch_Event yang berhasil diterapkan (invariant).

### Requirement 19: Undo dan Redo

**User Story:** Sebagai pengguna, saya ingin membatalkan dan mengulang perubahan, sehingga saya bisa bereksperimen tanpa takut merusak dashboard.

#### Acceptance Criteria

1. WHEN pengguna menekan undo, THE Dashboard_Store SHALL membalik Patch_Event terakhir yang belum dibatalkan (dari agent maupun pengguna) sebagai Patch_Event baru dan menaikkan Dashboard_Version.
2. WHEN pengguna menekan redo setelah undo, THE Dashboard_Store SHALL menerapkan kembali Patch_Event yang terakhir dibatalkan sebagai Patch_Event baru dan menaikkan Dashboard_Version.
3. WHEN Patch_Event baru diterapkan setelah undo, THE Dashboard_Store SHALL mengosongkan tumpukan redo.
4. FOR ALL state Dashboard dan Patch_Event yang valid, menerapkan Patch_Event lalu undo SHALL menghasilkan konten Dashboard yang ekuivalen dengan konten sebelum Patch_Event diterapkan (round-trip property).
5. FOR ALL state Dashboard, menerapkan Patch_Event lalu undo lalu redo SHALL menghasilkan konten Dashboard yang ekuivalen dengan konten setelah Patch_Event pertama kali diterapkan (round-trip property).

### Requirement 20: Kesadaran Agent terhadap Edit Manual

**User Story:** Sebagai pengguna, saya ingin agent mengetahui perubahan yang saya buat secara manual, sehingga agent tidak menimpa atau mengabaikan pekerjaan saya.

#### Acceptance Criteria

1. WHEN User_Edit_Event diterapkan, THE Backend_API SHALL menambahkan ringkasan User_Edit_Event tersebut ke konteks sesi agent Workspace terkait.
2. WHEN agent memproses pesan pengguna berikutnya, THE Root_Agent SHALL menerima state Dashboard terbaru beserta Dashboard_Version saat ini.
3. WHEN agent memanggil Agent_Tools yang mengubah Dashboard, THE Agent_Tools SHALL menyertakan Dashboard_Version dasar yang diketahui agent sehingga perubahan yang basi ditolak sesuai Requirement 18.
4. IF perubahan dari agent ditolak karena konflik versi, THEN THE Root_Agent SHALL memuat ulang state Dashboard terbaru dan merencanakan ulang perubahan tersebut dengan maksimal 2 kali percobaan ulang.

### Requirement 21: Alur Setelah Upload dan Persetujuan Perubahan Canvas

**User Story:** Sebagai pengguna, saya ingin agent memandu saya setelah upload tanpa mengubah dashboard tanpa izin, sehingga saya tetap memegang kendali.

#### Acceptance Criteria

1. WHEN profiling Dataset baru selesai, THE Root_Agent SHALL menampilkan ringkasan profil, Relation_Candidate, dan draft Semantic_Model (Requirement 32) di Chat_Panel untuk dikonfirmasi pengguna.
2. WHEN pengguna selesai menanggapi Relation_Candidate dan draft Semantic_Model, THE Root_Agent SHALL mendelegasikan ke Dashboard_Architect_Agent untuk menawarkan Dashboard_Blueprint (Requirement 37) beserta asumsi yang dipakai.
3. THE Root_Agent SHALL memanggil Agent_Tools yang mengubah Dashboard hanya setelah pengguna meminta atau menyetujui perubahan tersebut secara eksplisit di Chat_Panel.
4. IF tidak ada permintaan atau persetujuan pengguna untuk mengubah Dashboard pada giliran percakapan saat ini, THEN THE Backend_API SHALL menolak pemanggilan Agent_Tools yang mengubah Dashboard dan mengembalikan error kepada agent.

### Requirement 22: Global Filter

**User Story:** Sebagai pengguna, saya ingin memfilter seluruh dashboard berdasarkan tanggal atau kategori, sehingga saya dapat fokus pada subset data.

#### Acceptance Criteria

1. THE Canvas_Editor SHALL menyediakan Global_Filter bertipe rentang tanggal untuk kolom waktu dan Global_Filter kategorikal untuk kolom dimensi.
2. WHEN Global_Filter berubah, THE Filter_Engine SHALL mendaftarkan LazyFrame yang telah difilter dengan nama tabel yang sama di `pl.SQLContext` untuk permintaan render tersebut dan mengeksekusi SQL chart tanpa mengubah teks SQL.
3. WHEN Global_Filter berubah, THE Backend_API SHALL merender ulang chart yang terpengaruh tanpa memanggil LLM.
4. WHEN Global_Filter berubah, THE Backend_API SHALL mengembalikan hasil render ulang semua chart yang terpengaruh dalam waktu 5 detik untuk Dataset berukuran hingga 10 juta baris.
5. FOR ALL tabel dan Global_Filter, setiap baris pada tabel hasil filter SHALL memenuhi predikat filter, dan jumlah baris tabel hasil filter SHALL kurang dari atau sama dengan jumlah baris tabel asli (invariant/metamorphic).
6. FOR ALL tabel dan Global_Filter, menerapkan filter yang sama dua kali SHALL menghasilkan tabel yang sama dengan menerapkannya sekali (idempotence).
7. FOR ALL tabel dan dua Global_Filter, menerapkan kedua filter dalam urutan berbeda SHALL menghasilkan tabel yang sama (confluence).
8. THE Canvas_Editor SHALL menyediakan aksi "Reset semua filter" yang, dalam satu aksi pengguna, mengosongkan Global_Filter melalui Dashboard_Store dan menghapus semua Cross_Filter aktif.

### Requirement 23: Propagasi Filter melalui Relasi

**User Story:** Sebagai pengguna, saya ingin filter pada satu dataset juga berlaku pada dataset terkait, sehingga chart lintas-dataset tetap konsisten.

#### Acceptance Criteria

1. WHEN Global_Filter diterapkan pada kolom suatu tabel, THE Filter_Engine SHALL mempropagasikan filter ke tabel lain yang terhubung melalui Confirmed_Relation menggunakan semi-join.
2. THE Filter_Engine SHALL mempropagasikan filter hanya melalui Confirmed_Relation.
3. WHEN chart tidak menggunakan tabel yang terfilter maupun tabel yang terhubung melalui Confirmed_Relation dengan tabel terfilter, THE Canvas_Editor SHALL menandai chart tersebut sebagai tidak terpengaruh oleh filter.
4. IF graf Confirmed_Relation mengandung siklus, THEN THE Filter_Engine SHALL mempropagasikan filter ke setiap tabel paling banyak satu kali per permintaan render.
5. FOR ALL tabel yang terhubung melalui Confirmed_Relation, setiap nilai kunci relasi pada tabel hasil propagasi SHALL terdapat pada nilai kunci tabel sumber yang telah difilter (invariant semi-join).

### Requirement 24: Cross-Filtering

**User Story:** Sebagai pengguna, saya ingin mengklik elemen chart untuk memfilter chart lain, sehingga saya dapat mengeksplorasi data secara interaktif.

#### Acceptance Criteria

1. WHEN pengguna mengklik elemen chart (misalnya bar, slice, atau titik) yang terikat ke kolom dimensi, THE Frontend SHALL membuat Cross_Filter pada kolom dan nilai elemen tersebut.
2. WHEN Cross_Filter aktif, THE Filter_Engine SHALL menerapkan Cross_Filter ke semua chart lain dengan mekanisme yang sama seperti Global_Filter.
3. WHILE Cross_Filter aktif, THE Canvas_Editor SHALL menampilkan penanda Cross_Filter aktif beserta aksi untuk menghapusnya.
4. WHEN pengguna mengklik elemen yang sama untuk kedua kalinya atau menghapus penanda Cross_Filter, THE Frontend SHALL menghapus Cross_Filter dan THE Backend_API SHALL merender ulang chart yang terpengaruh.

### Requirement 25: Ekspor Dashboard

**User Story:** Sebagai pengguna, saya ingin mengekspor dashboard, sehingga saya dapat membagikannya dalam laporan.

#### Acceptance Criteria

1. WHEN pengguna memilih ekspor PNG, THE Export_Service SHALL menghasilkan file PNG yang memuat seluruh chart dan Insight_Card sesuai layout dan filter yang aktif.
2. WHEN pengguna memilih ekspor PDF, THE Export_Service SHALL menghasilkan file PDF yang memuat seluruh chart, Insight_Card, judul Dashboard, filter yang aktif, dan waktu ekspor.
3. IF ekspor gagal, THEN THE Export_Service SHALL menampilkan pesan error kepada pengguna dan mempertahankan state Dashboard tanpa perubahan.

### Requirement 26: Re-upload Dataset untuk Refresh

**User Story:** Sebagai pengguna, saya ingin mengunggah versi terbaru dataset, sehingga semua chart dan insight diperbarui tanpa membangun ulang dashboard.

#### Acceptance Criteria

1. WHEN pengguna mengunggah ulang file untuk Dataset yang sudah ada, THE Ingestion_Service SHALL membandingkan skema file baru dengan skema Dataset yang tersimpan.
2. WHEN skema file baru sama dengan skema tersimpan (nama kolom dan tipe data kompatibel), THE Ingestion_Service SHALL mengganti file Parquet Dataset dan THE Backend_API SHALL merender ulang semua chart dan menandai semua Insight_Card terkait untuk di-refresh.
3. IF skema file baru berbeda dari skema tersimpan, THEN THE Ingestion_Service SHALL menolak penggantian, mempertahankan Dataset lama, dan melaporkan kolom yang hilang, kolom yang ditambahkan, dan kolom dengan tipe berubah.
4. FOR ALL pasangan skema, fungsi perbandingan skema SHALL melaporkan tidak ada perbedaan jika dan hanya jika kedua skema memiliki himpunan nama kolom yang sama dan tipe data yang kompatibel untuk setiap kolom.

### Requirement 27: Privasi Data ke LLM

**User Story:** Sebagai pengguna, saya ingin mengontrol data yang dikirim ke LLM, sehingga data sensitif terlindungi.

#### Acceptance Criteria

1. THE Privacy_Guard SHALL membatasi informasi Dataset yang dikirim ke LLM pada skema, Column_Profile, dan Sample_Rows.
2. THE Privacy_Guard SHALL menggunakan 5 sebagai jumlah default Sample_Rows per Dataset.
3. WHEN hasil query dikirim ke LLM, THE Privacy_Guard SHALL memotong hasil menjadi maksimal 200 baris dan menyertakan jumlah baris total beserta penanda bahwa hasil telah dipotong.
4. WHERE toggle privasi "jangan kirim sample rows" aktif pada suatu Dataset, THE Privacy_Guard SHALL mengirim hanya skema dan Column_Profile Dataset tersebut ke LLM.
5. WHERE toggle privasi "jangan kirim sample rows" aktif pada suatu Dataset, THE Privacy_Guard SHALL menghapus nilai contoh (misalnya nilai terbanyak) dari Column_Profile kolom bertipe string sebelum dikirim ke LLM.
6. FOR ALL payload yang dikirim ke LLM, jumlah baris data per Dataset SHALL kurang dari atau sama dengan jumlah Sample_Rows yang dikonfigurasi, dan jumlah baris hasil query SHALL kurang dari atau sama dengan 200 (invariant).

### Requirement 28: Persistensi Lokal

**User Story:** Sebagai pengguna, saya ingin pekerjaan saya tetap tersimpan setelah aplikasi dimatikan, sehingga saya dapat melanjutkan kapan saja.

#### Acceptance Criteria

1. THE Backend_API SHALL menyimpan metadata Workspace, Dataset, relasi, Dashboard, layout, Chart_Spec, Insight_Card, dan Patch_Event di Metadata_Store berupa satu file SQLite lokal.
2. THE Session_Service SHALL menyimpan riwayat chat agent di SQLite menggunakan `DatabaseSessionService` ADK.
3. THE Ingestion_Service SHALL menyimpan file upload dan file Parquet di folder lokal `data/uploads/{workspace_id}/`.
4. WHEN Backend_API dijalankan ulang, THE Backend_API SHALL memulihkan semua Workspace, Dataset, Dashboard beserta Dashboard_Version, dan riwayat chat dari penyimpanan lokal.
5. THE Studio SHALL menyimpan semua data hanya di penyimpanan lokal, tanpa menggunakan cloud storage.

### Requirement 29: Keamanan Lokal dan Kesiapan Autentikasi

**User Story:** Sebagai pengguna tunggal, saya ingin aplikasi hanya dapat diakses dari komputer saya, sehingga data saya tidak terekspos ke jaringan.

#### Acceptance Criteria

1. WHEN Backend_API dijalankan dengan `python main.py` tanpa konfigurasi host eksplisit, THE Backend_API SHALL melakukan bind ke `127.0.0.1`.
2. WHERE host dikonfigurasi ke alamat selain loopback, THE Backend_API SHALL menampilkan peringatan saat startup bahwa API tanpa autentikasi dapat diakses dari jaringan.
3. THE Backend_API SHALL menyertakan kolom `owner_id` pada entitas Workspace, Dataset, dan Dashboard di Metadata_Store.
4. THE Backend_API SHALL mengizinkan CORS hanya untuk origin Frontend lokal yang dikonfigurasi.
5. IF nama file upload berisi komponen path (misalnya `..`, `/`, atau `\`), THEN THE Ingestion_Service SHALL menormalisasi nama file sehingga file tersimpan hanya di dalam folder `data/uploads/{workspace_id}/`.

### Requirement 30: Pengujian dan Evaluasi

**User Story:** Sebagai developer, saya ingin logika deterministik dan perilaku agent teruji, sehingga regresi terdeteksi sejak dini.

#### Acceptance Criteria

1. THE Studio SHALL menyediakan property-based test menggunakan Hypothesis untuk parsing CSV/XLSX, konversi Parquet, propagasi filter, patch berversi (termasuk undo/redo), dan validasi Chart_Spec.
2. THE Studio SHALL menyediakan ADK eval (`.evalset.json`) dengan dataset contoh yang memeriksa validitas SQL yang dihasilkan, kecocokan angka Insight_Card dengan hasil query, dan ketepatan pemanggilan tool.
3. THE Studio SHALL menyediakan test Vitest pada Frontend untuk logika state Dashboard dan penerapan Patch_Event.
4. THE Studio SHALL menyediakan minimal satu set dataset contoh multi-dataset dengan relasi (misalnya tabel pelanggan dan tabel transaksi melalui `customer_id`) untuk digunakan oleh ADK eval.

### Requirement 31: Model Semantik Workspace

**User Story:** Sebagai pengguna, saya ingin Studio menyimpan arti bisnis data saya, sehingga agent memahami data seperti seorang analis, bukan hanya membaca nama kolom.

#### Acceptance Criteria

1. THE Backend_API SHALL menyimpan satu Semantic_Model per Workspace di Metadata_Store yang terdiri dari Semantic_Column, Business_Metric, Glossary_Term, Workspace_Instruction, dan Verified_Query.
2. THE Semantic_Model SHALL menyimpan untuk setiap Semantic_Column: Dataset, nama kolom, label, deskripsi, sinonim, agregasi default (`sum`, `avg`, `count`, `count_distinct`, `min`, `max`, atau `none`), format angka (`number`, `currency`, atau `percent` beserta kode mata uang dan jumlah desimal), dan penanda enum.
3. THE Semantic_Model SHALL menyimpan untuk setiap Business_Metric: nama unik per Workspace, label, deskripsi, ekspresi agregat SQL, tabel dasar, sinonim, format angka, arah nilai yang baik (`up`, `down`, atau `neutral`), dan kolom waktu opsional.
4. THE Semantic_Model SHALL memberi setiap Semantic_Entry status `candidate`, `confirmed`, atau `rejected` dan sumber `auto` atau `user`.
5. WHEN Business_Metric dibuat, diubah, atau diimpor, THE SQL_Validator SHALL memvalidasi ekspresinya sebagai `SELECT <ekspresi> AS value FROM <tabel dasar>` dengan aturan yang sama seperti Requirement 10 dan 7.6.
6. IF validasi ekspresi Business_Metric gagal, THEN THE Backend_API SHALL menolak Business_Metric tersebut dan mengembalikan kode error beserta detail kolom atau tabel yang bermasalah.
7. WHEN pengguna mengedit Semantic_Entry, THE Backend_API SHALL menyimpan entri tersebut dengan sumber `user` dan status `confirmed`.
8. WHEN pengguna menolak Semantic_Entry, THE Backend_API SHALL menyimpan kunci kanonik entri tersebut sehingga Semantic_Drafter tidak mengusulkannya kembali.
9. WHEN Dataset dihapus, THE Backend_API SHALL menghapus Semantic_Column milik Dataset tersebut dan menandai Business_Metric serta Verified_Query yang merujuk Dataset tersebut sebagai tidak valid.
10. THE Backend_API SHALL menyediakan ekspor dan impor Semantic_Model dalam format YAML.
11. IF berkas impor YAML memuat entri yang tidak valid, THEN THE Backend_API SHALL menolak seluruh impor tanpa mengubah Semantic_Model dan melaporkan setiap entri yang tidak valid beserta alasannya.
12. FOR ALL Semantic_Model yang valid, mengekspor ke YAML lalu mengimpor hasilnya SHALL menghasilkan Semantic_Model yang ekuivalen (round-trip).

### Requirement 32: Draft Semantik Otomatis

**User Story:** Sebagai pengguna, saya ingin agent langsung memahami data saya setelah upload tanpa saya harus mendokumentasikan semuanya, sehingga saya cukup mengoreksi bagian yang keliru.

#### Acceptance Criteria

1. WHEN profiling Dataset selesai, THE Semantic_Drafter SHALL menyusun draft Semantic_Model secara otomatis tanpa permintaan pengguna.
2. THE Semantic_Drafter SHALL menyusun draft heuristik deterministik lebih dahulu: label dari nama kolom, agregasi default dari peran kolom (`measure` → `sum`, `identifier` → `count_distinct`, `dimension` dan `time` → `none`), dan penanda enum untuk kolom dimensi dengan jumlah nilai unik paling banyak 50.
3. WHEN draft heuristik tersimpan, THE Semantic_Drafter SHALL memperkaya draft melalui LLM dengan dugaan domain data, deskripsi kolom, sinonim, Business_Metric usulan yang mengacu pada katalog KPI di BI_Knowledge_Pack, dan Glossary_Term.
4. THE Semantic_Drafter SHALL mengirim data ke LLM hanya melalui Privacy_Guard sesuai Requirement 27.
5. THE Semantic_Drafter SHALL menyimpan semua entri draft dengan status `candidate` dan tidak menimpa Semantic_Entry yang berstatus `confirmed` atau bersumber `user`.
6. THE Semantic_Drafter SHALL tidak mengusulkan entri yang kunci kanoniknya pernah ditolak pengguna.
7. IF pengayaan LLM gagal, melewati batas waktu, atau menghasilkan output yang tidak sesuai skema, THEN THE Semantic_Drafter SHALL mempertahankan draft heuristik dan mengirim event peringatan ke Chat_Panel.
8. IF sebuah entri hasil LLM tidak valid (misalnya ekspresi Business_Metric gagal validasi atau merujuk kolom yang tidak ada), THEN THE Semantic_Drafter SHALL membuang entri tersebut, menyimpan entri valid lainnya, dan mencatat entri yang dibuang beserta alasannya.
9. WHEN draft selesai disimpan, THE Backend_API SHALL menyiarkan event `semantic.updated` dan THE Root_Agent SHALL menampilkan kartu "Pemahaman data" di Chat_Panel berisi dugaan domain, Business_Metric usulan beserta rumusnya, kolom penting, dan asumsi.
10. THE Chat_Panel SHALL menyediakan aksi konfirmasi, edit, dan tolak untuk setiap entri pada kartu "Pemahaman data" serta aksi "Konfirmasi semua".
11. THE Semantic_Drafter SHALL berjalan di latar belakang tanpa mengubah Dashboard dan tanpa memblokir percakapan.

### Requirement 33: Konteks Data dan Semantik untuk Agent

**User Story:** Sebagai pengguna, saya ingin setiap agent sudah membawa konteks data saya sejak giliran pertama, sehingga jawaban dan SQL-nya sesuai arti bisnis data saya.

#### Acceptance Criteria

1. WHEN agent memproses sebuah giliran, THE Backend_API SHALL menyisipkan konteks data ke instruksi agent sesuai cakupannya: Root_Agent menerima ringkasan Dataset, dugaan domain, nama Business_Metric, dan ringkasan Dashboard; Query_Agent menerima skema, Semantic_Column, Business_Metric beserta ekspresinya, Workspace_Instruction, Confirmed_Relation, dan Verified_Query yang relevan; Dashboard_Architect_Agent menerima domain, Business_Metric, Glossary_Term, Design_Brief, dan ringkasan Dashboard; Chart_Designer_Agent dan Insight_Agent menerima label serta format kolom dan Business_Metric.
2. THE Backend_API SHALL menyisipkan Semantic_Entry berstatus `confirmed` sebagai fakta, menyisipkan Semantic_Entry berstatus `candidate` dengan penanda "belum dikonfirmasi", dan tidak menyisipkan Semantic_Entry berstatus `rejected`.
3. THE Backend_API SHALL membatasi blok konteks semantik per agent pada anggaran karakter yang dapat dikonfigurasi dengan nilai default 12.000 karakter.
4. WHEN blok konteks semantik melebihi anggaran, THE Backend_API SHALL memasukkan entri berdasarkan urutan prioritas Workspace_Instruction, Business_Metric `confirmed`, Semantic_Column yang memiliki deskripsi, lalu entri lainnya, dan menyertakan penanda bahwa entri sisanya tersedia melalui tool `search_semantic`.
5. WHERE toggle privasi "jangan kirim sample rows" aktif pada suatu Dataset, THE Backend_API SHALL menghapus daftar nilai enum kolom Dataset tersebut dari konteks yang dikirim ke LLM.
6. WHEN pertanyaan pengguna merujuk sebuah Business_Metric atau sinonimnya, THE Query_Agent SHALL menggunakan ekspresi Business_Metric tersebut pada SQL yang dihasilkan.
7. THE Query_Agent SHALL menerapkan setiap Workspace_Instruction yang relevan pada SQL yang dihasilkan.
8. FOR ALL Semantic_Model dan anggaran, blok konteks semantik SHALL berukuran paling besar sama dengan anggaran, tidak memuat entri `rejected`, dan bila memuat suatu entri SHALL juga memuat semua entri dengan prioritas lebih tinggi (invariant).

### Requirement 34: Verified Query

**User Story:** Sebagai pengguna, saya ingin agent belajar dari query yang sudah terbukti benar, sehingga jawabannya makin akurat seiring pemakaian.

#### Acceptance Criteria

1. WHEN agent menambahkan chart atau KPI_Card ke Dashboard, THE Backend_API SHALL membuat Verified_Query berstatus `candidate` yang berisi pesan pengguna pada giliran tersebut (atau tujuan Blueprint_Slot), SQL, `query_id`, dan id item.
2. WHEN pengguna memilih aksi "Tandai terverifikasi" pada item Dashboard atau pada Verified_Query, THE Backend_API SHALL menyimpan Verified_Query tersebut dengan status `confirmed`.
3. WHEN agent memanggil tool `find_verified_queries` dengan sebuah pertanyaan dan batas k (default 3), THE Backend_API SHALL mengembalikan paling banyak k Verified_Query berstatus `confirmed` dan valid, diurutkan dari skor kemiripan kata kunci tertinggi, dengan Verified_Query terbaru didahulukan bila skornya sama.
4. IF SQL sebuah Verified_Query tidak lagi lolos SQL_Validator (misalnya kolom hilang atau relasi dihapus), THEN THE Backend_API SHALL menandai Verified_Query tersebut tidak valid dan tidak mengembalikannya ke agent.
5. FOR ALL kumpulan Verified_Query, pertanyaan, dan k, hasil `find_verified_queries` SHALL berjumlah paling banyak k, hanya memuat entri `confirmed` yang valid, berurutan dengan skor tidak naik, dan identik untuk masukan yang sama (determinisme).

### Requirement 35: Dashboard_Architect_Agent dan Pengetahuan BI

**User Story:** Sebagai pengguna, saya ingin agent yang merancang dashboard memiliki pengetahuan BI dan arah kerja yang jelas, tetapi tetap mengikuti cara saya merancang, sehingga hasilnya terarah tanpa terasa kaku.

#### Acceptance Criteria

1. THE Studio SHALL menyediakan Dashboard_Architect_Agent sebagai sub-agent Root_Agent yang bertanggung jawab merancang, mengarahkan, dan mereview Dashboard, tanpa menulis SQL maupun Chart_Spec.
2. THE Studio SHALL menyediakan BI_Knowledge_Pack berupa file Markdown lokal yang memuat minimal: prinsip desain dashboard, katalog KPI beserta rumus untuk domain sales, finance, marketing, operations, HR, dan e-commerce, playbook per domain tersebut, desain interaksi filter, dan panduan pemilihan chart.
3. WHEN Dashboard_Architect_Agent memanggil tool `get_bi_knowledge` dengan sebuah topik, THE Agent_Tools SHALL mengembalikan isi topik tersebut.
4. IF topik yang diminta tidak ada di BI_Knowledge_Pack, THEN THE Agent_Tools SHALL mengembalikan error beserta daftar topik yang tersedia.
5. THE Dashboard_Architect_Agent SHALL bekerja dengan urutan metode BI: framing (tujuan, audiens, periode), desain metrik, cerita dan layout, interaksi, pembangunan, dan review; langkah yang informasinya sudah tersedia di Design_Brief, pesan pengguna, atau Semantic_Model SHALL dilewati.
6. WHEN pengguna menyerahkan desain kepada agent tanpa rincian, THE Dashboard_Architect_Agent SHALL menyusun Dashboard_Blueprint lengkap dengan asumsi yang dinyatakan secara eksplisit, dan mengajukan paling banyak 3 pertanyaan klarifikasi dalam satu putaran hanya bila informasi kritis tidak dapat disimpulkan.
7. WHEN pengguna menentukan elemen, metrik, atau posisi tertentu, THE Dashboard_Architect_Agent SHALL mengikuti ketentuan pengguna tersebut dan menyampaikan saran BI sebagai usulan yang tidak menghalangi.
8. WHILE pengguna menyusun Dashboard secara bertahap, THE Dashboard_Architect_Agent SHALL menjaga konsistensi dengan Design_Brief dan menyarankan bagian yang belum ada.
9. THE Dashboard_Architect_Agent SHALL tidak mengubah atau menghapus item yang dibuat atau diedit pengguna tanpa permintaan eksplisit pengguna.
10. WHEN pengguna meminta merancang, menyusun, atau mereview Dashboard, atau setelah draft Semantic_Model ditanggapi pengguna, THE Root_Agent SHALL mendelegasikan ke Dashboard_Architect_Agent.

### Requirement 36: Design Brief

**User Story:** Sebagai pengguna, saya ingin maksud desain dashboard tercatat dan dapat saya ubah, sehingga agent tetap konsisten sepanjang sesi.

#### Acceptance Criteria

1. THE Dashboard_Store SHALL menyimpan Design_Brief opsional pada setiap Dashboard yang berisi tujuan, audiens, pertanyaan bisnis kunci, KPI beserta pembandingnya (`previous_period`, `target`, atau `none`), bagian, grain waktu, dan asumsi.
2. THE Dashboard_Store SHALL menerapkan perubahan Design_Brief hanya melalui Patch_Event sehingga perubahan tersebut berversi dan dapat di-undo serta di-redo sesuai Requirement 18 dan 19.
3. THE Frontend SHALL menyediakan panel Brief tempat pengguna melihat dan mengedit Design_Brief.
4. WHEN Dashboard_Architect_Agent mengubah Design_Brief, THE Agent_Tools SHALL menerapkan approval gate yang sama dengan tool pengubah Dashboard lainnya (Requirement 21.3, 21.4).
5. WHEN edit manual pengguna menghapus seluruh item pada suatu bagian Design_Brief atau menambah item yang tidak tercakup Design_Brief, THE Dashboard_Architect_Agent SHALL menawarkan pembaruan Design_Brief pada giliran berikutnya.

### Requirement 37: Dashboard Blueprint dan Pembangunan Terarah

**User Story:** Sebagai pengguna, saya ingin melihat dan menyetujui rancangan dashboard utuh sebelum dibangun, lalu agent membangunnya sesuai rancangan, sehingga hasil akhirnya seperti dashboard BI yang lengkap.

#### Acceptance Criteria

1. THE Dashboard_Architect_Agent SHALL mengusulkan Dashboard_Blueprint melalui tool `propose_dashboard_plan` yang berisi Design_Brief, Global_Filter bawaan, dan daftar Blueprint_Slot; setiap Blueprint_Slot memuat id, bagian (`kpi_row`, `trend`, `breakdown`, `composition`, `distribution`, `detail`, atau `other`), tujuan, tipe visual (`kpi`, `line`, `bar`, `pie`, `scatter`, `heatmap`, `waterfall`, atau `insight`), metrik, dimensi opsional, layout opsional, dan kolom Cross_Filter opsional.
2. WHEN `propose_dashboard_plan` dipanggil, THE Backend_API SHALL memvalidasi Dashboard_Blueprint: id slot unik, paling banyak 16 slot, setiap layout berada di dalam grid 12 kolom dan tidak tumpang tindih dengan slot lain maupun item Dashboard yang ada, serta setiap metrik merujuk Business_Metric atau kolom Dataset yang ada.
3. IF Dashboard_Blueprint tidak valid, THEN THE Agent_Tools SHALL mengembalikan error yang menyebut slot dan aturan yang dilanggar tanpa menampilkan Dashboard_Blueprint ke pengguna.
4. WHERE Blueprint_Slot tidak memiliki layout, THE Layout_Template SHALL menempatkan slot di bawah item Dashboard yang ada dengan aturan: slot `kpi_row` di baris teratas dengan tinggi 2 dan lebar dibagi rata (paling banyak 6 per baris), slot `trend` selebar 8 kolom dengan slot pendamping selebar 4 kolom, slot `breakdown`, `composition`, dan `distribution` berpasangan selebar 6 kolom, dan slot `detail` selebar 12 kolom.
5. WHEN Dashboard_Blueprint valid, THE Chat_Panel SHALL menampilkan kartu Blueprint berisi miniatur grid, daftar slot dengan pilihan centang per slot, aksi untuk mengubah tujuan dan tipe visual slot, serta aksi "Setujui terpilih", "Setujui semua", dan "Revisi".
6. WHEN pengguna menyetujui Dashboard_Blueprint, THE Frontend SHALL mengirim `proposal_id` beserta daftar id slot terpilih, dan THE Backend_API SHALL menyimpan Dashboard_Blueprint aktif yang hanya berisi slot terpilih.
7. WHEN Dashboard_Blueprint aktif tersimpan, THE Root_Agent SHALL membangun slot-slot terpilih secara berurutan dalam satu run, dan setiap item yang dihasilkan SHALL ditempatkan pada layout slot tersebut.
8. WHILE pembangunan Dashboard_Blueprint berlangsung, THE Backend_API SHALL mengirim event `blueprint.progress` berisi id slot dan status (`pending`, `building`, `done`, `failed`, atau `skipped`) beserta id item atau penyebab kegagalan.
9. THE TurnPolicy SHALL mengatur ulang penghitung retry di awal pembangunan setiap Blueprint_Slot.
10. IF pembangunan sebuah Blueprint_Slot gagal setelah batas retry, THEN THE Root_Agent SHALL menandai slot tersebut `failed`, melanjutkan slot berikutnya, dan melaporkan semua slot yang gagal beserta penyebabnya di akhir run.
11. WHEN pengguna menghentikan run saat pembangunan berlangsung, THE Backend_API SHALL menandai slot yang belum dibangun sebagai `skipped` dan mempertahankan item yang sudah dibuat.
12. WHEN semua slot terpilih selesai diproses, THE Root_Agent SHALL menerapkan Global_Filter bawaan yang disetujui, menjalankan review desain (Requirement 39), lalu menyampaikan ringkasan hasil kepada pengguna.
13. FOR ALL daftar Blueprint_Slot dan item Dashboard yang ada, Layout_Template SHALL menghasilkan layout yang berada di dalam grid, tidak tumpang tindih satu sama lain maupun dengan item yang ada, mempertahankan layout yang sudah ditentukan, dan identik untuk masukan yang sama (invariant dan determinisme).

### Requirement 38: KPI Card

**User Story:** Sebagai pengguna, saya ingin baris angka utama di bagian atas dashboard, sehingga kondisi bisnis terbaca dalam beberapa detik.

#### Acceptance Criteria

1. THE Dashboard_Store SHALL mendukung item KPI_Card yang berisi judul dan spesifikasi KPI: `query_id`, kolom nilai, kolom pembanding opsional, label pembanding opsional, format angka, dan arah nilai yang baik.
2. WHEN KPI_Card ditambahkan atau diubah, THE Backend_API SHALL memvalidasi bahwa kolom nilai dan kolom pembanding ada pada skema hasil query tersimpan dan bertipe numerik.
3. IF validasi spesifikasi KPI gagal, THEN THE Backend_API SHALL menolak perubahan dengan kode error `KPI_SPEC_INVALID` beserta kolom yang bermasalah.
4. WHEN KPI_Card dirender, THE Backend_API SHALL menghitung tanpa memanggil LLM: nilai, nilai pembanding, delta absolut, delta persen (kosong bila pembanding nol atau kosong), dan string terformat sesuai format angka dengan konvensi id-ID.
5. IF hasil query KPI_Card tidak berjumlah tepat satu baris, THEN THE Backend_API SHALL mengembalikan status render error `KPI_SHAPE` untuk item tersebut.
6. THE Filter_Engine SHALL menerapkan Global_Filter dan Cross_Filter pada KPI_Card dengan mekanisme yang sama seperti chart, termasuk status `invalid`, `stale`, dan tidak terpengaruh filter.
7. THE Canvas_Editor SHALL menampilkan KPI_Card sebagai kartu angka besar dengan label, nilai pembanding, dan delta yang diberi warna positif atau negatif sesuai arah nilai yang baik, serta mendukung aksi pindah, ubah ukuran, dan hapus.
8. THE Agent_Tools SHALL menyediakan tool `add_kpi` dan `update_kpi` yang tunduk pada approval gate dan pemeriksaan `base_version`.
9. FOR ALL nilai dan pembanding, delta absolut SHALL sama dengan nilai dikurangi pembanding, delta persen SHALL sama dengan delta absolut dibagi nilai absolut pembanding dikali 100 bila pembanding bukan nol, dan arah warna SHALL positif tepat ketika tanda delta searah dengan arah nilai yang baik (invariant).
10. FOR ALL nilai numerik dan format angka, mengekstrak angka dari string terformat KPI_Card dengan ekstraktor angka insight (Requirement 14.3) SHALL menghasilkan nilai yang sama dengan nilai asli setelah pembulatan sesuai format (round-trip).

### Requirement 39: Review Desain BI

**User Story:** Sebagai pengguna, saya ingin agent memeriksa dashboard terhadap prinsip BI, sehingga saya mendapat saran perbaikan tanpa perubahan otomatis.

#### Acceptance Criteria

1. WHEN Dashboard_Architect_Agent memanggil tool `review_dashboard`, THE Backend_API SHALL mengembalikan temuan Design_Rules untuk Dashboard aktif.
2. THE Design_Rules SHALL memeriksa minimal: jumlah item melebihi 12 (`TOO_MANY_VISUALS`), KPI_Card tanpa pembanding (`KPI_NO_COMPARISON`), KPI_Card tidak berada di baris teratas (`KPI_NOT_ON_TOP`), pie lebih dari 6 kategori (`PIE_TOO_MANY_SLICES`), chart tanpa judul (`MISSING_TITLE`), sumbu tanpa nama (`MISSING_AXIS_NAME`), chart multi-seri tanpa legenda (`MULTI_SERIES_NO_LEGEND`), Dashboard dengan kolom waktu tetapi tanpa chart tren (`NO_TIME_TREND`), dua chart atau lebih tanpa satu pun kolom Cross_Filter (`NO_CROSS_FILTER`), dan Business_Metric yang sama ditampilkan dengan format berbeda (`INCONSISTENT_METRIC_FORMAT`).
3. THE Design_Rules SHALL menghasilkan setiap temuan dengan kode, tingkat (`info` atau `warning`), id item terkait, pesan, dan saran perbaikan.
4. WHEN temuan review tersedia, THE Dashboard_Architect_Agent SHALL menambahkan penilaian apakah pertanyaan bisnis kunci pada Design_Brief sudah terjawab oleh item Dashboard.
5. THE Chat_Panel SHALL menampilkan temuan review sebagai kartu dengan aksi "Terapkan saran" per temuan yang mengirim permintaan perubahan sebagai pesan chat pengguna.
6. THE Backend_API SHALL tidak menerapkan saran review ke Dashboard tanpa permintaan atau persetujuan pengguna.
7. FOR ALL Dashboard, Design_Rules SHALL menghasilkan temuan yang identik terlepas dari urutan item, dan setiap id item pada temuan SHALL merupakan item yang ada di Dashboard tersebut (determinisme dan invariant).
