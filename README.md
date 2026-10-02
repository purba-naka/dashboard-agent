# Dashboard Studio Agent

Dashboard Studio Agent adalah aplikasi studio lokal untuk membuat dashboard BI
dari file CSV atau XLSX yang Anda unggah. Anda mendesain dashboard secara
hybrid: melalui percakapan dengan agent AI (multi-agent Google ADK) yang
membangun dashboard secara terarah, dan melalui canvas editor tempat Anda dapat
men-drag, me-resize, mengganti tipe chart, dan menghapus elemen secara manual.
Agent selalu mengetahui perubahan manual Anda.

Aplikasi ini berjalan sepenuhnya di komputer lokal, single-user, tanpa
autentikasi, dan tanpa layanan cloud. Data Anda disimpan di SQLite (atau
PostgreSQL lokal) dan folder lokal, sedangkan model LLM dikonfigurasi per agent
melalui LiteLLM (misalnya Gemini atau OpenAI).

<!-- prettier-ignore -->
> [!NOTE]
> Di luar cakupan saat ini: integrasi Google Sheets, cloud storage/deploy,
> multi-user/login, share link, dan BigQuery.

## Fitur utama

- **Workspace**: unit kerja yang menampung banyak dataset, model semantik, satu
  atau beberapa dashboard (multi-halaman), dan sesi chat.
- **Upload CSV/XLSX**: file dikonversi ke Parquet saat upload; data besar
  (lebih dari 100 MB, hingga 1 GiB per file) didukung lewat query lazy Polars.
- **Profiling data**: statistik per kolom (tipe, null, unik, min/max/mean,
  nilai terbanyak) dan deteksi kandidat relasi antar-dataset untuk JOIN.
- **Semantic layer**: draft metadata otomatis (deskripsi kolom, metrik bisnis,
  relasi) oleh Semantic Drafter, dikonfirmasi pengguna, dapat diekspor/impor
  sebagai YAML.
- **Pembuatan dashboard terarah**: agent Architect menyusun Design Brief dan
  Dashboard Blueprint (slot `kpi_row`, `trend`, `breakdown`, `composition`,
  `distribution`, `detail`) yang Anda setujui sebelum dibangun slot demi slot.
- **Chart ECharts**: KPI card, line, bar, pie, scatter, heatmap, dan waterfall;
  Chart_Spec divalidasi backend dan datanya diikat dari hasil query (bukan
  dari LLM).
- **Insight card berbasis bukti**: setiap insight tertaut ke SQL sumber dan
  hasil numerik eksekusi; dapat di-refresh.
- **Interaktif**: global filter, cross-filtering (klik elemen chart untuk
  memfilter chart lain), dan propagasi filter melalui relasi terkonfirmasi.
- **Hybrid editing**: undo/redo, versioning dashboard dengan patch event, dan
  agent sadar terhadap edit manual Anda di canvas.
- **Review desain BI**: agent memeriksa prinsip BI (KPI tanpa pembanding, pie
  terlalu banyak irisan, tanpa chart tren, dan lainnya) serta memberi saran
  tanpa mengubah apa pun tanpa persetujuan Anda.
- **Ekspor**: dashboard dapat diekspor ke PNG dan PDF.
- **Privasi**: Privacy Guard membatasi data (baris contoh terbatas) yang
  dikirim ke LLM; SQL hanya SELECT read-only via `pl.SQLContext`, tanpa
  eksekusi kode arbitrer.

## Arsitektur

Proyek terdiri dari dua aplikasi dan beberapa artefak pendukung:

```
agent-generate-dahsboard/
├── backend/                 # FastAPI + Google ADK + Polars (Python)
│   ├── main.py              # entrypoint: `python main.py`
│   ├── studio/
│   │   ├── agents/          # definisi agent ADK, prompt, tools, knowledge
│   │   ├── api/             # router REST: chat, workspaces, datasets,
│   │   │                    #   dashboards, queries, relations, semantic, SSE
│   │   ├── core/            # blueprint, chart_spec, KPI, filter, design
│   │   │                    #   rules, semantic, SQL rules, models_config
│   │   ├── data/            # ingestion CSV/XLSX → Parquet, engine, profiler
│   │   ├── events/          # event bus + SSE
│   │   └── store/           # SQLite/PostgreSQL, repo, migrasi
│   ├── alembic/             # migrasi skema (bila pakai PostgreSQL)
│   └── tests/               # pytest (unit, property, integration, eval)
├── frontend/                # Next.js 16 + React 19 + TypeScript (bun)
│   └── src/
│       ├── app/             # halaman utama & workspace `/w/[workspaceId]`
│       ├── components/      # chat, canvas, brief, datasets, filters,
│       │                    #   insights, relations, semantic, export, studio
│       └── lib/             # klien API, SSE, tema ECharts, state dashboard
├── data/                    # data runtime lokal (SQLite, uploads, Parquet)
├── docs/                    # rencana fitur (mis. waterfall & multi-halaman)
├── .kiro/specs/             # spesifikasi lengkap (requirements, design, tasks)
└── docker-compose.yml       # PostgreSQL 17 lokal untuk Metadata_Store
```

### Multi-agent

Backend meng-host satu Root_Agent orkestrator dengan sub-agent ADK. Model tiap
agent diatur terpisah melalui variabel `MODEL_*` (lihat "Konfigurasi model").

| Agent                  | Tugas utama                                                  |
| ---------------------- | ------------------------------------------------------------ |
| `Root_Agent`           | Orkestrator: chat, intent, dan delegasi ke sub-agent         |
| `Data_Profiler_Agent`  | Analisis skema, kualitas data, dan kandidat relasi           |
| `Dashboard_Architect`  | Design Brief, Blueprint, dan review desain BI                |
| `Query_Agent`          | Menghasilkan SQL dan mengeksekusinya lewat tool              |
| `Chart_Designer_Agent` | Memilih tipe chart dan menyusun Chart_Spec ECharts           |
| `Insight_Agent`        | Menghasilkan Insight_Card dari hasil query                   |
| `Blueprint_Builder`    | Non-LLM: membangun slot blueprint satu per satu via          |
|                        | Slot_Builder_Agent                                           |
| `Semantic_Drafter`     | Pengayaan LLM untuk draft model semantik (tanpa tool calling)|

### Tech stack

| Lapisan   | Teknologi                                                        |
| --------- | ---------------------------------------------------------------- |
| Frontend  | Next.js 16, React 19, TypeScript, ECharts 6, react-grid-layout, |
|           | react-markdown, jspdf + html-to-image (ekspor), Vitest           |
| Backend   | Python 3.10+, FastAPI, Google ADK 2.10, LiteLLM, Uvicorn         |
| Data      | Polars, PyArrow, fastexcel, sqlglot (validasi SQL), Parquet      |
| Storage   | SQLite (default) atau PostgreSQL 17 via Alembic                  |
| Streaming | SSE (Server-Sent Events) untuk respons chat dan event patch      |

## Menjalankan aplikasi

Anda memerlukan dua terminal: satu untuk backend, satu untuk frontend.

### Prasyarat

- Python 3.10 atau lebih baru (backend sudah menyertakan `.venv`).
- Bun 1.3.14 untuk manajemen paket frontend.
- API key LLM minimal satu provider (misalnya `GEMINI_API_KEY`), karena
  agent memerlukan model dengan tool calling.

### 1. Backend

1. Salin konfigurasi dan isi API key Anda:

   ```bash
   cp backend/.env.example backend/.env
   ```

2. Edit `backend/.env`: set minimal `MODEL_DEFAULT` dan kredensial provider,
   misalnya `GEMINI_API_KEY=<kunci Anda>`.
3. Aktifkan virtual environment lalu jalankan server:

   ```bash
   cd backend
   .venv/Scripts/activate      # Windows; di Linux/macOS: source .venv/bin/activate
   python main.py
   ```

   Backend berjalan di `http://127.0.0.1:8000`. Binding ke host non-loopback
   memunculkan peringatan karena API tidak memiliki autentikasi.

### 2. Frontend

```bash
cd frontend
bun install
bun run dev
```

Frontend berjalan di `http://localhost:3000`. Buka URL tersebut di browser,
buat workspace, unggah CSV/XLSX, dan mulai percakapan dengan agent.

### 3. PostgreSQL (opsional)

Default memakai SQLite di folder `data/`. Bila ingin PostgreSQL:

```bash
docker compose up -d
cd backend
alembic upgrade head
```

Lalu set di `backend/.env`:

```
DATABASE_URL=postgresql://studio:studio@127.0.0.1:5434/dashboard_studio
```

## Konfigurasi model

Nilai dalam format LiteLLM, misalnya `gemini/gemini-2.5-flash` atau
`openai/gpt-4o-mini`. Variabel per agent yang kosong memakai `MODEL_DEFAULT`.

| Variabel           | Agent yang memakainya           |
| ------------------ | -------------------------------- |
| `MODEL_DEFAULT`    | Cadangan semua agent             |
| `MODEL_ROOT`       | Root_Agent                       |
| `MODEL_PROFILER`   | Data_Profiler_Agent              |
| `MODEL_QUERY`      | Query_Agent                      |
| `MODEL_CHART`      | Chart_Designer_Agent             |
| `MODEL_INSIGHT`    | Insight_Agent                    |
| `MODEL_ARCHITECT`  | Dashboard_Architect_Agent        |
| `MODEL_SEMANTIC`   | Semantic_Drafter (tanpa tool)    |

<!-- prettier-ignore -->
> [!TIP]
> Untuk model lokal atau kustom yang gagal cek kemampuan tool calling, set
> `MODEL_SKIP_CAPABILITY_CHECK=1` di `backend/.env`.

Kredensial provider dibaca langsung oleh LiteLLM (misalnya `GEMINI_API_KEY`
atau `OPENAI_API_KEY`) di environment atau `backend/.env`.

<details>
<summary>Variabel runtime lainnya</summary>

- `HOST`, `PORT` — bind server (default `127.0.0.1:8000`).
- `DATA_DIR` — direktori data lokal (default `<repo>/data`).
- `DATABASE_URL` — kosong berarti SQLite; `postgresql://...` untuk Postgres.
- `CORS_ORIGINS` — origin frontend yang diizinkan, pisahkan dengan koma.
- `QUERY_TIMEOUT_S` — timeout eksekusi query, default 60 detik.
- `QUERY_WORKERS` — jumlah proses query worker (default: otomatis dari CPU).
- `QUERY_RUNNER_MODE` — `process` (default) atau `inline` (untuk test).
- `MAX_UPLOAD_BYTES` — batas upload; default dan minimum 1 GiB.
- `SSE_HEARTBEAT_SECONDS` — interval heartbeat SSE, default 15 detik.
- `SEMANTIC_CONTEXT_BUDGET` — anggaran karakter konteks semantik per agent
  (default 12000).
- `SEMANTIC_DRAFT_TIMEOUT_S` — batas waktu draft semantik LLM (default 60).

</details>

## Pengujian

Backend memakai pytest dengan marker; suite default mengecualikan `eval`,
`slow`, dan `live`:

```bash
cd backend
pytest                        # suite default
pytest -m eval                # evaluasi agent ADK dengan model mock
pytest -m slow                # performance test berat
pytest -m live                # end-to-end dengan LLM nyata (berbiaya)
```

Frontend memakai Vitest dan Testing Library:

```bash
cd frontend
bun test                      # unit & komponen
bun run lint                  # ESLint
bun run build                 # build produksi
```

## Alur penggunaan singkat

1. Buat workspace baru dari halaman utama.
2. Unggah satu atau beberapa file CSV/XLSX; backend melakukan profiling dan
   menyiapkan draft model semantik.
3. Tinjau dan konfirmasi relasi antar-dataset di panel Relations serta entri
   semantik di panel Semantic.
4. Ajukan pertanyaan atau minta dashboard; agent Architect menyusun Design
   Brief dan Blueprint berisi slot yang dapat Anda centang, ubah, atau revisi.
5. Setujui blueprint — agent membangun slot terpilih (KPI, chart, insight)
   sambil menyiarkan progres, lalu menjalankan review desain BI.
6. Sesuaikan hasilnya secara manual di canvas (drag, resize, ganti tipe chart,
   hapus) atau lanjutkan percakapan; gunakan global filter dan cross-filter
   untuk eksplorasi.
7. Ekspor dashboard ke PNG atau PDF dari Export Bar.

## Spesifikasi

Dokumen spesifikasi lengkap (requirements, design, dan task breakdown)
tersimpan di `.kiro/specs/dashboard-studio-agent/`, dan rencana fitur aktif di
folder `docs/`.
