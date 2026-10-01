-- Ekstensi v2: Semantic_Model, draft run, Dashboard_Blueprint (Req 31, 32, 37).

CREATE TABLE semantic_entries (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('column','metric','term','instruction','verified_query')),
  entry_key TEXT NOT NULL,
  dataset_id TEXT REFERENCES datasets(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','rejected')),
  source TEXT NOT NULL CHECK (source IN ('auto','user')),
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_at TEXT,
  UNIQUE (workspace_id, entry_key)
);

CREATE INDEX idx_semantic_entries_ws_kind ON semantic_entries (workspace_id, kind, status);

CREATE TABLE semantic_meta (
  workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  domain TEXT,
  domain_confidence REAL,
  assumptions_json TEXT NOT NULL DEFAULT '[]',
  semantic_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE semantic_draft_runs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  trigger_dataset_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','done','llm_failed')),
  discarded_json TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE dashboard_blueprints (
  id TEXT PRIMARY KEY,
  dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
  proposal_id TEXT REFERENCES proposals(id) ON DELETE SET NULL,
  blueprint_json TEXT NOT NULL,
  slot_status_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','completed','stopped')),
  created_at TEXT NOT NULL,
  finished_at TEXT
);

ALTER TABLE proposals ADD COLUMN kind TEXT NOT NULL DEFAULT 'changes';
ALTER TABLE proposals ADD COLUMN payload_json TEXT;
