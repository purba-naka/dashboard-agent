"""Skema awal Metadata_Store (setara migrations/001_init.sql + 002_semantic.sql).

Revision ID: 0001_init
Revises:
"""

from alembic import op

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None

# Timestamp = string ISO, JSON = string: sama dengan SQLite agar repos.py tidak berubah.
TABLES = [
    """CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL DEFAULT 'local',
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)""",
    """CREATE TABLE uploads (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  original_name TEXT NOT NULL,
  stored_path TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('csv','xlsx')),
  size_bytes BIGINT NOT NULL,
  created_at TEXT NOT NULL
)""",
    """CREATE TABLE datasets (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT 'local',
  upload_id TEXT REFERENCES uploads(id),
  table_name TEXT NOT NULL,
  source_name TEXT NOT NULL,
  sheet_name TEXT,
  parquet_path TEXT NOT NULL,
  schema_json TEXT NOT NULL,
  column_mapping_json TEXT NOT NULL,
  row_count BIGINT NOT NULL,
  data_version INTEGER NOT NULL DEFAULT 1,
  data_updated_at TEXT NOT NULL,
  privacy_no_samples INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE (workspace_id, table_name)
)""",
    """CREATE TABLE dataset_profiles (
  dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
  data_version INTEGER NOT NULL,
  columns_json TEXT NOT NULL,
  quality_json TEXT NOT NULL,
  computed_at TEXT NOT NULL
)""",
    """CREATE TABLE relations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  candidate_key TEXT NOT NULL,
  from_dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  from_column TEXT NOT NULL,
  to_dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  to_column TEXT NOT NULL,
  cardinality TEXT NOT NULL CHECK (cardinality IN ('one_to_one','one_to_many','many_to_many')),
  overlap_pct DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('candidate','confirmed','rejected','deleted')),
  created_at TEXT NOT NULL,
  decided_at TEXT,
  UNIQUE (workspace_id, candidate_key)
)""",
    """CREATE TABLE queries (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  sql TEXT NOT NULL,
  tables_used_json TEXT NOT NULL,
  dataset_ids_json TEXT NOT NULL,
  relations_used_json TEXT NOT NULL,
  lineage_json TEXT NOT NULL,
  output_schema_json TEXT NOT NULL,
  result_snapshot_json TEXT NOT NULL,
  row_count BIGINT NOT NULL,
  filters_json TEXT NOT NULL,
  executed_at TEXT NOT NULL,
  created_by TEXT NOT NULL CHECK (created_by IN ('agent','user','system'))
)""",
    """CREATE TABLE dashboards (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  owner_id TEXT NOT NULL DEFAULT 'local',
  title TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 0,
  content_json TEXT NOT NULL,
  undo_stack_json TEXT NOT NULL DEFAULT '[]',
  redo_stack_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)""",
    """CREATE TABLE patch_events (
  id TEXT PRIMARY KEY,
  dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  base_version INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('agent','user')),
  kind TEXT NOT NULL CHECK (kind IN ('normal','undo','redo')),
  target_patch_id TEXT REFERENCES patch_events(id),
  command_json TEXT NOT NULL,
  ops_json TEXT NOT NULL,
  inverse_ops_json TEXT NOT NULL,
  actor_run_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (dashboard_id, version)
)""",
    """CREATE TABLE chat_sessions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_agent_version INTEGER NOT NULL DEFAULT 0
)""",
    """CREATE TABLE proposals (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','expired')),
  created_at TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'changes',
  payload_json TEXT
)""",
    """CREATE TABLE semantic_entries (
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
)""",
    "CREATE INDEX idx_semantic_entries_ws_kind ON semantic_entries (workspace_id, kind, status)",
    """CREATE TABLE semantic_meta (
  workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  domain TEXT,
  domain_confidence DOUBLE PRECISION,
  assumptions_json TEXT NOT NULL DEFAULT '[]',
  semantic_version INTEGER NOT NULL DEFAULT 0
)""",
    """CREATE TABLE semantic_draft_runs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  trigger_dataset_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','done','llm_failed')),
  discarded_json TEXT NOT NULL DEFAULT '[]',
  started_at TEXT NOT NULL,
  finished_at TEXT
)""",
    """CREATE TABLE dashboard_blueprints (
  id TEXT PRIMARY KEY,
  dashboard_id TEXT NOT NULL REFERENCES dashboards(id) ON DELETE CASCADE,
  proposal_id TEXT REFERENCES proposals(id) ON DELETE SET NULL,
  blueprint_json TEXT NOT NULL,
  slot_status_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','completed','stopped')),
  created_at TEXT NOT NULL,
  finished_at TEXT
)""",
]

DROP_ORDER = [
    "dashboard_blueprints", "semantic_draft_runs", "semantic_meta", "semantic_entries",
    "proposals", "chat_sessions", "patch_events", "dashboards", "queries", "relations",
    "dataset_profiles", "datasets", "uploads", "workspaces",
]


def upgrade() -> None:
    for ddl in TABLES:
        op.execute(ddl)


def downgrade() -> None:
    for table in DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
