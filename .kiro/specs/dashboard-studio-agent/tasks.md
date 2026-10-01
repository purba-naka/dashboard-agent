# Implementation Plan: Dashboard Studio Agent

## Overview

Implementasi dilakukan secara inkremental dan test-driven: scaffolding backend (Python FastAPI, `python main.py`) dan frontend (Next.js + bun) → modul fungsi murni di `backend/studio/core/` (masing-masing diikuti property test Hypothesis) → penyimpanan SQLite & repositori → Dashboard_Store berversi dengan undo/redo → ingestion CSV/XLSX → Parquet → Data_Engine + worker pool + SQL_Validator → propagasi Filter_Engine → REST API + SSE → Model_Gateway + agent ADK + tools + turn policy + runner → frontend (klien API/SSE, reducer state, Chat_Panel, Canvas_Editor, panel dataset/relasi, filter, insight, ekspor) → ADK eval → wiring akhir. Semua kode backend memakai Python, frontend memakai TypeScript. Nama file dan kontrak mengikuti design.md.

## Tasks

- [x] 1. Scaffolding backend
  - [x] 1.1 Buat struktur proyek backend dan dependensi pinned
    - Buat `backend/pyproject.toml` dengan dependensi versi exact (`==`, versi stabil terbaru saat implementasi): `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `python-dotenv`, `python-multipart`, `polars`, `pyarrow`, `fastexcel`, `sqlglot`, `aiosqlite`, `google-adk`, `litellm`, `python-ulid`; extras `[dev]`: `pytest`, `pytest-asyncio`, `hypothesis`, `xlsxwriter`, `httpx`, `psutil`
    - Buat kerangka paket `backend/studio/` beserta subpaket `api/`, `core/`, `data/`, `store/`, `store/migrations/`, `agents/`, `agents/tools/`, `agents/prompts/`, `events/` (file `__init__.py`)
    - Buat `backend/tests/{property,unit,integration,eval}/` dan `backend/tests/conftest.py` dengan profil Hypothesis default `max_examples=100` serta fixture `tmp_data_dir`
    - Buat `backend/.env.example` (HOST, PORT, DATA_DIR, CORS_ORIGINS, MODEL_DEFAULT, MODEL_ROOT, MODEL_PROFILER, MODEL_QUERY, MODEL_CHART, MODEL_INSIGHT) dan `.gitignore` root yang mengecualikan `data/`, `.env`, `node_modules/`, `.next/`
    - _Requirements: 28.3, 28.5, 30.1_

  - [x] 1.2 Implementasikan konfigurasi, error envelope, app factory, dan entrypoint `main.py`
    - `studio/config.py`: `Settings` (pydantic-settings) dengan default `HOST=127.0.0.1`, `PORT=8000`, `DATA_DIR` absolut `<repo>/data`, `CORS_ORIGINS=http://localhost:3000`
    - `studio/api/errors.py`: kelas dasar `StudioError(code, message, details, http_status)` tanpa dependensi FastAPI di konstruktor, plus fungsi `register_error_handlers(app)` yang memetakan ke envelope `{"error": {code, message, details}}`
    - `studio/app.py`: `create_app(settings)` dengan CORS hanya untuk `CORS_ORIGINS`, endpoint `GET /api/health`, dan lifespan kosong yang akan diisi kemudian
    - `backend/main.py`: load `.env`, bangun `Settings`, cetak peringatan ke stderr bila `HOST` bukan alamat loopback, lalu `uvicorn.run(create_app(...), host, port)`
    - _Requirements: 29.1, 29.2, 29.4_

  - [x]* 1.3 Tulis unit test konfigurasi dan keamanan lokal
    - `tests/unit/test_config_security.py`: host default `127.0.0.1`, peringatan muncul untuk host non-loopback (mis. `0.0.0.0`) dan tidak muncul untuk `127.0.0.1`/`localhost`/`::1`, CORS menolak origin di luar `CORS_ORIGINS`, envelope error konsisten
    - _Requirements: 29.1, 29.2, 29.4_

- [x] 2. Scaffolding frontend
  - [x] 2.1 Buat proyek Next.js dengan bun dan konfigurasi Vitest
    - Inisialisasi `frontend/` (Next.js App Router, TypeScript) dengan bun; tambahkan dependensi exact (`bun add --exact`): `echarts`, `echarts-for-react`, `react-grid-layout`, `html-to-image`, `jspdf`; dev: `vitest`, `jsdom`, `fast-check`, `@testing-library/react`, `@testing-library/user-event`, `@types/react-grid-layout`
    - Buat `vitest.config.ts` (environment jsdom), skrip `test` = `vitest --run`
    - Buat route kerangka `src/app/page.tsx` dan `src/app/w/[workspaceId]/page.tsx`, serta `src/lib/types.ts` berisi tipe yang mencerminkan model design (Workspace, Dataset, ColumnProfile, Relation, ChartSpec, ChartItem, InsightItem, LayoutRect, DashboardContent, Predicate, Op, PatchEvent, DashboardSnapshot, event SSE)
    - _Requirements: 12.4, 17.1, 30.3_

- [x] 3. Model domain, identifier, dan perbandingan skema
  - [x] 3.1 Definisikan model domain Pydantic bersama
    - `studio/core/models.py`: `LogicalType`, `ColumnRole`, `ColumnInfo`, `ColumnProfile`, `Predicate`/`FilterSet` alias, `ChartSpec`, `ChartItem`, `InsightItem`, `EvidenceTable`, `NumberMatch`, `LayoutRect` (w, h ≥ 1, grid 12 kolom), `DashboardContent` (validator invariant `keys(layout) == keys(items)`), `Op` (discriminated union: `add_item`, `remove_item`, `set_item`, `set_layout`, `set_filters`, `set_title`), `PatchEvent`, `QueryResult`
    - Modul ini tidak boleh mengimpor FastAPI atau ADK
    - _Requirements: 13.1, 14.5, 18.4, 22.1_

  - [x] 3.2 Implementasikan `studio/core/identifiers.py`
    - `normalize_columns(names) -> (normalized, mapping)`: trim, ganti kosong dengan `column_{i}`, dedupe dengan sufiks `_2`, `_3`, idempoten
    - `make_table_name(source_name, existing) -> str`: identifier `^[a-z_][a-z0-9_]*$`, bukan keyword SQL, unik terhadap `existing`
    - `sanitize_filename(name) -> str`: ambil basename setelah memisah `/` dan `\`, hapus `..`, karakter kontrol, dan `<>:"|?*`; kosong → `upload`; prefiks ULID; helper `safe_join(workspace_dir, name)` memverifikasi `resolve().is_relative_to(workspace_dir)`
    - `check_extension(name)`: hanya `.csv`/`.xlsx` (case-insensitive), selain itu `UnsupportedFormat` yang menyebut format yang didukung
    - _Requirements: 2.4, 2.5, 10.1, 29.5_

  - [x]* 3.3 Tulis property test normalisasi nama kolom
    - **Property 1: Normalisasi nama kolom menghasilkan nama unik dan tidak kosong**
    - File `tests/property/test_normalize_columns.py`
    - **Validates: Requirements 2.5**

  - [x]* 3.4 Tulis property test gerbang format file
    - **Property 2: Gerbang format file**
    - File `tests/property/test_check_extension.py`
    - **Validates: Requirements 2.4**

  - [x]* 3.5 Tulis property test sanitasi nama file
    - **Property 3: Sanitasi nama file mengurung file di folder workspace**
    - File `tests/property/test_sanitize_filename.py` (generator memuat `..`, `/`, `\`, path absolut, karakter kontrol, unicode)
    - **Validates: Requirements 29.5**

  - [x]* 3.6 Tulis property test nama tabel Dataset
    - **Property 4: Nama tabel Dataset unik dan valid**
    - File `tests/property/test_make_table_name.py`
    - **Validates: Requirements 10.1**

  - [x] 3.7 Implementasikan `studio/core/schema_diff.py`
    - `types_compatible(old, new)` dan `diff_schemas(old, new) -> SchemaDiff{missing, added, changed}` dengan `is_empty`
    - _Requirements: 26.1, 26.3, 26.4_

  - [x]* 3.8 Tulis property test perbandingan skema
    - **Property 34: Perbandingan skema re-upload**
    - File `tests/property/test_schema_diff.py`
    - **Validates: Requirements 26.3, 26.4**

- [x] 4. Resolusi model dan Privacy_Guard
  - [x] 4.1 Implementasikan `studio/core/models_config.py`
    - `AGENT_ENV` dan `resolve_models(env)`: nilai kosong/whitespace dianggap tidak diset; fallback `MODEL_DEFAULT`; `MissingModelConfig(missing_vars)` menyebut tepat nama variabel yang hilang
    - _Requirements: 9.2, 9.3, 9.4_

  - [x]* 4.2 Tulis property test resolusi model
    - **Property 11: Resolusi model per agent**
    - File `tests/property/test_models_config.py`
    - **Validates: Requirements 9.2, 9.3, 9.4**

  - [x] 4.3 Implementasikan `studio/core/privacy.py`
    - `PrivacySettings(sample_rows=5, no_samples=False)`, `build_dataset_context(...)` (hanya nama tabel, skema, Column_Profile, `sample_rows[:n]`; mode `no_samples` menghapus sample dan `top_values` kolom string), `truncate_result(result, limit=200)` → `{columns, rows, row_count, truncated}`
    - _Requirements: 27.1, 27.2, 27.3, 27.4, 27.5, 27.6_

  - [x]* 4.4 Tulis property test batas payload privasi
    - **Property 35: Batas payload privasi**
    - File `tests/property/test_privacy.py`
    - **Validates: Requirements 27.1, 27.3, 27.4, 27.5, 27.6**

- [x] 5. Patch dan riwayat undo/redo (fungsi murni)
  - [x] 5.1 Implementasikan `studio/core/patches.py`
    - `resolve_command(content, command) -> list[Op]` untuk semua command di tabel design (`add_chart`, `add_insight`, `update_chart`, `change_chart_type`, `update_insight`, `remove_item`, `set_layout`, `set_global_filters`, `set_title`), termasuk layout default "baris kosong pertama" dan snapshot `before`
    - `apply_ops` (murni, memvalidasi prasyarat, raise `InvalidOp`), `invert_ops` (urutan dibalik), `replay(initial, history)`
    - _Requirements: 17.5, 18.2, 18.7, 19.4_

  - [x] 5.2 Implementasikan `studio/core/history.py`
    - Logika stack undo/redo berbasis patch id sesuai state diagram design: patch normal push ke undo & kosongkan redo; undo pop dari undo → push ke redo; redo pop dari redo → push patch redo ke undo; `can_undo`/`can_redo`
    - _Requirements: 19.1, 19.2, 19.3_

  - [x]* 5.3 Tulis property test round-trip apply → undo
    - **Property 23: Round-trip apply → undo**
    - File `tests/property/test_patches_undo.py`; generator `dashboard_contents()` dan `valid_commands(content)` di `tests/property/strategies.py`
    - **Validates: Requirements 19.1, 19.4**

  - [x]* 5.4 Tulis property test round-trip apply → undo → redo
    - **Property 24: Round-trip apply → undo → redo**
    - File `tests/property/test_patches_redo.py`
    - **Validates: Requirements 19.2, 19.5**

- [x] 6. Filter_Engine: predikat dan propagasi
  - [x] 6.1 Implementasikan `studio/core/filters.py`
    - `Predicate` (frozen; `date_range` inklusif dengan ujung opsional, `in` dengan `frozenset`), `normalize_filters` (dedupe, urutan kanonik), `to_expr`, `apply_predicates(lf, preds)` (konjungsi)
    - _Requirements: 22.1, 22.2, 22.5, 22.6, 22.7, 24.2_

  - [x]* 6.2 Tulis property test soundness filter
    - **Property 27: Soundness filter**
    - File `tests/property/test_filter_soundness.py`; buat generator bersama `tables()`, `filter_sets()`, dan `relation_graphs()` (2–6 tabel, kolom kunci berbagi domain nilai kecil, siklus, status acak) di `tests/property/strategies_data.py`
    - **Validates: Requirements 22.5**

  - [x]* 6.3 Tulis property test idempotence filter
    - **Property 28: Idempotence filter**
    - File `tests/property/test_filter_idempotence.py`
    - **Validates: Requirements 22.6**

  - [x] 6.4 Implementasikan `studio/core/propagation.py`
    - `RelationGraph` (hanya Confirmed_Relation, urutan kanonik tabel lalu relation_id), `plan_propagation` (BFS per sumber, setiap tabel paling banyak sekali per sumber), `materialize(frames, filter_set, plan)` (rantai semi-join dari predikat langsung sumber), `affected_tables`, `is_filter_unaffected(tables_used, affected)`
    - _Requirements: 23.1, 23.2, 23.3, 23.4, 23.5_

  - [x]* 6.5 Tulis property test confluence filter
    - **Property 29: Confluence filter**
    - File `tests/property/test_filter_confluence.py`; memakai `relation_graphs()` dari `strategies_data.py`
    - **Validates: Requirements 22.7**

  - [x]* 6.6 Tulis property test invariant semi-join
    - **Property 30: Invariant semi-join propagasi**
    - File `tests/property/test_propagation_semijoin.py` (bandingkan dengan referensi himpunan Python murni)
    - **Validates: Requirements 23.1, 23.5**

  - [x]* 6.7 Tulis property test cakupan dan terminasi propagasi
    - **Property 31: Cakupan dan terminasi propagasi**
    - File `tests/property/test_propagation_coverage.py`
    - **Validates: Requirements 23.2, 23.3, 23.4**

- [x] 7. SQL_Validator (aturan sqlglot)
  - [x] 7.1 Implementasikan `studio/core/sql_rules.py`
    - `analyze_sql(sql, tables, confirmed_relations) -> SqlAnalysis` menerapkan aturan V1–V6 berurutan (berhenti pada pelanggaran pertama) dengan kode `PARSE_ERROR`, `MULTIPLE_STATEMENTS`, `NOT_SELECT` (menyebut jenis statement), `FORBIDDEN_STATEMENT`, `FORBIDDEN_SOURCE`, `UNKNOWN_TABLE` (+ daftar tabel), `UNCONFIRMED_JOIN` (+ pasangan kolom)
    - Resolusi alias/CTE ke tabel dasar melalui scope sqlglot; self-join diizinkan; CROSS/comma join antar-Dataset berbeda ditolak
    - Hasil: `tables_used`, `relations_used`, dan `lineage {output_col: (table, column) | None}`
    - _Requirements: 7.6, 7.7, 10.3, 10.4, 14.6_

  - [x]* 7.2 Tulis property test gerbang statement SQL
    - **Property 12: Gerbang statement SQL**
    - File `tests/property/test_sql_statement_gate.py`; generator `select_queries()` dan `forbidden_statements()` dari template AST sqlglot; pastikan eksekutor (mock) tidak pernah dipanggil untuk SQL yang ditolak
    - **Validates: Requirements 10.3, 10.4**

  - [x]* 7.3 Tulis property test JOIN hanya melalui Confirmed_Relation
    - **Property 13: JOIN hanya melalui Confirmed_Relation**
    - File `tests/property/test_sql_joins.py`; generator `join_queries(relations)` dengan alias dan CTE
    - **Validates: Requirements 7.6, 7.7, 14.6**

- [x] 8. Chart_Spec_Validator, binding, dan konversi tipe
  - [x] 8.1 Implementasikan `studio/core/chart_spec.py`
    - `validate_chart_spec(spec, result_schema) -> ChartSpec` dengan aturan C1–C7 dan kode `UNKNOWN_KEY`, `INLINE_DATA`, `UNSUPPORTED_SERIES`, `UNKNOWN_COLUMN` (menyebut kolom), `AXIS_STRUCTURE`, `CODE_VALUE`, `TOO_LARGE` melalui `ChartSpecError(code, path, detail)`
    - `bind_data(spec, result) -> dict`: tambahkan `option.dataset = {dimensions, source}` (date/datetime ISO-8601) tanpa mengubah key lain
    - _Requirements: 12.2, 12.3, 13.1, 13.2, 13.3, 13.4, 13.6_

  - [x]* 8.2 Tulis property test validasi Chart_Spec
    - **Property 15: Validasi Chart_Spec**
    - File `tests/property/test_chart_spec_validation.py`; generator `chart_specs(schema)` dan mutator `inject_inline_data`, `inject_unknown_column`, `inject_code_string` di `tests/property/strategies_chart.py`
    - **Validates: Requirements 12.2, 13.1, 13.2, 13.3, 13.4**

  - [x]* 8.3 Tulis property test round-trip Chart_Spec
    - **Property 16: Round-trip Chart_Spec**
    - File `tests/property/test_chart_spec_roundtrip.py`
    - **Validates: Requirements 13.6**

  - [x]* 8.4 Tulis property test binding data
    - **Property 17: Binding data mengikat hasil query tanpa mengubah desain**
    - File `tests/property/test_bind_data.py`
    - **Validates: Requirements 12.3**

  - [x] 8.5 Implementasikan `studio/core/chart_convert.py`
    - `convert_chart_type(spec, new_type, result_schema)`: bar↔line mempertahankan `encode`; ke pie `itemName = x`, `value = y[0]`, sumbu dihapus; ke scatter butuh dua measure numerik atau `INCOMPATIBLE_TYPE`; hasil selalu divalidasi ulang dengan `validate_chart_spec`
    - _Requirements: 17.3_

  - [x]* 8.6 Tulis property test konversi tipe chart
    - **Property 18: Konversi tipe chart menghasilkan spec valid**
    - File `tests/property/test_chart_convert.py`
    - **Validates: Requirements 17.3**

- [x] 9. Verifikasi angka insight, kandidat relasi, dan status turunan
  - [x] 9.1 Implementasikan `studio/core/numbers.py`
    - `extract_numbers` (format id-ID dan en, persen, minus, sufiks `rb/ribu/jt/juta/M/miliar/T/triliun`, token ambigu dengan beberapa interpretasi), `allowed_values(evidence)`, `format_number(v, decimals, scale)` id-ID, `verify_insight_numbers(text, evidence) -> list[NumberMatch]` atau `InsightNumberMismatch(unmatched)`
    - `check_insight_type(insight_type, tables_used, relations_used)`: `cross_dataset_correlation` hanya bila `tables_used ≥ 2` dan `relations_used` tidak kosong
    - _Requirements: 14.3, 14.4, 14.6_

  - [x]* 9.2 Tulis property test verifikasi angka insight
    - **Property 19: Verifikasi angka insight**
    - File `tests/property/test_insight_numbers.py`
    - **Validates: Requirements 14.3, 14.4**

  - [x] 9.3 Implementasikan `studio/core/relations.py`
    - `candidate_key(a, b)` (pasangan tak berurut kanonik `tA.cA|tB.cB`), `cardinality(unique_a, unique_b)`, `score_candidates(tables, profiles, rejected, overlap_fn)`: tipe kompatibel, nama sama setelah normalisasi atau pola `{tabel}_id ↔ id`, `overlap_pct ≥ 50`, kecualikan `rejected`
    - _Requirements: 7.1, 7.2, 7.5_

  - [x] 9.4 Implementasikan `studio/core/status.py`
    - `item_status(item, query_meta, confirmed_relation_ids, dataset_versions) -> {invalid, stale}` sesuai aturan design (invalid ⇔ `relations_used ⊄ confirmed`; stale ⇔ `data_version` lebih baru dari `item.dataset_versions`)
    - _Requirements: 7.8, 26.2_

  - [x]* 9.5 Tulis property test status invalid turunan
    - **Property 14: Status invalid turunan dari relasi**
    - File `tests/property/test_item_status.py`
    - **Validates: Requirements 7.8**

- [x] 10. Checkpoint - Modul inti murni
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Penyimpanan lokal dan event bus
  - [x] 11.1 Implementasikan `studio/store/db.py` dan migrasi SQLite
    - `store/migrations/001_init.sql` persis sesuai skema design (termasuk `owner_id`, `PRAGMA journal_mode = WAL`, `foreign_keys = ON`, constraint CHECK dan UNIQUE)
    - `db.py`: koneksi `aiosqlite` ke `DATA_DIR/studio.db` (path absolut), runner migrasi berbasis tabel `schema_migrations`, helper transaksi
    - _Requirements: 28.1, 28.4, 29.3_

  - [x] 11.2 Implementasikan `studio/store/repos.py`
    - Repositori Workspace (create/list/get/rename/delete), Upload, Dataset (insert, update file & `data_version` saat re-upload, privacy toggle), DatasetProfile, Relation (upsert kandidat by `candidate_key`, confirm, reject, delete → status `deleted`, daftar rejected keys), Query (simpan SQL + snapshot ≤ 1.000 baris), Dashboard, PatchEvent, ChatSession, Proposal
    - Semua timestamp ISO-8601 UTC, id ULID
    - _Requirements: 1.1, 1.3, 1.5, 4.2, 7.4, 7.5, 10.7, 14.5, 28.1, 29.3_

  - [x]* 11.3 Tulis unit test repositori
    - `tests/unit/test_repos.py`: CRUD, cascade saat hapus Workspace, `owner_id = 'local'`, UNIQUE `table_name` per workspace, status relasi, snapshot query terpotong 1.000 baris
    - _Requirements: 1.1, 1.3, 1.4, 7.4, 7.5, 10.7, 29.3_

  - [x] 11.4 Implementasikan `studio/events/bus.py` dan `studio/events/sse.py`
    - Pub/sub in-process per workspace dengan sequence id monoton dan buffer ring untuk `Last-Event-ID`; formatter SSE `id/event/data`
    - _Requirements: 16.3, 16.5, 18.3_

  - [x]* 11.5 Tulis unit test event bus dan formatter SSE
    - `tests/unit/test_events.py`: urutan sequence, replay sejak `Last-Event-ID`, format `event: <type>\ndata: <json>\n\n`
    - _Requirements: 16.3, 16.5_

- [x] 12. Dashboard_Store berversi
  - [x] 12.1 Implementasikan `studio/store/dashboard_store.py`
    - `DashboardStore.get/apply/undo/redo/patches_since` sesuai design: `asyncio.Lock` per `dashboard_id`, cek `base_version == version` (tolak lebih lama maupun lebih baru dengan `VersionConflict(current_version)`), resolve command → ops, validasi (Chart_Spec_Validator terhadap skema query tersimpan, verifier angka insight, `check_insight_type`), `apply_ops`, `invert_ops`, insert `patch_events` + `UPDATE ... WHERE version = ?` dalam satu transaksi, update stack undo/redo, publish `patch.applied` setelah commit
    - `NOTHING_TO_UNDO`/`NOTHING_TO_REDO`; `DashboardSnapshot` memuat `can_undo`, `can_redo`, dan `item_status` dari `core/status.py`
    - Sediakan hook `on_user_edit(summary)` (dipanggil bila `source == "user"`) yang nantinya dihubungkan ke sesi ADK
    - _Requirements: 17.5, 18.1, 18.2, 18.3, 18.4, 18.5, 18.8, 19.1, 19.2, 19.3, 20.3_

  - [x]* 12.2 Tulis property test versi bertambah tepat satu
    - **Property 20: Versi Dashboard bertambah tepat satu per patch sukses**
    - File `tests/property/test_dashboard_store_versions.py` memakai `hypothesis.stateful.RuleBasedStateMachine` (command valid, tidak valid, basi, undo, redo)
    - **Validates: Requirements 18.2, 18.8**

  - [x]* 12.3 Tulis property test replay riwayat
    - **Property 21: Replay riwayat merekonstruksi state**
    - File `tests/property/test_dashboard_store_replay.py`
    - **Validates: Requirements 18.4, 18.7**

  - [x]* 12.4 Tulis property test base version basi ditolak
    - **Property 22: Base version basi ditolak**
    - File `tests/property/test_dashboard_store_conflict.py`
    - **Validates: Requirements 18.5, 20.3**

  - [x]* 12.5 Tulis property test patch baru mengosongkan redo
    - **Property 25: Patch baru mengosongkan redo**
    - File `tests/property/test_dashboard_store_redo_clear.py`
    - **Validates: Requirements 19.3**

  - [x]* 12.6 Buat generator fixture patch lintas bahasa
    - Skrip `backend/tests/fixtures/gen_patch_fixtures.py` yang membangkitkan `frontend/src/lib/__fixtures__/patches.json` berisi `{initial, patches, expected}` dari `core/patches.py` untuk setiap jenis op dan urutan undo/redo
    - _Requirements: 30.3_

- [x] 13. Ingestion CSV/XLSX → Parquet
  - [x] 13.1 Implementasikan `studio/data/csv_reader.py`
    - Validasi encoding UTF-8 streaming (BOM diterima) → `ParseError(cause="encoding", line)`; inferensi skema `pl.scan_csv(infer_schema_length=10_000, try_parse_dates=True)`; pemetaan ke 6 tipe logis; normalisasi header via `normalize_columns`; iterator batch 100.000 baris; pelebaran tipe integer→float→string dengan satu kali ulang; `locate_bad_row()` dengan modul `csv` stdlib; file kosong/hanya header → `ParseError(cause="empty")`
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 5.2_

  - [x] 13.2 Implementasikan `studio/data/parquet_writer.py`
    - Penulis batch `pyarrow.parquet.ParquetWriter` (zstd) ke `*.parquet.partial`, `finalize()` dengan `os.replace`, `abort()` menghapus file parsial; helper `write_parquet`/`read_parquet`
    - _Requirements: 4.1, 5.2, 5.5_

  - [x]* 13.3 Tulis property test round-trip Parquet
    - **Property 6: Round-trip Parquet**
    - File `tests/property/test_parquet_roundtrip.py` (memakai `tmp_path`)
    - **Validates: Requirements 4.4**

  - [x] 13.4 Implementasikan `studio/data/xlsx_reader.py`
    - `list_sheets(path)` via `fastexcel` (error → `UnreadableWorkbook`), baca sheet dengan `pl.read_excel(engine="calamine")` untuk ≤ 100 MB dan per blok baris `load_sheet(skip_rows, n_rows)` untuk file lebih besar, sheet tanpa baris data → `EmptySheet(sheet)`, normalisasi header
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 5.2_

  - [x]* 13.5 Tulis property test round-trip XLSX
    - **Property 7: Round-trip XLSX**
    - File `tests/property/test_xlsx_roundtrip.py` (tulis dengan `xlsxwriter` ke beberapa sheet)
    - **Validates: Requirements 3.1, 3.2**

  - [x] 13.6 Implementasikan `studio/data/ingestion.py` (IngestionService)
    - `start_csv_job`: simpan file asli via streaming chunk 1 MiB ke `data/uploads/{workspace_id}/` memakai `sanitize_filename` + `safe_join`; job konversi background (`asyncio.to_thread`) dengan status `queued/running/done/failed`
    - `start_sheet_jobs`: satu job per sheet terpilih; `list_sheets` untuk respons upload XLSX
    - Progres `min(99, estimasi bytes / ukuran * 100)` per batch + heartbeat ≤ 1 dtk melalui event bus (`job.progress`), 100 saat Dataset terdaftar (`job.done`), `job.failed` saat gagal
    - Finalisasi: `os.replace` `.partial` → final, insert Dataset (skema, `column_mapping`, `row_count`, `make_table_name`) dalam satu transaksi; kegagalan/terputus → hapus `.partial` dan file asli yang tidak lengkap
    - `reupload`: parse ke Parquet sementara → `diff_schemas` → `os.replace` + naikkan `data_version`/`data_updated_at`, atau tolak `SCHEMA_MISMATCH {missing, added, changed}` dengan Dataset lama dipertahankan
    - _Requirements: 2.1, 3.2, 4.1, 4.2, 5.1, 5.3, 5.5, 26.1, 26.2, 26.3, 28.3, 29.5_

  - [x]* 13.7 Tulis property test round-trip CSV → Parquet
    - **Property 5: Round-trip CSV → Parquet**
    - File `tests/property/test_csv_parquet_roundtrip.py`; definisikan generator `csv_safe_tables()` di file ini; verifikasi juga `row_count` tersimpan
    - **Validates: Requirements 2.2, 4.2, 4.3**

  - [x]* 13.8 Tulis property test CSV rusak
    - **Property 8: CSV rusak ditolak dengan nomor baris yang tepat**
    - File `tests/property/test_csv_bad_row.py`; pastikan tidak ada Dataset terdaftar dan tidak ada file parsial tersisa
    - **Validates: Requirements 2.3, 5.5**

  - [x]* 13.9 Tulis unit test edge case ingestion
    - `tests/unit/test_ingestion_edge.py`: encoding non-UTF-8, file kosong, hanya header, sheet kosong, XLSX rusak/terenkripsi, ekstensi tidak didukung, re-upload skema berbeda mempertahankan Dataset lama, progres monoton dan mencapai 100
    - _Requirements: 2.3, 2.4, 3.3, 3.4, 5.3, 5.5, 26.3_

- [x] 14. Data_Engine, worker pool, dan profiling
  - [x] 14.1 Implementasikan `studio/data/worker.py` (QueryWorkerPool)
    - Pool `min(4, cpu_count)` proses `spawn`; job JSON (path Parquet, FilterSet, rencana propagasi, SQL); worker membangun `scan_parquet` → `propagation.materialize` → `pl.SQLContext(frames, eager=False).execute(sql)` → `collect(engine="streaming")`; hasil Arrow IPC bytes; timeout 60 dtk → terminate & ganti proses, `QUERY_TIMEOUT`
    - _Requirements: 5.4, 10.6, 22.2, 23.1_

  - [x] 14.2 Implementasikan `studio/data/engine.py` (DataEngine)
    - `validate(ws_id, sql)`: `analyze_sql` (V1–V6) lalu V7 `ctx.execute(sql).collect_schema()`; `POLARS_ERROR` menyertakan pesan Polars dan katalog `{tabel: [kolom:tipe]}`; output `ValidatedQuery`
    - `execute(ws_id, sql, filters, timeout_s=60)`: validasi → worker pool → `QueryResult {query_id, columns, rows, row_count, truncated_for_storage}`; simpan ke tabel `queries` (SQL, tables_used, relations_used, lineage, output_schema, snapshot ≤ 1.000 baris, filters, created_by)
    - Teks SQL tidak pernah diubah
    - _Requirements: 10.1, 10.2, 10.5, 10.7, 11.1, 11.2, 22.2_

  - [x]* 14.3 Tulis property test render terfilter
    - **Property 32: Render terfilter ekuivalen dengan query atas tabel terfilter**
    - File `tests/property/test_filtered_render_equivalence.py` (naikkan `deadline`; termasuk Cross_Filter yang dikecualikan dari chart sumber dan FilterSet kosong)
    - **Validates: Requirements 10.5, 22.2, 24.2**

  - [x]* 14.4 Tulis unit test Data_Engine
    - `tests/unit/test_engine.py`: timeout dengan batas waktu diperkecil + query lambat (worker diganti dan pool tetap berfungsi), `POLARS_ERROR` memuat katalog, `UNKNOWN_TABLE`, snapshot query tersimpan
    - _Requirements: 10.6, 10.7, 11.2_

  - [x] 14.5 Implementasikan `studio/data/profiler.py` dan hook profiling otomatis
    - `compute_column_profiles(lf)` (satu query agregat lazy; top-5 deterministik; baris duplikat; kolom tipe campuran), `classify_roles(schema, profile)`, `compute_overlap(a, b)`
    - Simpan ke `dataset_profiles`; setelah Dataset terdaftar di `ingestion.py`, jalankan profiling lalu `score_candidates` (kecualikan rejected) dan upsert Relation_Candidate; publish `dataset.profiled` dan `relation.updated`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 7.1, 7.2, 7.5_

  - [x]* 14.6 Tulis property test profil kolom
    - **Property 9: Profil kolom sesuai perhitungan referensi**
    - File `tests/property/test_column_profiles.py`
    - **Validates: Requirements 6.1, 6.2, 6.3, 6.5**

  - [x]* 14.7 Tulis property test kandidat relasi
    - **Property 10: Kandidat relasi benar dan menghormati penolakan**
    - File `tests/property/test_relation_candidates.py` (overlap nyata dari `compute_overlap` pada tabel dengan domain kunci kecil)
    - **Validates: Requirements 7.1, 7.2, 7.5**

- [x] 15. Checkpoint - Penyimpanan, ingestion, dan Data_Engine
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. REST API dan SSE workspace
  - [x] 16.1 Definisikan `studio/api/schemas.py`
    - Model request/response Pydantic untuk semua endpoint pada tabel kontrak design (Workspace, upload, sheets, job, dataset detail, relation, DashboardSnapshot, PatchEvent, patch command, render request/response, query detail)
    - _Requirements: 1.1, 7.2, 14.7, 18.4_

  - [x] 16.2 Implementasikan `studio/api/workspaces.py`
    - `POST/GET /workspaces`, `GET /workspaces/{ws}` (datasets, dashboards, chat_sessions), `PATCH` rename, `DELETE` dengan `confirm_name` yang harus cocok: hapus metadata (cascade), `shutil.rmtree` folder upload, dan panggil hook penghapusan sesi chat (diisi di task 18.11)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

  - [x] 16.3 Implementasikan `studio/api/datasets.py`
    - `POST /workspaces/{ws}/uploads` (CSV → `202 {upload_id, job_id}`, XLSX → `200 {upload_id, sheets}`, ekstensi lain → 415), `POST .../uploads/{upload_id}/sheets`, `GET /jobs/{job_id}`, `GET/PATCH /workspaces/{ws}/datasets/{id}` (profil, quality, column_mapping, `privacy_no_samples`), `POST .../reupload` (`202` atau `409 SCHEMA_MISMATCH`)
    - Batas ukuran upload dikonfigurasi minimal 1 GB dengan body streaming
    - _Requirements: 2.1, 2.4, 2.5, 3.2, 3.3, 3.4, 5.1, 6.2, 26.1, 26.3, 27.4_

  - [x] 16.4 Implementasikan `studio/api/relations.py`
    - `GET ?status=`, `POST .../confirm`, `POST .../reject`, `DELETE` (status `deleted`; item dependen menjadi `invalid` saat dibaca); publish `relation.updated`
    - _Requirements: 7.3, 7.4, 7.5, 7.8_

  - [x] 16.5 Implementasikan `studio/api/dashboards.py`, `studio/api/queries.py`, dan `studio/data/render.py`
    - Endpoint create/get/patches(`since_version`)/`POST patches`/undo/redo dengan pemetaan `VERSION_CONFLICT` → 409 `details.current_version`, validasi → 422
    - `render.py`: `render_items(dashboard, item_ids, cross_filters)` → ambil Global_Filter dari content, hitung `affected_tables`, eksekusi SQL tersimpan via DataEngine (Cross_Filter tidak diterapkan ke chart sumbernya), `bind_data`, status `invalid`/`stale`/`filter_unaffected`; tanpa pemanggilan LLM
    - `POST /dashboards/{id}/insights/{item_id}/refresh`: eksekusi ulang SQL dengan filter aktif, perbarui evidence, `filters_snapshot`, `computed_at`, `dataset_versions` via `DashboardStore.apply` (penyusunan ulang teks dihubungkan di task 18.11)
    - `GET /queries/{query_id}`: SQL, kolom, snapshot, row_count, executed_at, filters
    - _Requirements: 12.3, 14.7, 15.1, 17.2, 17.3, 17.4, 17.5, 18.5, 19.1, 19.2, 22.2, 22.3, 23.3, 24.2, 26.2_

  - [x] 16.6 Implementasikan `studio/api/events.py`
    - `GET /workspaces/{ws}/events` SSE (`StreamingResponse`) mendukung `Last-Event-ID`: `patch.applied`, `job.progress`, `job.done`, `job.failed`, `dataset.profiled`, `relation.updated`
    - _Requirements: 5.3, 16.5, 18.3_

  - [x] 16.7 Hubungkan router dan lifespan di `studio/app.py`
    - Lifespan: buat `DATA_DIR`, jalankan migrasi, inisialisasi event bus, `QueryWorkerPool`, `DataEngine`, `IngestionService`, `DashboardStore`; daftarkan semua router di prefix `/api` dan error handler
    - _Requirements: 28.1, 28.4_

  - [x]* 16.8 Tulis integration test REST API
    - `tests/integration/test_api_flow.py` (httpx `AsyncClient` + data dir sementara): upload CSV → progres → profil → kandidat relasi → konfirmasi → buat Dashboard → patch → render; 409 konflik versi; undo/redo; hapus Workspace membersihkan SQLite dan folder; restart app memulihkan Workspace/Dashboard/versi; re-upload menandai insight `stale`; render tidak memanggil LLM (model di-mock dan diassert tidak dipanggil)
    - _Requirements: 1.4, 5.3, 7.4, 17.5, 18.5, 22.3, 26.2, 28.4_

- [x] 17. Checkpoint - API backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Model_Gateway dan multi-agent ADK
  - [x] 18.1 Implementasikan `studio/agents/model_gateway.py` dan validasi startup
    - `build_models(resolved, supports_tools=litellm.supports_function_calling)` → `LiteLlm` per agent; `ModelCapabilityError(agent, model)` bila `False` atau exception
    - Di `backend/main.py`: panggil `resolve_models` + `build_models` sebelum `uvicorn.run`; kegagalan dicetak ke stderr dan `sys.exit(1)`
    - _Requirements: 9.1, 9.4, 9.5_

  - [x]* 18.2 Tulis unit test Model_Gateway
    - `tests/unit/test_model_gateway.py`: stub `supports_function_calling` (True/False/exception), pesan error menyebut model + agent, startup berhenti dengan exit 1 saat variabel hilang
    - _Requirements: 9.1, 9.4, 9.5_

  - [x] 18.3 Implementasikan `studio/agents/turn_policy.py`
    - Approval gate: set `temp:mutation_allowed` bila giliran membawa `approval.proposal_id` pending yang valid, atau `classify_turn(explicit_change_request=True, evidence)` dengan evidence ≥ 3 karakter yang merupakan substring pesan pengguna giliran ini; helper `require_mutation_allowed(state)` → `APPROVAL_REQUIRED`
    - Retry counter per invocation: `sql` (1 + 3), `chart_spec` (1 + 3), `version_conflict` (1 + 2) → `RETRY_EXHAUSTED` beserta error terakhir
    - _Requirements: 11.3, 11.4, 13.5, 20.4, 21.3, 21.4_

  - [x]* 18.4 Tulis property test approval gate
    - **Property 26: Approval gate untuk tool mutasi**
    - File `tests/property/test_approval_gate.py` (pesan, evidence, dan proposal acak; Dashboard tidak berubah saat ditolak)
    - **Validates: Requirements 21.3, 21.4**

  - [x]* 18.5 Tulis unit test retry counter
    - `tests/unit/test_turn_policy_retry.py`: SQL ditolak setelah 3 percobaan ulang, chart setelah 3, konflik versi setelah 2, error terakhir disertakan
    - _Requirements: 11.3, 11.4, 13.5, 20.4_

  - [x] 18.6 Implementasikan `studio/agents/tools/guard.py` dan `studio/agents/tools/data_tools.py`
    - `guard.py`: dekorator `@llm_output` yang memastikan return berasal dari `build_dataset_context`/`truncate_result`, dan `after_tool_callback` ADK yang menolak hasil tool > 200 baris; semua error tool dikembalikan sebagai `{"ok": false, "error": {...}}`
    - `data_tools.py`: `list_tables`, `run_sql` (validasi + eksekusi + retry counter + hasil terpotong ≤ 200 baris + `query_id` ke state `query:last_id`), `get_query_schema`, `get_query_result`, `get_dataset_profile`, `set_column_roles` (hanya 4 peran), `compute_relation_candidates`
    - _Requirements: 6.3, 6.5, 7.1, 8.4, 10.2, 11.1, 11.2, 11.3, 27.1, 27.3, 27.6_

  - [x] 18.7 Implementasikan `studio/agents/tools/dashboard_tools.py` dan `studio/agents/tools/turn_tools.py`
    - `dashboard_tools.py`: `get_dashboard_state`, `add_chart`, `update_chart`, `remove_chart`, `update_layout`, `add_insight`, `update_insight`, `undo_last`; semua mutasi wajib `base_version`, cek approval gate lebih dulu, memanggil `DashboardStore.apply/undo` dengan `source="agent"`, mengembalikan state terbaru pada `VERSION_CONFLICT`, dan menghitung retry chart/konflik
    - `turn_tools.py`: `classify_turn(intent, explicit_change_request, evidence)`, `propose_changes(summary, themes)` (simpan Proposal, emit `approval.request`), `present_profile_summary` (emit `profile.summary`), `present_relation_candidates` (emit `relation.candidates`)
    - _Requirements: 8.2, 8.3, 12.2, 12.6, 14.1, 14.2, 14.4, 14.5, 14.6, 17.5, 20.3, 20.4, 21.1, 21.2, 21.3, 21.4_

  - [x]* 18.8 Tulis unit test Agent_Tools
    - `tests/unit/test_agent_tools.py`: `APPROVAL_REQUIRED` tanpa persetujuan, callback menolak hasil > 200 baris, `cross_dataset_correlation` ditolak tanpa relasi confirmed, `INSIGHT_NUMBER_MISMATCH` menyebut angka, `VERSION_CONFLICT` mengembalikan state terbaru, `run_sql` menyimpan `query:last_id`
    - _Requirements: 14.4, 14.6, 20.3, 21.4, 27.6_

  - [x] 18.9 Implementasikan definisi agent, prompt, dan InstructionProvider
    - `agents/prompts/*.md` untuk kelima agent (aturan pemilihan tipe chart, judul/label sumbu/legenda, pemakaian `format_number`, larangan mengarang statistik, instruksi transfer kembali ke Root, alur setelah upload dan penawaran draft)
    - `agents/context.py`: InstructionProvider Root yang menyisipkan daftar dataset (via Privacy_Guard), Confirmed_Relation, ringkasan state Dashboard, `dashboard_version`, dan edit manual sejak giliran agent terakhir (`chat_sessions.last_agent_version`)
    - `root.py`, `profiler.py`, `query.py`, `chart.py`, `insight.py`: `LlmAgent` dengan model dari Model_Gateway dan tools sesuai tabel design; Root memiliki `sub_agents=[profiler, query, chart, insight]`
    - _Requirements: 6.4, 8.1, 8.2, 8.3, 12.1, 12.5, 14.1, 20.2, 21.1, 21.2_

  - [x] 18.10 Implementasikan `studio/agents/runner.py`
    - `Runner` dengan `DatabaseSessionService("sqlite+aiosqlite:///<absolut>/data/adk_sessions.db")`, `app_name="dashboard_studio"`, `user_id="local"`; `RunConfig(streaming_mode=StreamingMode.SSE)`
    - Terjemahkan event ADK ke event SSE chat (`agent.active`, `text.delta`, `tool.call`, `tool.result`, `patch.applied`, `approval.request`, `relation.candidates`, `profile.summary`, `error`, `run.done`); bungkus kegagalan sub-agent menjadi `error` + state `error:last`
    - `RunRegistry {run_id: asyncio.Task}` untuk stop (`run.stopped`); `append_user_edit_summary(session, summary)` untuk User_Edit_Event; update `last_agent_version`
    - _Requirements: 8.5, 16.1, 16.3, 16.6, 20.1, 28.2_

  - [x] 18.11 Implementasikan `studio/api/chat.py` dan hubungkan agent ke aplikasi
    - `POST /workspaces/{ws}/chat` (kirim `run.started {run_id, session_id}` segera sebelum agent berjalan, lalu stream event), `POST .../chat/runs/{run_id}/stop`, `GET .../chat/sessions/{sid}/messages`
    - Daftarkan router chat dan inisialisasi runner di `app.py`; hubungkan hook `DashboardStore.on_user_edit` ke `append_user_edit_summary`; isi hook penghapusan sesi ADK di `workspaces.py`; di `dashboards.py` panggil Insight_Agent untuk menyusun ulang teks saat hasil numerik refresh berubah
    - _Requirements: 1.4, 15.2, 16.1, 16.2, 16.6, 20.1, 28.2_

  - [x]* 18.12 Tulis test chat stream dengan model mock
    - `tests/integration/test_chat_stream.py` (model ADK di-mock): `run.started` dikirim sebelum agent berjalan dan < 2 dtk, urutan event terpisah untuk teks/tool/patch/error, stop menghasilkan `run.stopped`, ringkasan User_Edit_Event masuk sesi, kegagalan sub-agent menghasilkan event `error`, riwayat chat pulih setelah restart
    - _Requirements: 8.5, 16.2, 16.3, 16.6, 20.1, 28.4_

- [x] 19. Checkpoint - Agent dan chat backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 20. Frontend: klien API, SSE, dan logika state
  - [x] 20.1 Implementasikan `src/lib/api.ts`
    - Klien REST bertipe untuk semua endpoint (base URL dari env `NEXT_PUBLIC_API_URL`), parsing envelope error ke `ApiError {code, message, details, status}`, upload dengan `XMLHttpRequest` + callback progres; perbarui `src/lib/types.ts` bila perlu
    - _Requirements: 1.1, 1.3, 2.1, 5.3, 18.5_

  - [x] 20.2 Implementasikan `src/lib/sse.ts`
    - Langganan `EventSource` workspace dengan auto-reconnect; saat reconnect panggil `GET /dashboards/{id}/patches?since_version=` (atau snapshot) lalu terapkan berurutan
    - Parser stream chat berbasis `fetch` + `ReadableStream` untuk format `event:/data:`
    - _Requirements: 16.1, 16.3, 16.5_

  - [x] 20.3 Implementasikan `src/lib/dashboard-state.ts`
    - Reducer `applyPatch(state, patch)` yang mencerminkan `core/patches.py` untuk semua op; tolak `version !== state.version + 1` dengan sinyal resync; handler 409 → refetch snapshot + notifikasi "perubahan ditolak karena state sudah berubah"
    - _Requirements: 18.3, 18.6, 30.3_

  - [x]* 20.4 Tulis test Vitest untuk dashboard-state
    - `src/lib/dashboard-state.test.ts`: setiap jenis op, penolakan versi tidak berurutan, penanganan 409, dan kecocokan dengan fixture `__fixtures__/patches.json`
    - _Requirements: 18.6, 30.3_

  - [x] 20.5 Implementasikan `src/lib/filters.ts`
    - `toggleCrossFilter(fs, element)`, `normalizeFilters`, `filtersDiffer(snapshot, globalFilters)`
    - _Requirements: 15.3, 24.1, 24.4_

  - [x]* 20.6 Tulis property test toggle Cross_Filter (fast-check)
    - **Property 33: Toggle Cross_Filter**
    - File `src/lib/filters.test.ts` (`numRuns: 100`, tag komentar `Feature: dashboard-studio-agent, Property 33`); juga contoh `filtersDiffer`
    - **Validates: Requirements 24.1, 24.4**

  - [x]* 20.7 Tulis test Vitest untuk parser SSE
    - `src/lib/sse.test.ts`: parsing event terpotong antar-chunk, resync patch saat reconnect
    - _Requirements: 16.3, 16.5_

- [x] 21. Frontend: komponen UI
  - [x] 21.1 Implementasikan halaman daftar Workspace dan kerangka halaman Workspace
    - `src/app/page.tsx` + `src/components/workspaces/`: buat, ganti nama, hapus dengan konfirmasi nama
    - `src/app/w/[workspaceId]/page.tsx`: kerangka yang memuat daftar Dataset, Dashboard, dan sesi chat
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 21.2 Implementasikan `src/components/datasets/`
    - Upload dengan progres gabungan (XHR 0–50%, `job.progress` 50–100%, diperbarui ≤ 2 dtk), pemilih sheet XLSX, tampilan error (format, parse + nomor baris, sheet kosong), detail dataset (skema, profil, kualitas, pemetaan kolom), toggle privasi "jangan kirim sample rows", re-upload dengan tampilan `missing/added/changed`
    - _Requirements: 2.3, 2.4, 2.5, 3.2, 3.3, 5.3, 6.2, 26.3, 27.4_

  - [x] 21.3 Implementasikan `src/components/relations/`
    - Daftar relasi (candidate/confirmed/rejected) dengan kardinalitas dan overlap, aksi konfirmasi, tolak, dan hapus
    - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.8_

  - [x] 21.4 Implementasikan `src/components/chat/` (Chat_Panel)
    - Streaming teks, indikator agent aktif dan tool berjalan, kartu kandidat relasi (Konfirmasi/Tolak), kartu `approval.request` dengan tombol Setujui (mengirim `approval.proposal_id`), kartu ringkasan profil, pesan error, tombol Stop, riwayat sesi
    - _Requirements: 6.4, 7.3, 8.5, 11.4, 16.1, 16.4, 16.6, 21.1, 21.2_

  - [x]* 21.5 Tulis test komponen Chat_Panel
    - `src/components/chat/ChatPanel.test.tsx` (React Testing Library): status agent/tool, kartu relasi, approval mengirim `proposal_id`, tombol Stop
    - _Requirements: 7.3, 16.4, 16.6, 21.2_

  - [x] 21.6 Implementasikan `src/components/canvas/` (Canvas_Editor)
    - Grid react-grid-layout 12 kolom; chart dengan echarts-for-react dari option hasil `/render` (tanpa `eval`); `onDragStop/onResizeStop` → command `set_layout`; menu ganti tipe (`change_chart_type`, toast bila 422) dan hapus (`remove_item`); tombol undo/redo berdasarkan `can_undo/can_redo`; badge `invalid`, `stale`, `tidak terpengaruh filter`; memakai komponen InsightCard dari task 21.8
    - _Requirements: 7.8, 12.4, 17.1, 17.2, 17.3, 17.4, 19.1, 19.2, 23.3, 26.2_

  - [x] 21.7 Implementasikan `src/components/filters/` dan cross-filter di canvas
    - Global_Filter rentang tanggal untuk kolom waktu dan kategorikal untuk kolom dimensi (command `set_global_filters`); klik elemen chart → `toggleCrossFilter` pada kolom `cross_filter_column`; penanda Cross_Filter aktif + tombol hapus; setiap perubahan memicu `/render` ulang
    - _Requirements: 22.1, 22.3, 24.1, 24.2, 24.3, 24.4_

  - [x] 21.8 Implementasikan `src/components/insights/`
    - `InsightCard` (teks, tipe, badge "dihitung dengan filter berbeda" via `filtersDiffer`, badge stale), modal detail yang menampilkan SQL sumber dan tabel bukti dari `GET /queries/{id}`, tombol refresh
    - _Requirements: 14.7, 15.1, 15.3, 26.2_

  - [x]* 21.9 Tulis test komponen canvas dan insight
    - `src/components/canvas/Canvas.test.tsx` dan `src/components/insights/InsightCard.test.tsx`: badge status, penanda Cross_Filter + hapus, detail insight menampilkan SQL dan tabel bukti, penanda filter berbeda
    - _Requirements: 7.8, 14.7, 15.3, 23.3, 24.3_

  - [x] 21.10 Implementasikan `src/components/export/` (Export_Service)
    - PNG: `toPng(canvasNode)` setelah semua chart selesai render; PDF: jsPDF A4 landscape berisi judul Dashboard, filter aktif, waktu ekspor, lalu gambar canvas dipecah per halaman; error → toast tanpa mengubah state
    - _Requirements: 25.1, 25.2, 25.3_

  - [x]* 21.11 Tulis test Export_Service
    - `src/components/export/Export.test.tsx` dengan `html-to-image`/`jspdf` di-mock: sukses PNG/PDF memuat judul, filter, waktu; gagal menampilkan error dan state tetap
    - _Requirements: 25.1, 25.2, 25.3_

- [x] 22. Evaluasi agent (ADK eval)
  - [x] 22.1 Buat dataset contoh multi-dataset
    - Skrip deterministik `backend/tests/eval/samples/generate_samples.py` yang menghasilkan `customers.csv`, `transactions.csv` (relasi `customer_id`), dan `products.xlsx`; commit file hasilnya
    - _Requirements: 30.4_

  - [x] 22.2 Buat evalset dan test eval
    - `backend/tests/eval/studio.evalset.json`: kasus profiling setelah upload, konfirmasi relasi, pertanyaan agregasi, chart per bulan, ganti tipe chart via chat, insight kontributor teratas, insight lintas-dataset, permintaan tanpa persetujuan (tidak boleh memanggil tool mutasi)
    - `backend/tests/eval/test_eval.py`: `AgentEvaluator` dengan `tool_trajectory_avg_score` dan `response_match_score`, plus metrik kustom (setiap SQL lolos SQL_Validator, setiap Insight_Card lolos verifier angka, tidak ada tool mutasi tanpa approval); diberi marker agar tidak berjalan default
    - _Requirements: 8.2, 12.6, 30.2, 30.4_

- [x] 23. Wiring akhir
  - [x] 23.1 Rangkai halaman Workspace
    - Di `src/app/w/[workspaceId]/page.tsx`: gabungkan Chat_Panel, Canvas_Editor, panel dataset/relasi, filter, dan ekspor dengan satu state store (reducer `applyPatch`) yang diisi dari SSE workspace dan chat; picu `/render` saat patch, filter, atau Cross_Filter berubah; setelah `job.done` kirim giliran chat pemicu agar Root menampilkan ringkasan profil dan kandidat relasi; render ulang chart saat `data_version` Dataset berubah
    - _Requirements: 1.2, 6.4, 16.5, 18.3, 21.1, 22.3, 24.4, 26.2_

  - [x]* 23.2 Tulis integration test end-to-end backend dengan model mock
    - `tests/integration/test_e2e_flow.py`: upload dua CSV → profil → konfirmasi relasi → chat (model mock memanggil `run_sql` + `add_chart` + `add_insight`) → edit manual → konflik versi agent → undo → filter global dengan propagasi → re-upload
    - _Requirements: 7.4, 17.5, 18.5, 19.1, 20.1, 20.4, 23.1, 26.2_

  - [x]* 23.3 Tulis performance test (marker `slow`)
    - `tests/perf/test_large_data.py`: CSV sintetis > 100 MB dengan RSS puncak < 2× ukuran file (psutil), render ulang ≤ 5 dtk untuk 10 juta baris
    - _Requirements: 5.1, 5.2, 22.4_

- [x] 24. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Ekstensi v2: Model Semantik, Dashboard_Architect_Agent, dan Blueprint

Urutan: model domain & patch (KPI, Brief) → penyimpanan semantik → logika semantik murni → Semantic_Drafter & API → KPI/blueprint/layout/review murni → agent, tools, konteks, prompt, knowledge → frontend → eval. Lihat bagian "Ekstensi v2" di design.md.

- [x] 25. Model domain dan patch untuk KPI_Card dan Design_Brief
  - [x] 25.1 Perluas `studio/core/models.py` dan `frontend/src/lib/types.ts`
    - Tambah `NumberFormat`, `KpiSpec`, `KpiItem`, `BriefKpi`, `DesignBrief`, `SectionRole`, `VisualType`, `BlueprintSlot`, `DashboardBlueprint`, model Semantic_Model (`SemanticColumn`, `BusinessMetric`, `GlossaryTerm`, `WorkspaceInstruction`, `VerifiedQuery`, `SemanticModel`)
    - `DashboardItem` union menjadi `ChartItem | InsightItem | KpiItem`; `DashboardContent.brief: DesignBrief | None = None` (konten lama tetap valid)
    - Op `SetBriefOp`; command `AddKpiCommand`, `UpdateKpiCommand`, `SetBriefCommand`; perbarui adapter dan `__all__`
    - _Requirements: 31.2, 31.3, 31.4, 36.1, 36.2, 38.1_

  - [x] 25.2 Perluas `core/patches.py`, `core/status.py`, dan reducer frontend
    - `resolve_command` untuk `add_kpi`, `update_kpi`, `set_brief`; `apply_ops`/`invert_ops` untuk `set_brief`; `change_chart_type` pada KPI → `INVALID_OP`
    - `item_status` memperlakukan `KpiItem` seperti `ChartItem`
    - `frontend/src/lib/dashboard-state.ts`: `applyPatch` untuk op baru
    - _Requirements: 36.2, 38.6_

  - [x]* 25.3 Perluas generator dan fixture untuk Properti 23 dan 24
    - `tests/property/strategies.py`: `dashboard_contents()`/`valid_commands()` menyertakan KPI dan `set_brief`; regenerasi `frontend/src/lib/__fixtures__/patches.json` via `gen_patch_fixtures.py`; Vitest fixture tetap lulus
    - _Requirements: 19.4, 19.5, 30.3, 36.2_

- [x] 26. Penyimpanan Semantic_Model
  - [x] 26.1 Buat migrasi `store/migrations/002_semantic.sql`
    - Tabel `semantic_entries`, `semantic_meta`, `semantic_draft_runs`, `dashboard_blueprints`; kolom `proposals.kind` dan `proposals.payload_json` sesuai design
    - _Requirements: 31.1, 31.4, 37.6_

  - [x] 26.2 Tambah repositori di `store/repos.py`
    - `SemanticRepo` (list per status/kind, upsert by `entry_key`, confirm, reject, confirm-all candidate, `rejected_keys`, naikkan `semantic_version`), `SemanticMetaRepo`, `DraftRunRepo`, `BlueprintRepo` (create active, update slot status, finish), perluasan `ProposalRepo` (kind, payload)
    - _Requirements: 31.1, 31.4, 31.7, 31.8, 37.6, 37.8_

  - [x]* 26.3 Tulis unit test repositori semantik
    - `tests/unit/test_semantic_repos.py`: UNIQUE `entry_key`, cascade saat Dataset/Workspace dihapus, rejected tetap tersimpan, `semantic_version` naik
    - _Requirements: 31.8, 31.9_

- [x] 27. Logika semantik murni
  - [x] 27.1 Implementasikan `core/semantic.py`
    - Kunci kanonik entri, `heuristic_draft`, `merge_draft`, `metric_probe_sql`, cek agregat (`METRIC_NOT_AGGREGATE`)
    - Tambah `DataEngine.validate_metric(ws_id, metric)` yang menjalankan probe melalui `validate` (V1–V7) dan memetakan error ke `METRIC_INVALID`
    - _Requirements: 31.5, 31.6, 31.8, 32.2, 32.5, 32.6_

  - [x]* 27.2 Tulis property test validasi ekspresi metrik
    - **Property 36: Validasi ekspresi Business_Metric**
    - File `tests/property/test_metric_validation.py`; generator `metric_exprs(schema)` di `tests/property/strategies_semantic.py`
    - **Validates: Requirements 31.5, 31.6**

  - [x]* 27.3 Tulis property test draft semantik
    - **Property 38: Draft semantik deterministik dan menghormati keputusan pengguna**
    - File `tests/property/test_semantic_draft.py`
    - **Validates: Requirements 31.8, 32.2, 32.5, 32.6**

  - [x] 27.4 Implementasikan `core/semantic_yaml.py`
    - Tambah dependensi `pyyaml` versi exact di `pyproject.toml`; `export_yaml` deterministik dan `import_yaml` dengan `safe_load` + validasi Pydantic yang melaporkan `issues`
    - _Requirements: 31.10, 31.11_

  - [x]* 27.5 Tulis property test round-trip YAML
    - **Property 37: Round-trip YAML Semantic_Model**
    - File `tests/property/test_semantic_yaml.py`
    - **Validates: Requirements 31.10, 31.12**

  - [x] 27.6 Implementasikan `core/semantic_context.py`
    - `build_semantic_block(model, scope, budget, privacy)` dengan urutan prioritas, penanda `unconfirmed`, pemotongan prefiks, penanda `truncated`, penghapusan enum untuk `privacy_no_samples`; `Settings.semantic_context_budget = 12000`
    - _Requirements: 33.2, 33.3, 33.4, 33.5_

  - [x]* 27.7 Tulis property test blok konteks semantik
    - **Property 39: Anggaran dan prioritas blok konteks semantik**
    - File `tests/property/test_semantic_context.py`
    - **Validates: Requirements 33.2, 33.3, 33.4, 33.5, 33.8**

  - [x] 27.8 Implementasikan `core/verified_queries.py`
    - `tokenize`, `score` (Jaccard), `find_verified`, dan helper validitas berbasis `analyze_sql`
    - _Requirements: 34.3, 34.4_

  - [x]* 27.9 Tulis property test pencarian Verified_Query
    - **Property 40: Pencarian Verified_Query**
    - File `tests/property/test_verified_queries.py`
    - **Validates: Requirements 34.3, 34.4, 34.5**

- [x] 28. Semantic_Drafter dan API semantik
  - [x] 28.1 Implementasikan `data/semantic_drafter.py` dan konfigurasi model
    - `AGENT_ENV` tambah `semantic` (`MODEL_SEMANTIC`) dan `architect` (`MODEL_ARCHITECT`), update `.env.example`, `Settings`, dan Model_Gateway (cek tool-calling dilewati untuk `semantic`); perbarui test Properti 11
    - Pipeline: heuristik → simpan + `semantic.updated` → LLM (`LlmAgent` + `output_schema`, `Runner` dengan `InMemorySessionService`, timeout 60 dtk, prompt `prompts/semantic_drafter.md`) → validasi per entri → merge → simpan + `semantic.updated`; gagal → `llm_failed` + `semantic.warning`; satu drafter aktif per Workspace dengan antrean
    - _Requirements: 9.2, 9.5, 32.1, 32.3, 32.4, 32.7, 32.8, 32.11_

  - [x] 28.2 Hubungkan drafter ke pipeline profiling
    - Di `data/ingestion.py`, setelah `score_candidates` dan `relation.updated`, jalankan drafter sebagai task latar belakang
    - _Requirements: 32.1, 32.11_

  - [x] 28.3 Implementasikan `api/semantic.py`
    - Endpoint GET/PATCH/POST entri, confirm/reject, confirm-all, export/import YAML (`422 SEMANTIC_IMPORT_INVALID`), `POST /dashboards/{id}/items/{item_id}/verify`, `GET /dashboards/{id}/blueprint`; daftarkan router; event workspace `semantic.updated` dan `semantic.warning` di `api/events.py`
    - _Requirements: 31.6, 31.7, 31.10, 31.11, 32.10, 34.2_

  - [x]* 28.4 Tulis unit dan integration test drafter
    - `tests/unit/test_semantic_drafter.py` (model mock: sukses, timeout, output tidak sesuai skema, metrik tidak valid dibuang, entri confirmed tidak ditimpa) dan `tests/integration/test_semantic_api.py` (upload → dua `semantic.updated` → confirm-all → export → import ulang ekuivalen → impor berisi entri invalid ditolak tanpa perubahan)
    - _Requirements: 31.11, 32.5, 32.7, 32.8_

- [x] 29. Checkpoint - Semantic_Model
  - Ensure all tests pass, ask the user if questions arise.

- [x] 30. KPI, Layout_Template, Blueprint, dan Design_Rules (fungsi murni)
  - [x] 30.1 Implementasikan `core/kpi.py` dan render KPI
    - `validate_kpi_spec`, `compute_kpi` (delta, delta_pct, sentiment, formatted via `format_number`), `KpiShapeError`
    - `DashboardStore` memvalidasi `add_kpi`/`update_kpi` terhadap skema query tersimpan; `data/render.py` mengembalikan `RenderedItem.kpi` dan status `KPI_SHAPE`; perbarui `schemas.py`
    - _Requirements: 38.2, 38.3, 38.4, 38.5, 38.6_

  - [x]* 30.2 Tulis property test KPI
    - **Property 42: Perhitungan dan format KPI_Card**
    - File `tests/property/test_kpi.py`
    - **Validates: Requirements 38.4, 38.5, 38.9, 38.10**

  - [x] 30.3 Implementasikan `core/layout_templates.py` dan `core/blueprint.py`
    - `place_slots`, `validate_blueprint` (kode issue sesuai design), `select_slots`
    - _Requirements: 37.2, 37.3, 37.4, 37.6_

  - [x]* 30.4 Tulis property test Layout_Template dan Blueprint
    - **Property 41: Layout_Template dan validasi Blueprint**
    - File `tests/property/test_blueprint_layout.py`; generator `blueprint_slots()` di `strategies_semantic.py`
    - **Validates: Requirements 37.2, 37.4, 37.6, 37.13**

  - [x] 30.5 Implementasikan `core/design_rules.py`
    - `review(content, semantic, query_meta)` dengan 10 aturan di design; hasil terurut `(code, item_ids)`
    - _Requirements: 39.2, 39.3_

  - [x]* 30.6 Tulis property test review desain
    - **Property 43: Determinisme review desain**
    - File `tests/property/test_design_rules.py`
    - **Validates: Requirements 39.2, 39.3, 39.7**

- [x] 31. Agent, tools, konteks, dan pengetahuan BI
  - [x] 31.1 Tulis BI_Knowledge_Pack di `agents/knowledge/`
    - `principles.md`, `kpi_catalog.md` (rumus, grain, arah baik), `interaction.md`, `chart_selection.md` (pindahan tabel dari `chart.md` + pola KPI dan waterfall), `playbooks/{sales,finance,marketing,operations,hr,ecommerce}.md`; setiap file dengan front-matter `topic` dan `summary`, ≤ 16 KB
    - _Requirements: 35.2_

  - [x] 31.2 Implementasikan tool knowledge dan semantik
    - `agents/tools/knowledge_tools.py`: `get_bi_knowledge` (`UNKNOWN_TOPIC` + daftar topik)
    - `agents/tools/semantic_tools.py`: `get_semantic_model`, `search_semantic`, `find_verified_queries`, `present_semantic_draft` (emit `semantic.draft`)
    - _Requirements: 32.9, 33.4, 34.3, 35.3, 35.4_

  - [x] 31.3 Implementasikan tool Architect, Blueprint, dan KPI
    - `agents/tools/architect_tools.py`: `propose_dashboard_plan` (validasi + `place_slots`, simpan proposal `kind=blueprint`, emit `approval.request` berisi blueprint), `update_brief` (approval gate), `review_dashboard` (emit `review.findings`)
    - `agents/tools/blueprint_tools.py`: `next_blueprint_slot` (reset retry, `blueprint:current_slot`, emit progres), `mark_slot_done`
    - `dashboard_tools.py`: `add_kpi`, `update_kpi`; parameter `slot_id` opsional pada `add_chart`/`add_kpi`/`add_insight` yang memakai layout slot; buat Verified_Query `candidate` setelah `add_chart`/`add_kpi` sukses
    - _Requirements: 34.1, 36.4, 37.1, 37.2, 37.3, 37.7, 37.8, 37.9, 37.10, 38.8, 39.1, 39.6_

  - [x] 31.4 Perluas TurnPolicy, runner, dan API chat untuk persetujuan Blueprint
    - `ChatRequest.approval.selected_slot_ids`; `resolve_approval` untuk proposal `blueprint` menyimpan `select_slots(...)` ke `dashboard_blueprints` dan state `blueprint:active_id`
    - Penanganan stop menandai slot `pending`/`building` sebagai `skipped`; terjemahkan event chat baru (`semantic.draft`, `blueprint.progress`, `review.findings`, `approval.request` dengan `kind`)
    - _Requirements: 37.6, 37.8, 37.11_

  - [x] 31.5 Generalisasi InstructionProvider di `agents/context.py`
    - `make_instruction(agent_key, services)` untuk semua agent dengan cakupan sesuai tabel design; Query menerima 3 Verified_Query teratas; Architect menerima `brief_drift` dan sumber item
    - _Requirements: 33.1, 33.2, 33.5, 36.5_

  - [x] 31.6 Tulis dan perbarui prompt
    - Baru: `prompts/architect.md` (metode 6 langkah, deteksi gaya kolaborasi, batas 3 pertanyaan, larangan mengubah item `user`, alur blueprint → build → review) dan `prompts/semantic_drafter.md`
    - Ubah: `root.md` (delegasi ke Architect, loop `next_blueprint_slot`/`mark_slot_done`, default filters, kartu semantik), `query.md` (pakai `expr` metrik, Workspace_Instruction, Verified_Query), `chart.md` (KPI lewat `add_kpi`, pola waterfall, `slot_id`), `insight.md` (format dari Semantic_Model)
    - _Requirements: 21.1, 21.2, 33.6, 33.7, 35.5, 35.6, 35.7, 35.8, 35.9, 35.10, 37.12, 39.4_

  - [x] 31.7 Daftarkan Dashboard_Architect_Agent
    - `agents/definitions.py` dan `wiring.py`: `LlmAgent` architect dengan model `architect` dan tools sesuai design; tambahkan ke `sub_agents` Root; tambahkan tool baru ke Root, Query, dan Chart
    - _Requirements: 35.1, 35.10_

  - [x]* 31.8 Tulis unit test tools baru
    - `tests/unit/test_architect_tools.py`: `UNKNOWN_TOPIC`, `BLUEPRINT_INVALID` tidak emit kartu, approval dengan `selected_slot_ids`, `next_blueprint_slot` mereset retry, slot gagal tidak menghentikan loop, stop → `skipped`, `add_kpi` tanpa approval → `APPROVAL_REQUIRED`, Verified_Query candidate dibuat setelah `add_chart`
    - _Requirements: 34.1, 35.4, 37.3, 37.6, 37.9, 37.10, 37.11, 38.8_

  - [x]* 31.9 Tulis integration test alur Blueprint dengan model mock
    - `tests/integration/test_blueprint_flow.py`: chat "buatkan dashboard" → `approval.request(kind=blueprint)` → setujui 3 dari 5 slot → 3 item tepat di layout slot + `blueprint.progress` → default filters diterapkan → `review.findings`
    - _Requirements: 37.5, 37.6, 37.7, 37.8, 37.12, 39.1_

- [x] 32. Checkpoint - Agent dan Blueprint backend
  - Ensure all tests pass, ask the user if questions arise.

- [x] 33. Frontend ekstensi
  - [x] 33.1 Perluas `lib/api.ts`, `lib/sse.ts`, dan `lib/types.ts`
    - Klien endpoint semantik, verify item, blueprint; `approval.selected_slot_ids`; event chat dan workspace baru
    - _Requirements: 31.7, 34.2, 37.6_

  - [x] 33.2 Implementasikan `components/semantic/` dan `SemanticDraftCard`
    - Panel Semantik (filter status, edit inline, confirm/reject, confirm-all, ekspor/impor YAML dengan tampilan `issues`, entri yang dibuang drafter); kartu "Pemahaman data" di Chat_Panel
    - _Requirements: 31.7, 31.10, 31.11, 32.9, 32.10_

  - [x] 33.3 Implementasikan `BlueprintCard` dan `ReviewCard` di Chat_Panel
    - Miniatur grid, checkbox per slot, edit tujuan/visual sebagai pesan revisi, Setujui terpilih/semua/Revisi, progres slot dari `blueprint.progress`; kartu temuan dengan "Terapkan saran"
    - _Requirements: 37.5, 37.8, 39.5_

  - [x] 33.4 Implementasikan `KpiTile` di Canvas_Editor
    - Angka besar, label, pembanding, delta berwarna sesuai `sentiment`, `aria-label` deskriptif, badge status, menu hapus dan "Tandai terverifikasi" (juga untuk chart)
    - _Requirements: 34.2, 38.7_

  - [x] 33.5 Implementasikan `components/brief/BriefPanel` dan tombol "Reset semua filter"
    - Form Design_Brief → command `set_brief`; tombol reset di toolbar canvas → `set_global_filters []` + kosongkan Cross_Filter; rangkai panel baru di `WorkspaceStudio.tsx`
    - _Requirements: 22.8, 36.3_

  - [x]* 33.6 Tulis test komponen ekstensi
    - `SemanticPanel.test.tsx`, `BlueprintCard.test.tsx` (approval mengirim `selected_slot_ids`, progres), `KpiTile.test.tsx` (warna sesuai sentimen, aria-label), `BriefPanel.test.tsx`, reset filter
    - _Requirements: 22.8, 32.10, 37.5, 37.6, 38.7_

- [x] 34. Evaluasi agent ekstensi
  - [x] 34.1 Tambah dataset contoh dan evalset
    - `generate_samples.py` menghasilkan `finance_monthly.csv`; `tests/eval/blueprint.evalset.json` berisi kasus sinonim metrik, Workspace_Instruction, agent lead, user lead, item `user` tidak diubah, KPI via `add_kpi`
    - _Requirements: 30.2, 30.4, 33.6, 33.7, 35.6, 35.7, 35.9_

  - [x] 34.2 Tambah metrik eval kustom
    - Di `tests/eval/test_blueprint_eval.py`: setiap Blueprint lolos `validate_blueprint`, setiap item hasil build berada di layout slot, tidak ada tool mutasi tanpa approval
    - _Requirements: 30.2, 37.2, 37.7, 21.4_

- [x] 35. Final checkpoint - Ekstensi v2
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Sub-task bertanda `*` bersifat opsional (test) dan dapat dilewati untuk MVP yang lebih cepat.
- Setiap property test Hypothesis/fast-check memakai minimal 100 iterasi dan diberi komentar tag `# Feature: dashboard-studio-agent, Property N: <judul>`; satu property = satu test.
- Properti 1–32 dan 34–35 diuji dengan Hypothesis di backend; Properti 33 dengan fast-check di Vitest.
- Semua perubahan Dashboard hanya melalui `DashboardStore.apply/undo/redo` (Req 18.1); tidak ada dependensi SDK cloud storage (Req 28.5).
- Checkpoint memastikan validasi inkremental; ADK eval (task 22.2) memanggil LLM sungguhan sehingga dijalankan manual.
- Ekstensi v2 (task 25–35): Properti 36–43 diuji dengan Hypothesis; Properti 23 dan 24 diperluas untuk KPI dan `set_brief`. Perilaku Dashboard_Architect_Agent (gaya kolaborasi, penggunaan metrik) divalidasi lewat ADK eval (task 34), bukan pytest. Halaman/tab per Dashboard di luar cakupan.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "3.1"] },
    { "id": 2, "tasks": ["1.3", "3.2", "3.7", "4.1", "4.3", "5.1", "6.1", "7.1", "8.1", "9.1", "9.3", "9.4", "11.1", "11.4"] },
    { "id": 3, "tasks": ["3.3", "3.4", "3.5", "3.6", "3.8", "4.2", "4.4", "5.2", "6.2", "6.4", "7.2", "7.3", "8.2", "8.5", "9.2", "9.5", "11.2", "11.5"] },
    { "id": 4, "tasks": ["5.3", "6.3", "6.5", "6.6", "6.7", "8.3", "8.4", "8.6", "11.3", "12.1", "13.1", "13.2", "13.4"] },
    { "id": 5, "tasks": ["5.4", "12.2", "12.3", "12.4", "12.5", "12.6", "13.3", "13.5", "13.6", "14.1"] },
    { "id": 6, "tasks": ["13.7", "13.8", "13.9", "14.2"] },
    { "id": 7, "tasks": ["14.3", "14.4", "14.5"] },
    { "id": 8, "tasks": ["14.6", "14.7", "16.1"] },
    { "id": 9, "tasks": ["16.2", "16.3", "16.4", "16.5", "16.6"] },
    { "id": 10, "tasks": ["16.7", "18.1", "18.3", "20.1", "20.3", "20.5", "22.1"] },
    { "id": 11, "tasks": ["16.8", "18.2", "18.4", "18.5", "18.6", "20.2", "20.4", "20.6"] },
    { "id": 12, "tasks": ["18.7", "20.7", "21.1"] },
    { "id": 13, "tasks": ["18.8", "18.9", "21.2", "21.3", "21.4", "21.8", "21.10"] },
    { "id": 14, "tasks": ["18.10", "21.5", "21.6", "21.11"] },
    { "id": 15, "tasks": ["18.11", "21.7"] },
    { "id": 16, "tasks": ["18.12", "21.9", "22.2", "23.1"] },
    { "id": 17, "tasks": ["23.2", "23.3"] },
    { "id": 18, "tasks": ["25.1", "26.1", "31.1"] },
    { "id": 19, "tasks": ["25.2", "26.2", "27.1", "27.4", "27.6", "27.8", "30.3", "30.5"] },
    { "id": 20, "tasks": ["25.3", "26.3", "27.2", "27.3", "27.5", "27.7", "27.9", "30.1", "30.4", "30.6"] },
    { "id": 21, "tasks": ["28.1", "30.2", "31.2"] },
    { "id": 22, "tasks": ["28.2", "28.3", "31.3"] },
    { "id": 23, "tasks": ["28.4", "31.4", "31.5", "33.1"] },
    { "id": 24, "tasks": ["31.6", "31.7", "33.2", "33.3", "33.4", "33.5"] },
    { "id": 25, "tasks": ["31.8", "31.9", "33.6", "34.1"] },
    { "id": 26, "tasks": ["34.2"] }
  ]
}
```
