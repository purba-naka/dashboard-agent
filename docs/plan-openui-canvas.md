# Rencana: Refactor canvas dashboard ke OpenUI

Dokumen ini adalah rencana refactor Canvas_Editor Dashboard Studio Agent agar
canvas dibangun dengan [OpenUI](https://www.openui.com/docs). Agent tetap
memahami data dan bisnis lewat pipeline yang ada, lalu menulis canvas sebagai
program OpenUI Lang. Tujuannya canvas yang terasa seperti dashboard Tableau:
layout container bersarang, tabel, slicer di canvas, dan cross-filter.

Baca bagian 0 sampai 3 sebelum menulis kode. Bagian itu berisi keputusan yang
sudah dikunci dalam sesi desain beserta alasannya. Jangan ubah keputusan tanpa
diskusi.

## 0. Untuk engineer yang mengerjakan

Bagian ini merangkum konteks repo yang wajib Anda tahu.

- Backend: FastAPI, Google ADK, LiteLLM, dan Polars di `backend/`. Frontend:
  Next.js 16 dan React 19 di `frontend/`, dikelola dengan `bun`.
- Next.js 16 punya breaking changes. Baca `frontend/AGENTS.md` sebelum
  menyentuh frontend.
- Spesifikasi produk ada di `.kiro/specs/dashboard-studio-agent/`. Nomor
  requirement (misalnya Req 24.1) merujuk ke sana.
- Semua tooling JavaScript memakai `bun` atau `bunx`. Tambah paket dengan
  `bun add` agar versi terkunci di lockfile. Jangan tulis versi tangan.
- Untuk API OpenUI, urutan sumber yang dipercaya: kode dan tipe di
  `node_modules/@openuidev/*` yang terpasang, lalu
  [repo OpenUI](https://github.com/thesysdev/openui), lalu docs. Bila berbeda,
  ikuti kode terpasang dan catat perbedaannya di dokumen ini.
- Bahasa produk adalah Indonesia.

## 1. Keputusan yang sudah dikunci

Tabel berikut adalah hasil sesi desain. Alasan tiap baris penting saat Anda
menemui kasus yang tidak tertulis di sini.

| # | Keputusan | Alasan |
|---|---|---|
| D1 | Program OpenUI Lang menjadi sumber kebenaran canvas. `items`, `layout` grid, dan Chart_Spec ECharts diganti | Layout dan komposisi bebas dari grid 12 kolom. |
| D2 | Pipeline agent tetap (Profiler, semantic model, Architect, Query, Chart_Designer, Insight). Angka hanya berasal dari query tersimpan lewat `Query(...)`. Tidak ada data inline | LLM tidak bisa mengarang angka. Prinsip C3 tetap berlaku. |
| D3 | Satu statement sama dengan satu op patch. Merge mengikuti semantik `mergeStatements` (`editMode`) | Undo/redo granular, approval, dan versi patch tetap jalan. |
| D4 | Library komponen custom (Zod) dengan props semantik. Chart tetap dirender ECharts, memakai ulang `chart-normalize`, `echarts-theme`, `KpiTile`, dan `InsightCard` | Hemat token, mudah divalidasi, tema dan export tidak berubah. |
| D5 | Agent menulis lewat tool `apply_canvas_patch(patch, base_version)`. Tanpa OpenUI Gateway. LLM tetap lewat LiteLLM | Approval gate, retry budget, dan `patch.applied` tidak dirombak. Gateway membuang opsi `editMode` dan `tools`. |
| D6 | Parse, merge, dan system prompt dikerjakan sidecar bun yang memakai `@openuidev/lang-core` asli. Validasi domain di Python. Spec library digenerate dari library frontend | Yang divalidasi backend identik dengan yang dirender browser. |
| D7 | Global filter dan cross-filter tetap state host. `toolProvider` menyisipkan filter aktif dan membatch semua `Query` ke satu `POST /render`. `$variable` hanya untuk interaksi tampilan | Logika filter, propagasi relasi, dan `filter_unaffected` yang sudah teruji tidak disentuh. Filter tetap persisten dan bisa di-undo. |
| D8 | Layout gaya Tableau: `HContainer` dan `VContainer` bersarang. User bisa drag batas tile untuk resize, reorder, dan pindah container. Semua aksi menjadi patch `source="user"` | LLM menulis struktur, bukan koordinat. Rapi di 1366 dan 1920 serta saat export. |
| D9 | Scope: paritas fitur sekarang ditambah `Table`/`Matrix` dan `Slicer` di canvas | Dua komponen ini paling menentukan rasa Tableau/Power BI dengan biaya kecil. |
| D10 | Migrasi satu arah. Chart yang gagal dikonversi menjadi `Unsupported`. Riwayat patch direset ke baseline | Pekerjaan user tidak hilang, cukup satu renderer. |
| D11 | Satu jalur tulis dengan allowlist komponen per agent. Evidence insight di tabel pendamping, di-key nama statement. Nama slot blueprint menjadi nama statement | Satu validator, peran agent tetap terpisah, progres blueprint tetap terlacak. |
| D12 | Tujuh irisan di balik flag dev `CANVAS_ENGINE=legacy\|openui`. Cutover terakhir menjalankan migrasi dan menghapus legacy | Tiap irisan bisa diverifikasi tanpa merusak aplikasi. Risiko terbesar diuji lebih dulu. |

Di luar scope, dan menjadi spec lanjutan: treemap, gauge, funnel, map,
drill-down, field wells (authoring manual), measure kalkulasi, format pane,
data source live, `Floating`, serta preview canvas per token.

## 2. Fakta yang sudah diverifikasi

Fakta di bagian ini dicek di kode repo dan di docs OpenUI per 27 Oktober 2026.
Cek ulang bila kode atau versi paket berubah.

### 2.1 OpenUI

- OpenUI Lang berorientasi baris: `nama = Ekspresi`, dengan argumen posisi.
  Parser membuang bagian invalid dan Renderer menampilkan sisanya
  ([OpenUI Lang](https://www.openui.com/docs/openui-lang)).
- Pola dashboard resmi adalah "generate once, then execute". Program berisi
  `Query("tool", args, default)`. Runtime di browser memanggil tool lewat prop
  `toolProvider` milik `<Renderer />` tanpa LLM. `$variable` yang berubah
  memicu fetch ulang query yang memakainya
  ([Architecture](https://www.openui.com/docs/openui-lang/architecture)).
- `generateSystemPrompt({ library, promptOptions })` ada di
  `@openuidev/lang-core`. Opsi `editMode: true` membuat model menulis hanya
  statement yang berubah. `mergeStatements(previous, patch)` menggabungkan per
  nama: nama sama diganti, nama baru ditambah, `nama = null` dihapus, statement
  yang tidak terjangkau dari `root` dibuang, `$variable` selalu dipertahankan
  ([Incremental Editing](https://www.openui.com/docs/openui-lang/incremental-editing)).
- Gateway membangun prompt sendiri dan membuang `editMode`, `inlineMode`, dan
  `tools`.
- Paket: `@openuidev/lang-core` (parser, prompt, tipe),
  `@openuidev/react-lang` (`defineComponent`, `createLibrary`, `Renderer`,
  `mergeStatements`), dan `@openuidev/cli` (`generate --spec`). Untuk backend
  Python, panduan resmi adalah mem-port perilaku dari JavaScript
  ([skill OpenUI](https://github.com/thesysdev/skills/blob/main/skills/openui/SKILL.md)).

#### Hasil verifikasi irisan 1 (`@openuidev/lang-core` 1.0.1)

Fakta berikut dicek di kode terpasang dan menggantikan asumsi sebelumnya.

- Versi terpasang: `@openuidev/lang-core` 1.0.1, `@openuidev/react-lang`
  1.0.1, dan `zod` 3.25.76 (lang-core mengimpor `zod/v4`, yang tersedia di
  3.25). `@openuidev/cli` tidak dipakai. Artefak library digenerate dengan
  `bun run openui:spec` dari `frontend/src/openui/schema.ts`.
- `lang-core` sendiri sudah framework-agnostic: `createLibrary`,
  `defineComponent`, `createParser`, `mergeStatements`, `generateSystemPrompt`,
  `tokenize`, `split`, `parseExpression`, dan `walkAST` diekspor dari sana.
- `ParseResult` memuat `root` (`ElementNode` dengan `typeName`, `props`
  bernama, `statementId`), `meta.errors` (kode `unknown-component`,
  `missing-required`, `type-mismatch`, `excess-args`, `no-root`, dan lainnya,
  lengkap dengan `statementId`), `meta.unresolved`, `meta.orphaned`, dan
  `queryStatements`.
- `mergeStatements(existing, patch, rootId = "root")` mengembalikan string.
  `nama = null` menghapus. Statement yang tidak terjangkau dari `root` dibuang,
  termasuk `Query` yang tak lagi dirujuk.
- `Renderer` tidak punya API refetch dari luar, tetapi `QueryManager` dibuat
  ulang setiap identitas `toolProvider` berubah
  (`useMemo(..., [toolProvider])`). Host memicu refetch dengan membuat
  `toolProvider` baru saat filter atau `data_version` berubah.
- Postinstall `lang-core` mengirim telemetri instalasi. Skrip itu dibiarkan
  terblokir oleh bun. Sidecar menyetel `OPENUI_TELEMETRY_DISABLED=1`.
- Prompt sidecar memakai `bindings: false` dan `builtinFunctions: false`
  (filter milik host, angka hanya dari query). Prompt bawaan masih menyebut
  `Mutation` dan "mock data". Aturan tambahan dari backend wajib melarang
  keduanya, dan validator menolaknya (V4).

### 2.2 Codebase

- Canvas: `frontend/src/components/canvas/Canvas.tsx` memakai react-grid-layout
  12 kolom, `echarts-for-react`, `KpiTile`, dan `InsightCard`. Data diambil
  lewat `POST /api/dashboards/{id}/render` setiap versi, filter, atau
  `renderKey` berubah.
- Kontrak: `DashboardContent {title, items, layout, global_filters, brief}` di
  `backend/studio/core/models.py` dan `frontend/src/lib/types.ts`. Op patch:
  `add_item`, `remove_item`, `set_item`, `set_layout`, `set_filters`,
  `set_title`, dan `set_brief`.
- Tool agent ada di `backend/studio/agents/tools/dashboard_tools.py`
  (`DASHBOARD_TOOL_NAMES`). Approval gate dan retry budget (`version_conflict`
  1+2, `chart_spec` 1+3) dijelaskan di docstring file itu.
- Validasi chart C1–C7 di `backend/studio/core/chart_spec.py`. Binding data di
  `bind_data`. KPI dihitung `compute_kpi` tanpa LLM.
- Render server di `backend/studio/data/render.py` (`render_items`):
  `effective_filters`, propagasi relasi, `filter_unaffected`, dan
  `item_status`.
- Layout blueprint deterministik di `backend/studio/core/layout_templates.py`
  (`place_slots`).
- Export klien di `frontend/src/components/export/client.ts` memakai
  `html-to-image` dan jsPDF, melewati elemen `data-export-hide`.
- Gate e2e G1–G6 di `e2e/tests/gate/gate.e2e.ts` membaca DOM canvas.

## 3. Arsitektur target

Bagian ini menjelaskan bentuk sistem setelah refactor. Contoh sintaks bersifat
ilustrasi. Tanda tangan komponen final ditentukan di irisan 1.

### 3.1 Program canvas

Satu halaman (Dashboard) menyimpan satu program. Contoh:

```text
root = VContainer([hdr, kpis, row_trend, tbl_detail])
hdr = Section("Ringkasan penjualan", "Q3 2026")
kpis = HContainer([kpi_rev, kpi_orders, slc_region])
q_kpi_rev = Query("render_kpi", {query: "q_01", value: "revenue", compare: "revenue_prev", format: "currency", good: "up"}, {})
kpi_rev = Kpi(q_kpi_rev, "Pendapatan")
q_trend = Query("render_query", {query: "q_02"}, {rows: []})
slot_trend = LineChart(q_trend, "month", ["revenue"], {crossFilter: "month"})
q_region = Query("render_query", {query: "q_03"}, {rows: []})
slot_breakdown = BarChart(q_region, "region", ["revenue"], {crossFilter: "region"})
row_trend = HContainer([slot_trend, slot_breakdown], [2, 1])
slc_region = Slicer("region", "in")
tbl_detail = Table(q_region, ["region", "revenue", "orders"])
ins_trend = Insight("trend", "Pendapatan naik", "Pendapatan Q3 naik 12%...", "q_02")
```

Aturan program:

- Komponen data selalu menerima statement `Query`. Tidak ada angka di program,
  kecuali teks insight yang diverifikasi backend.
- `Query` hanya memakai tool `render_query` (baris dan kolom) atau
  `render_kpi` (hasil `compute_kpi` yang sudah diformat). Argumen `query`
  wajib `query_id` milik workspace.
- Filter global dan cross-filter tidak muncul di program (D7).

### 3.2 Model penyimpanan

`DashboardContent` baru:

```python
class Statement(BaseModel):
    name: str
    source: str            # teks satu statement, apa adanya
    component: str         # hasil parse sidecar, misalnya "LineChart" atau "Query"
    refs: list[str]        # nama statement yang dirujuk

class DashboardContent(BaseModel):
    title: str
    statements: dict[str, Statement]   # urutan sisip dipertahankan
    global_filters: FilterSet
    brief: DesignBrief | None = None
```

- Op baru `set_statement {name, before: Statement | None, after: Statement | None}`
  menggantikan `add_item`, `remove_item`, `set_item`, dan `set_layout`. Inverse
  op cukup menukar `before` dan `after`.
- Statement yang terbuang karena tidak terjangkau dari `root` dicatat sebagai
  op hapus eksplisit di patch yang sama, agar undo memulihkannya.
- `set_filters`, `set_title`, dan `set_brief` tetap.
- `item_status` (`invalid`, `stale`) dihitung per statement `Query`, lalu
  diturunkan ke komponen yang merujuknya. Status tetap turunan, tidak
  disimpan.
- Evidence insight (`sql`, `evidence`, `matched_numbers`, `filters_snapshot`,
  `dataset_versions`) disimpan di tabel `insight_evidence (dashboard_id,
  statement_name, ...)` lewat migrasi `003_openui.sql` beserta versi alembic
  untuk PostgreSQL.

### 3.3 Sidecar OpenUI

Sidecar menjembatani Python ke parser resmi.

- Lokasi `backend/openui-sidecar/` (bun, `package.json` sendiri, dependensi
  `@openuidev/lang-core` dengan versi sama seperti frontend).
- Dijalankan sekali oleh lifespan FastAPI. Komunikasi JSON-RPC lewat
  stdin/stdout, satu permintaan per baris. Restart otomatis bila mati, dan
  timeout per permintaan.
- Metode:
  - `parse(patch)` mengembalikan statement beserta AST dan error parse.
  - `merge(previous, patch)` mengembalikan program hasil merge dan daftar
    statement yang ditambah, diganti, atau dihapus.
  - `prompt(options)` mengembalikan system prompt dari spec library dengan
    `editMode: true`. Hasilnya di-cache saat startup.
- Spec library digenerate dari `frontend/src/openui/library.ts` ke
  `frontend/src/openui/generated/spec.json` dengan `bunx @openuidev/cli
  generate --spec`, lalu dikomit. Sidecar membaca file itu.

### 3.4 Validasi patch di Python

`backend/studio/core/openui/validate.py` menggantikan `chart_spec.py`. Aturan
baru dinomori V1–V8 agar mudah dirujuk di error dan test.

| Kode | Aturan |
|---|---|
| V1 `PARSE_ERROR` | Sidecar melaporkan error parse. Patch ditolak seluruhnya, tidak ada statement yang dibuang diam-diam |
| V2 `UNKNOWN_COMPONENT` | Komponen tidak ada di library |
| V3 `FORBIDDEN_COMPONENT` | Komponen di luar allowlist agent pemanggil (D11) |
| V4 `UNKNOWN_QUERY` | `Query` merujuk `query_id` yang tidak ada atau milik workspace lain, atau tool selain `render_query` dan `render_kpi` |
| V5 `UNKNOWN_COLUMN` | Kolom pada props komponen (`x`, `y`, `crossFilter`, kolom tabel, kolom slicer) tidak ada di skema query |
| V6 `INVALID_REF` | Rujukan ke statement yang tidak ada setelah merge, atau siklus |
| V7 `CODE_VALUE` / `TOO_LARGE` | String berpola kode, ukuran patch lebih dari 64 KB, atau kedalaman berlebih (warisan C6 dan C7) |
| V8 `INSIGHT_NUMBER_MISMATCH` | Angka di teks `Insight` tidak cocok dengan evidence |

Pie maksimal 8 kategori tetap dicek saat render.

### 3.5 Render dan filter

Data tetap dihitung backend, sementara runtime OpenUI hanya memicu
pengambilan.

- `POST /api/dashboards/{id}/render` menerima
  `{queries: [{key, tool, args}], cross_filters}`. Key berupa nama statement
  `Query`.
- `render_query` menjalankan query tersimpan dengan `effective_filters` dan
  mengembalikan `{columns, rows, status, filter_unaffected}`. `render_kpi`
  mengembalikan `RenderedKpi`.
- `toolProvider` di frontend mengumpulkan semua panggilan dalam satu tick ke
  satu request, menyisipkan cross-filter dari state host, dan membagikan hasil
  per key.
- Perubahan filter host atau `data_version` memaksa semua `Query` fetch ulang.
  Mekanismenya diputuskan di irisan 3 berdasarkan API Renderer yang terpasang.
- Klik elemen chart memanggil handler host lewat React context untuk toggle
  cross-filter (Req 24.1). `Slicer` menulis ke global filter lewat command
  `set_global_filters`.

### 3.6 Komponen library

Library didefinisikan di `frontend/src/openui/library.ts`.

| Kelompok | Komponen |
|---|---|
| Layout | `VContainer(children, weights?)`, `HContainer(children, weights?)`, `Tabs`, `Section(title, subtitle?)` |
| Data | `LineChart`, `BarChart` (termasuk stack dan combo), `PieChart`, `ScatterChart`, `Heatmap`, `Waterfall`, `Kpi`, `Table`, `Matrix` |
| Filter | `Slicer(column, mode)` dengan mode `in` atau `date_range` |
| Teks | `Insight(type, title, text, query_id)` |
| Sistem | `Unsupported(reason)`, hanya dari migrasi dan tidak masuk allowlist agent |

- Komponen chart membangun option ECharts dari baris `render_query`, lalu
  memakai `normalizeChart`, `useChartTheme`, dan `colorFor`.
- Setiap tile membawa header seragam: judul, badge status (`Tidak valid`,
  `Data berubah`, `Cross_Filter`, `Hasil benar`), dan menu aksi dengan
  `data-export-hide`.
- Tinggi tile ditentukan per jenis komponen, bukan oleh user.

### 3.7 Agent

Pipeline pemahaman data tidak berubah. Hanya tahap akhir yang menulis program.

- Tool `apply_canvas_patch(patch: str, base_version: int)` menggantikan
  `add_chart`, `update_chart`, `remove_chart`, `update_layout`, `add_insight`,
  `update_insight`, `add_kpi`, dan `update_kpi`. `get_dashboard_state`
  mengembalikan program saat ini dan skema setiap `Query`. `undo_last`,
  `update_brief`, dan tool halaman tetap.
- Allowlist per agent:

| Agent | Komponen yang boleh ditulis |
|---|---|
| Dashboard_Architect_Agent, Blueprint_Builder | `root`, layout, `Section`, `Slicer` |
| Chart_Designer_Agent | `Query`, komponen chart, `Kpi`, `Table`, `Matrix` |
| Insight_Agent | `Insight` |

- Retry budget dipisah berdasarkan jenis error: V1–V7 memakai jatah
  `chart_spec`, `VERSION_CONFLICT` memakai jatah `version_conflict`.
- `place_slots` menghasilkan kerangka container yang merujuk nama slot
  (`slot_trend`, `slot_breakdown`, dan seterusnya). Chart_Designer mengisi nama
  itu. `blueprint.progress` dilacak per nama slot.
- `change_chart_type` dari menu canvas menjadi penulisan ulang deterministik
  satu statement (`source="user"`), menggantikan `chart_convert.py`.
- Prompt Chart_Designer dan Architect memuat system prompt dari sidecar
  ditambah aturan domain (tanpa data inline, satu `Query` per sumber, nama
  slot).

## 4. Dampak per modul

Tabel ini membantu memperkirakan review dan test yang terdampak.

| Modul | Perubahan |
|---|---|
| `backend/studio/core/models.py`, `api/schemas.py` | `DashboardContent` baru, op `set_statement`, `RenderRequest` dan `RenderResponse` baru |
| `backend/studio/core/chart_spec.py`, `chart_convert.py` | Diganti `core/openui/validate.py` dan `core/openui/rewrite.py` |
| `backend/studio/core/openui/` (baru) | Klien sidecar, validator V1–V8, converter legacy |
| `backend/openui-sidecar/` (baru) | Parse, merge, dan prompt dengan `lang-core` |
| `backend/studio/store/` | Apply dan undo per statement, migrasi `003_openui.sql`, tabel `insight_evidence` |
| `backend/studio/data/render.py` | Render per `Query` statement, bukan per item |
| `backend/studio/core/layout_templates.py`, `agents/blueprint_builder.py` | Output kerangka container |
| `backend/studio/agents/tools/dashboard_tools.py`, `agents/prompts/*.md`, `agents/knowledge/*.md` | Tool `apply_canvas_patch`, allowlist, prompt baru |
| `backend/tests/property`, `unit`, `integration`, `eval` | Strategi Hypothesis untuk program, test chart_spec dan contoh prompt diganti |
| `frontend/src/openui/` (baru) | Library, `toolProvider` dengan batching, context host |
| `frontend/src/components/canvas/` | `Canvas.tsx` diganti renderer OpenUI dan editor layout |
| `frontend/src/lib/types.ts`, `dashboard-state.ts`, `__fixtures__/patches.json` | Kontrak dan reducer per statement |
| `e2e/tests/gate/`, `e2e/scenarios/` | Locator dan pembacaan canvas baru |

## 5. Irisan pengerjaan

Kerjakan berurutan. Setiap irisan selesai bila semua kriteria terpenuhi dan
perintah verifikasi lulus: `pytest` di `backend/`, serta `bun run test`,
`bun run lint`, dan `bun run build` di `frontend/`.

### Irisan 1: Library, spec, dan sidecar

Irisan ini menguji asumsi paling berisiko lebih dulu.

1. Pasang `@openuidev/react-lang` dan `@openuidev/lang-core` di frontend
   dengan `bun add`. Catat versi di bagian 2.1.
2. Verifikasi di `node_modules`: bentuk AST parse, lokasi `mergeStatements`,
   opsi `generateSystemPrompt`, dan cara `Renderer` memaksa refetch. Perbarui
   catatan IMPORTANT di bagian 2.1.
3. Definisikan library di `frontend/src/openui/library.ts` dan generate
   `spec.json`.
4. Bangun `backend/openui-sidecar/` beserta klien Python dan lifecycle di
   lifespan.
5. Tulis test kesetaraan: sekumpulan program contoh di-parse sidecar dan
   `Renderer` frontend menghasilkan himpunan statement yang sama.

Selesai bila sidecar menjawab `parse`, `merge`, dan `prompt`, serta test
kesetaraan lulus. Bila parser tidak bisa dipakai dari sidecar, berhenti dan
diskusikan ulang D6.

### Irisan 2: Model penyimpanan dan converter

Irisan ini mengganti isi Dashboard_Store tanpa menyentuh UI.

1. Tambah `Statement`, `DashboardContent` baru, dan op `set_statement` di
   balik `CANVAS_ENGINE`.
2. Implementasikan apply, undo, dan redo per statement di `DashboardStore`,
   termasuk op hapus eksplisit untuk statement yang terbuang.
3. Tulis validator V1–V8 di `core/openui/validate.py`.
4. Tulis converter legacy: KPI dan insight 1:1, chart dari `chart_type` dan
   `series[].encode`, layout per baris `y` menjadi `HContainer`, sisanya
   `Unsupported`.
5. Tulis migrasi `003_openui.sql`, versi alembic, dan tabel
   `insight_evidence`.

Selesai bila property test lulus untuk round-trip apply-undo, replay patch,
konflik versi, dan converter atas fixture lama.

### Irisan 3: Renderer read-only

Irisan ini menampilkan program di canvas tanpa edit layout.

1. Implementasikan komponen data, layout, dan `Insight` dengan header tile
   seragam.
2. Implementasikan endpoint render baru dan `toolProvider` dengan batching.
3. Sambungkan cross-filter lewat context host dan refetch saat filter berubah.
4. Pastikan export PNG dan PDF serta tema light dan dark tetap benar.

Selesai bila dashboard hasil converter tampil setara dengan legacy di 1366 dan
1920 (bandingkan dengan `.shots`), cross-filter jalan, dan satu render memicu
satu request.

### Irisan 4: Agent

Irisan ini membuat agent menulis program.

1. Ganti tool mutasi dengan `apply_canvas_patch` beserta allowlist dan retry
   budget per jenis error.
2. Ubah `place_slots` dan `Blueprint_Builder` agar menghasilkan kerangka
   container.
3. Tulis ulang prompt Chart_Designer, Architect, dan Insight, termasuk contoh
   yang divalidasi test pengganti `test_chart_prompt_examples.py`.
4. Perbarui evalset ADK di `tests/eval/`.

Selesai bila alur blueprint end-to-end menghasilkan dashboard valid di
integration test, dan `pytest -m eval` tidak lebih buruk dari baseline legacy.

### Irisan 5: Edit layout manual

Irisan ini mengembalikan kemampuan merapikan canvas tanpa chat.

1. Drag batas antar tile mengubah `weights` container.
2. Drag tile untuk reorder di dalam container atau pindah ke container lain.
3. Hapus tile dan ganti tipe chart dari menu.
4. Setiap aksi menjadi satu patch `source="user"` yang bisa di-undo dan
   dilaporkan ke sesi agent seperti edit manual sekarang.

Selesai bila undo dan redo memulihkan setiap aksi di unit test dan uji manual.

### Irisan 6: Table, Matrix, dan Slicer

Irisan ini menambah komponen paritas D9.

1. `Table` dan `Matrix` dari `render_query`, dengan sort kolom di klien.
2. `Slicer` mode `in` (nilai dari query distinct per kolom) dan `date_range`,
   menulis `set_global_filters`.
3. Tambahkan ke allowlist dan prompt.

Selesai bila slicer mengubah semua tile yang terdampak dan tabel ikut
ter-filter.

### Irisan 7: Gate, cutover, dan pembersihan

Irisan ini menutup refactor.

1. Tulis ulang gate G1–G6 dan skenario scorecard untuk DOM baru, lalu
   jalankan `bun run gate` di `e2e/`.
2. Perbarui skrip screenshot `.shots`.
3. Jalankan migrasi pada salinan `data/studio.db` dan periksa hasilnya.
4. Hapus `CANVAS_ENGINE`, `Canvas.tsx` lama, react-grid-layout,
   `chart_spec.py`, dan op legacy.
5. Perbarui `README.md` dan `design.md`.

Selesai bila semua gate lulus dan tidak ada referensi ke jalur legacy.

## 6. Risiko

Risiko berikut sudah diketahui. Masing-masing punya titik keputusan.

- **API `lang-core` berbeda dari docs.** Ini dijawab di irisan 1 sebelum
  pekerjaan lain dimulai.
- **Refetch `Query` dari filter host.** Bila `Renderer` tidak punya cara
  resmi, alternatifnya me-remount dengan `key` atau menyuntikkan satu
  `$variable` sistem. Diputuskan di irisan 3.
- **Model salah menulis argumen posisi.** Error V1–V7 dikembalikan ke LLM
  lewat retry budget. Ukur di eval irisan 4 dengan model aktif (`MODEL_*`).
- **Escape string di argumen tool.** Patch berisi tanda kutip di dalam JSON
  function-call. Uji dengan teks insight yang mengandung kutip dan baris baru.
- **Statement terbuang saat `root` ditulis ulang.** Komponen bisa hilang tanpa
  sengaja. Respons tool menyebut statement yang terbuang agar agent sadar.
- **Latensi sidecar per patch.** Ukur di `tests/perf/` dan pertahankan proses
  tetap hidup.

## 7. Status implementasi (27 Oktober 2026)

Irisan 1 sampai 6 sudah dikerjakan di balik flag. Irisan 7 belum dikerjakan.

| Irisan | Status | Lokasi utama |
|---|---|---|
| 1 | Selesai | `frontend/src/openui/schema.ts`, `backend/openui-sidecar/`, `backend/studio/core/openui/sidecar.py` |
| 2 | Selesai | `core/models.py` (`CanvasStatement`, `SetStatementOp`, `ApplyCanvasPatchCommand`), `core/openui/validate.py`, `core/openui/convert.py`, `store/dashboard_store.py` |
| 3 | Selesai | `data/render.py` (`render_canvas`), `POST /dashboards/{id}/canvas/render`, `frontend/src/openui/library.tsx`, `components/canvas/OpenUICanvas.tsx` |
| 4 | Selesai, belum dievaluasi dengan LLM | `agents/tools/dashboard_tools.py` (`apply_canvas_patch:<peran>`), `core/openui/skeleton.py`, `agents/prompts/*_openui.md` |
| 5 | Selesai | `frontend/src/openui/program.ts` (pindah, lebar, relokasi, ganti tipe, hapus), resize batas di `HContainerView` |
| 6 | Selesai | `Table`, `Matrix`, `Slicer` di `library.tsx` |
| 7 | Belum | Gate e2e, `.shots`, migrasi DB pengguna, hapus legacy |

Cara mencoba: set `CANVAS_ENGINE=openui` di backend dan
`NEXT_PUBLIC_CANVAS_ENGINE=openui` di frontend. Dashboard legacy menampilkan
tombol **Konversi ke canvas OpenUI**. Dashboard yang sudah punya `statements`
selalu dirender OpenUI, terlepas dari flag.

Penyimpangan dari rencana:

- Tidak ada tabel `insight_evidence` dan migrasi `003_openui.sql`. Lampiran
  Insight_Card disimpan di `CanvasStatement.insight` di dalam content JSON,
  sehingga undo/redo ikut memulihkannya tanpa migrasi skema.
- Allowlist D11 tetap mengizinkan container untuk semua peran, karena edit mode
  OpenUI mewajibkan tile baru dirujuk dari induknya. Yang dibatasi adalah
  komponen data, Insight, dan Slicer.
- Tool agent memakai satu nama `apply_canvas_patch` dengan instance berbeda per
  peran (`layout`, `data`, `insight`, `slot`).

Celah yang diketahui:

- `review_dashboard` (Design_Rules) belum membaca canvas; temuan review kosong
  untuk dashboard OpenUI.
- Tombol refresh `InsightCard` memanggil endpoint insight legacy dan gagal
  untuk tile `Insight` canvas.
- Eval ADK (`pytest -m eval`) dan gate e2e belum dijalankan untuk mode
  OpenUI.

## Langkah berikutnya

Jalankan eval ADK dengan `CANVAS_ENGINE=openui` untuk mengukur kualitas patch
dari model aktif, tutup dua celah di atas, lalu kerjakan irisan 7.
