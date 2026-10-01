-- Migrasi 001: skema awal Metadata_Store (data/studio.db).
-- Sesuai design.md "Data Models". PRAGMA di bawah dijalankan runner migrasi
-- di luar transaksi; db.py juga menyetelnya pada setiap koneksi baru.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,                 -- ULID
  owner_id TEXT NOT NULL DEFAULT 'local',
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,            -- ISO-8601 UTC
  updated_at TEXT NOT NULL
);

CREATE TABLE uploads (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,           -- relatif terhadap data/uploads/{workspace_id}/
  kind TEXT NOT NULL CHECK (kind IN ('csv','xlsx')),
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE datasets (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT 'local',
  upload_id TEXT REFERENCES uploads(id),
  table_name TEXT NOT NULL,            -- identifier SQL unik per workspace
  source_name TEXT NOT NULL,           -- nama file / sheet
  sheet_name TEXT,
  parquet_path TEXT NOT NULL,
  schema_json TEXT NOT NULL,           -- [{name, type}]
  column_mapping_json TEXT NOT NULL,   -- [{original, normalized}]
  row_count INTEGER NOT NULL,
  data_version INTEGER NOT NULL DEFAULT 1,   -- naik saat re-upload
  data_updated_at TEXT NOT NULL,
  privacy_no_samples INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (workspace_id, table_name)
);

CREATE TABLE dataset_profiles (
  dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
  data_version INTEGER NOT NULL,
  columns_json TEXT NOT NULL,          -- ColumnProfile[] (termasuk role)
  quality_json TEXT NOT NULL,          -- {duplicate_rows, mixed_type_columns[], null_pct{}}
  computed_at TEXT NOT NULL
);

CREATE TABLE relations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  candidate_key TEXT NOT NULL,         -- "tA.cA|tB.cB" kanonik (urut leksikografis)
  from_dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  from_column TEXT NOT NULL,
  to_dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  to_column TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('one_to_one','one_to_many','many_to_many')),
  overlap_pct REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','rejected','deleted')),
  created_at TEXT NOT NULL,
  decided_at TEXT,
  UNIQUE (workspace_id, candidate_key)
);

CREATE TABLE queries (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  sql TEXT NOT NULL,
  tables_used_json TEXT NOT NULL,      -- [table_name]
  dataset_ids_json TEXT NOT NULL,
  relations_used_json TEXT NOT NULL,   -- [relation_id]
  lineage_json TEXT NOT NULL,          -- {output_col: {table, column} | null}
  output_schema_json TEXT NOT NULL,
  result_snapshot_json TEXT NOT NULL,  -- <= 1000 baris
  row_count INTEGER NOT NULL,
  filters_json TEXT NOT NULL,          -- FilterSet saat eksekusi
  executed_at TEXT NOT NULL,
  created_by TEXT NOT NULL CHECK (created_by IN ('agent','user','system'))
);

CREATE TABLE dashboards (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT 'local',
  title TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  content_json TEXT NOT NULL,          -- DashboardContent
  undo_stack_json TEXT NOT NULL DEFAULT '[]',   -- [patch_id]
  redo_stack_json TEXT NOT NULL DEFAULT '[]',   -- [patch_id]
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE patch_events (
  id TEXT PRIMARY KEY,
  dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,            -- versi SETELAH patch
  base_version INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('agent','user')),
  kind TEXT NOT NULL CHECK (kind IN ('normal','undo','redo')),
  target_patch_id TEXT REFERENCES patch_events(id),   -- untuk undo/redo
  command_json TEXT NOT NULL,          -- command asli (audit & ringkasan)
  ops_json TEXT NOT NULL,              -- ops forward yang diterapkan
  inverse_ops_json TEXT NOT NULL,
  actor_run_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (dashboard_id, version)
);

CREATE TABLE chat_sessions (
  id TEXT PRIMARY KEY,                 -- = ADK session id
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_agent_version INTEGER NOT NULL DEFAULT 0   -- versi dashboard saat agent terakhir berjalan
);

CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','expired')),
  created_at TEXT NOT NULL
);
