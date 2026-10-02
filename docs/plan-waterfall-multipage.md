# Rencana: Waterfall + Dashboard Multi-Halaman

Target: dashboard hasil generate bisa menyerupai template Power BI "Financial Analysis" (ZoomCharts). Isinya baris KPI di atas, bar+line combo, waterfall, dan beberapa halaman dengan navigasi di samping.

## Untuk agent yang mengerjakan

- Repo: backend Python (`backend/studio`, `uv`, `pytest`) dan frontend Next.js 16 (`frontend`, `bun`, `vitest`).
- Next.js 16 punya breaking changes. Baca `frontend/AGENTS.md` dan docs di `frontend/node_modules/next/dist/docs/` sebelum menulis kode frontend.
- Spec utama ada di `.kiro/specs/dashboard-studio-agent/` (requirements, design, tasks). Ikuti konvensi dan bahasanya (Indonesia).
- Verifikasi per fase:
  - `bun run test` dan `bun run build` di `frontend/`;
  - `uv run pytest` di `backend/`.
  - Di PowerShell, exit code 1 karena banner stderr `$ vitest` atau `$ next build` bukan kegagalan. Baca ringkasan hasilnya.
- Kerjakan berurutan: Fase 1, lalu Fase 2. Fase 3 dan 4 hanya bila diminta.

## 0. Kondisi saat ini (sudah diverifikasi)

- **Backend sudah mendukung waterfall** sebagai pola `bar` bertumpuk: seri `base` transparan + seri `delta`. Ini keputusan desain di `design.md:1085` dan `backend/studio/agents/knowledge/chart_selection.md:95-180`. Tidak perlu `chart_type` baru.
- **Frontend merusak waterfall.** `normalizeChart` (`frontend/src/lib/chart-normalize.ts`, fungsi `applyLabels`) dijalankan dengan contoh waterfall dari knowledge agent, hasilnya `{"baseColor":"#4e79a7","baseLabel":true,"legend":true}`. Rinciannya:
  - seri `base` diwarnai ulang oleh `colorFor()`;
  - labelnya tampil;
  - legend memuat entri "base".
  Akibatnya tampil sebagai stacked bar biasa.
- **Multi-halaman belum ada.** `design.md:1087` sengaja mengeluarkannya dari scope.

## 1. Keputusan desain inti: Halaman = Dashboard

| Opsi | Perubahan | Dipilih |
|---|---|---|
| A. Satu halaman = satu `Dashboard` di Workspace yang sama | Tidak menyentuh `DashboardContent`, ops, patch, undo, fixture | ✅ |
| B. `pages` di dalam `DashboardContent` (+ `item_page`) | Op baru, invariant baru, inversi undo, reducer frontend, `patches.json`, migrasi konten lama | ❌ |

Alasan memilih A:
- Backend sudah mendukung banyak dashboard per workspace: `DashboardRepo.list_by_workspace` mengurutkan dengan `created_at`, dan `DashboardRepo.delete` sudah ada.
- Reducer frontend sudah mengabaikan patch dashboard lain.
- Semantik Power BI juga sama: filter, undo, dan layout berlaku per halaman.

Konsekuensi yang diterima:
- `global_filters` berlaku per halaman. Sinkronisasi antarhalaman ditunda.
- Undo/redo berlaku per halaman.
- Urutan halaman = `created_at`. Kolom posisi/reorder ditunda.

## Fase 1: Waterfall tampil benar (frontend saja)

1.1 **Deteksi waterfall di `chart-normalize.ts`**
- Waterfall = ≥2 seri `bar` dengan `stack` sama, dan salah satunya punya `itemStyle.color === "transparent"`.
- Seri `base`:
  - pertahankan transparan (jangan ditimpa `colorFor`);
  - `label.show = false`, `tooltip.show = false`, `emphasis.disabled = true`, `silent: true`;
  - jangan diberi `markLine` acuan.
- Keluarkan `base` dari legend. Kalau hanya tersisa satu seri terlihat, hapus legend.
- Seri `delta` dipertahankan.

1.2 **Warna per langkah**
- Kolom opsional `step_kind` ∈ `total | up | down` dihitung di SQL. Normalizer membaca dimensi ini dari `dataset.dimensions` dan memasang `itemStyle.color` sebagai fungsi. Contoh: total gelap, down oranye, up hijau/biru, dari palet `echarts-theme.ts`.
- Fungsi aman karena dibuat di frontend, bukan dari JSON LLM. Validator backend menolak formatter fungsi.
- Kalau kolomnya tidak ada, pakai satu warna.

1.3 **Update knowledge agent**
- `backend/studio/agents/knowledge/chart_selection.md`: tambah `step_kind` di contoh SQL dan Chart_Spec waterfall.
- `backend/studio/agents/prompts/query.md:28`: sebut kolom `step_kind`.

1.4 **Tes**
- `frontend/src/lib/chart-normalize.test.ts`: base tetap transparan, tanpa label, tidak di legend; warna mengikuti `step_kind`.
- `backend/tests/unit/test_chart_prompt_examples.py` harus tetap lolos dengan contoh JSON yang baru.

## Fase 0 (ditutup oleh Fase 2): bug dashboard aktif tidak sinkron

Bila ada lebih dari satu dashboard:
- UI menampilkan `dashboards[0]`, yaitu yang tertua (`frontend/src/components/studio/WorkspaceStudio.tsx`, `loadDashboardIfNeeded`).
- Chat menargetkan dashboard yang **terakhir diubah** (`backend/studio/api/chat.py:_active_dashboard_id`, `backend/studio/agents/tools/dashboard_tools.py:resolve_dashboard`).

Akibatnya pengguna melihat halaman A sementara agent mengedit halaman B.

## Fase 2: Multi-halaman

### 2.1 Backend API
- `ChatRequest.dashboard_id: str | None` (`backend/studio/api/schemas.py` + `frontend/src/lib/types.ts`).
  - Divalidasi milik workspace.
  - Kalau kosong, pakai fallback lama.
- `api/chat.py`: tulis dashboard aktif ke state sesi **setiap giliran**. Saat ini `runner.ensure_session` (`agents/runner.py`) hanya mengisi `STATE_DASHBOARD_ID` saat sesi baru dibuat. Pakai mekanisme `extra_state` yang sudah ada.
- `DELETE /dashboards/{id}` di `api/dashboards.py`:
  - repo delete sudah ada;
  - tolak bila itu halaman terakhir workspace;
  - cek cascade FK patch_events/blueprints di `store/migrations/001_init.sql`, `002_semantic.sql`, dan alembic `0001_init.py`.
- Rename halaman cukup memakai command `set_title` yang sudah ada.

### 2.2 Agent tools (`backend/studio/agents/tools/dashboard_tools.py`)
- Tool baru, juga ditambahkan ke `DASHBOARD_TOOL_NAMES`:
  - `list_pages()` → `[{dashboard_id, title, item_count}]`.
  - `create_page(title)` → buat dashboard dan bind `STATE_DASHBOARD_ID`.
  - `switch_page(dashboard_id)` → validasi workspace, lalu bind state.
- `dashboard_state_for_llm` ikut menyertakan daftar judul halaman.
- `architect_tools._dashboard_id` memakai logika fallback yang sama dengan `resolve_dashboard`. Pastikan keduanya menghormati state yang di-bind.
- Prompt root/architect:
  - satu halaman = satu topik (Overview, Revenue, Expenses, Ratios, Table…);
  - KPI ringkasan hanya di Overview;
  - jangan membuat halaman tanpa permintaan pengguna atau Blueprint.
- Frontend perlu tahu bila ada halaman baru. Saat `patch.applied` datang untuk `dashboard_id` yang tidak dikenal, reload workspace detail.

### 2.3 Frontend
- `WorkspaceStudio.tsx`:
  - state `activeDashboardId` disinkronkan ke URL `?page=<id>`, dengan fallback `dashboards[0]`;
  - `loadDashboardIfNeeded` memakai id aktif;
  - Cross_Filter direset saat pindah halaman.
- Komponen baru `PageNav` (satu file) di kiri canvas:
  - `<nav aria-label="Halaman dashboard">` berisi tombol, dengan `aria-current="page"` untuk halaman aktif;
  - tombol "+ Halaman" memanggil `createDashboard` yang sudah ada;
  - rename inline lewat `set_title`;
  - hapus dengan konfirmasi.
- Ganti panel "Dashboard" di sidebar `frontend/src/components/workspaces/WorkspaceView.tsx` (±L194-232) dengan PageNav, supaya tidak ada dua daftar.
- `ChatPanel` mengirim `dashboard_id` aktif.
- PageNav diberi `data-export-hide`.

### 2.4 Ekspor
- PNG/PDF halaman aktif: tidak berubah.
- Opsional, "PDF semua halaman": iterasi halaman, render canvas, `toPng`, lalu `jsPDF.addPage` di `frontend/src/components/export/client.ts`. Canvas harus ter-mount, jadi halaman diganti berurutan di layar.

### 2.5 Tes
- Backend:
  - chat dengan `dashboard_id` workspace lain → error;
  - `create_page`/`switch_page` mengubah target `add_chart`;
  - DELETE halaman terakhir ditolak.
- Frontend:
  - pindah halaman memuat snapshot yang benar;
  - patch halaman lain tidak mengubah canvas;
  - `?page=` dipulihkan saat reload;
  - chat mengirim `dashboard_id`.

## Fase 3 (opsional): Blueprint multi-halaman

- `BlueprintSlot.page: str | None`. `finalize_blueprint` menjalankan `place_slots` per halaman.
- Saat disetujui, `activate_blueprint` membuat dashboard per halaman. Slot builder memanggil `switch_page` sebelum membangun slot halaman itu.
- Evaluasi ulang batas 16 slot.
- `BlueprintCard.tsx` mengelompokkan slot per halaman.
- File yang tersentuh: `core/blueprint.py`, `core/layout_templates.py`, `agents/tools/architect_tools.py`, `api/chat.py`, `BlueprintCard.tsx`.

## Fase 4 (opsional): gaya visual

Kartu KPI gelap dan aksen kuning lewat `frontend/src/lib/echarts-theme.ts` dan `frontend/src/components/canvas/canvas.module.css`. Cek kontras dengan `lib/contrast.ts`.

## Ditunda (YAGNI)
- Sinkronisasi filter antarhalaman.
- Reorder halaman (kolom `position` + migrasi).
- `chart_type: "waterfall"` di `change_chart_type`.
- Render ekspor offscreen.

## Pertanyaan terbuka (tanyakan ke pengguna sebelum Fase 2)
1. Apakah filter global per halaman bisa diterima?
2. Apakah perlu ekspor PDF semua halaman di Fase 2?
3. Apakah rencana ini dicatat sebagai Ekstensi v3 di `.kiro/specs/dashboard-studio-agent` (Requirement 40+, Task 36+)?
