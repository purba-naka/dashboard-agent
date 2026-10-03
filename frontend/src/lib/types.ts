/**
 * Tipe bersama Frontend yang mencerminkan model domain dan kontrak REST/SSE
 * di design.md (bagian "Data Models" dan "REST & SSE API contracts").
 *
 * Konvensi:
 * - Nama field mengikuti JSON backend (snake_case).
 * - Timestamp adalah string ISO-8601 UTC; id adalah ULID (string).
 * - `Op` didiskriminasi oleh field `op`, `Command` oleh field `type`.
 */

// ---------------------------------------------------------------------------
// Primitif
// ---------------------------------------------------------------------------

export type Scalar = string | number | boolean | null;

/** ISO-8601 UTC, mis. "2024-01-31T10:00:00Z". */
export type IsoDateTime = string;

/** Tanggal ISO-8601, mis. "2024-01-31". */
export type IsoDate = string;

export type LogicalType = "integer" | "float" | "string" | "boolean" | "date" | "datetime";

export type ColumnRole = "dimension" | "measure" | "time" | "identifier";

export interface ColumnInfo {
  name: string;
  type: LogicalType;
}

// ---------------------------------------------------------------------------
// Workspace, Dataset, profil, relasi
// ---------------------------------------------------------------------------

export interface Workspace {
  id: string;
  owner_id: string; // selalu "local"
  name: string;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

/** Item `GET /workspaces`: Workspace + ringkasan isi. */
export interface WorkspaceSummary extends Workspace {
  dataset_count: number;
  dashboard_count: number;
  /** Terbaru dari rename, data Dataset, atau edit Dashboard. */
  last_activity_at: IsoDateTime;
}

export interface ColumnMapping {
  original: string;
  normalized: string;
}

export interface Dataset {
  id: string;
  workspace_id: string;
  owner_id: string;
  upload_id: string | null;
  table_name: string;
  source_name: string;
  sheet_name: string | null;
  schema: ColumnInfo[];
  column_mapping: ColumnMapping[];
  row_count: number;
  data_version: number;
  data_updated_at: IsoDateTime;
  privacy_no_samples: boolean;
  created_at: IsoDateTime;
}

export interface ColumnProfile {
  name: string;
  type: LogicalType;
  role: ColumnRole;
  null_count: number;
  null_pct: number;
  distinct_count: number;
  min: Scalar;
  max: Scalar;
  mean: number | null;
  /** Maks 5 pasangan [nilai, jumlah]. */
  top_values: Array<[Scalar, number]>;
}

export interface DatasetQuality {
  duplicate_rows: number;
  mixed_type_columns: string[];
  null_pct: Record<string, number>;
}

export interface DatasetDetail {
  dataset: Dataset;
  schema: ColumnInfo[];
  column_profiles: ColumnProfile[];
  quality: DatasetQuality;
  column_mapping: ColumnMapping[];
}

export type RelationCardinality = "one_to_one" | "one_to_many" | "many_to_many";

export type RelationStatus = "candidate" | "confirmed" | "rejected" | "deleted";

export interface Relation {
  id: string;
  workspace_id: string;
  /** "tA.cA|tB.cB" kanonik. */
  candidate_key: string;
  from_dataset_id: string;
  /** Nama tabel SQL sisi sumber. */
  from_table?: string;
  from_column: string;
  to_dataset_id: string;
  /** Nama tabel SQL sisi tujuan. */
  to_table?: string;
  to_column: string;
  cardinality: RelationCardinality;
  overlap_pct: number;
  status: RelationStatus;
  created_at: IsoDateTime;
  decided_at: IsoDateTime | null;
}

export interface ChatSession {
  id: string;
  workspace_id: string;
  title: string;
  created_at: IsoDateTime;
  last_agent_version: number;
}

export interface DashboardSummary {
  id: string;
  workspace_id: string;
  title: string;
  version: number;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface WorkspaceDetail {
  workspace: Workspace;
  datasets: Dataset[];
  dashboards: DashboardSummary[];
  chat_sessions: ChatSession[];
}

// ---------------------------------------------------------------------------
// Filter
// ---------------------------------------------------------------------------

interface PredicateBase {
  table: string;
  column: string;
}

/** Rentang tanggal inklusif; salah satu ujung boleh null. */
export interface DateRangePredicate extends PredicateBase {
  kind: "date_range";
  start: IsoDate | null;
  end: IsoDate | null;
}

/** Keanggotaan himpunan; `values` diperlakukan sebagai himpunan (tanpa urutan). */
export interface InPredicate extends PredicateBase {
  kind: "in";
  values: Scalar[];
}

export type Predicate = DateRangePredicate | InPredicate;

/** Himpunan predikat (konjungsi). */
export type FilterSet = Predicate[];

// ---------------------------------------------------------------------------
// Item Dashboard
// ---------------------------------------------------------------------------

export type ChartType = "line" | "bar" | "pie" | "scatter" | "heatmap";

export interface ChartSpec {
  spec_version: 1;
  query_id: string;
  chart_type: ChartType;
  /** Opsi ECharts tanpa data inline; `dataset` ditambahkan backend saat render. */
  option: Record<string, unknown>;
  cross_filter_column: string | null;
}

export interface ChartItem {
  id: string;
  kind: "chart";
  title: string;
  spec: ChartSpec;
}

export interface EvidenceTable {
  columns: ColumnInfo[];
  /** Maks 200 baris. */
  rows: Scalar[][];
  row_count: number;
}

export interface NumberMatch {
  token: string;
  value: number;
  row: number | null;
  column: string | null;
}

export type InsightType =
  | "trend"
  | "anomaly"
  | "comparison"
  | "top_bottom_contributors"
  | "cross_dataset_correlation";

export interface InsightItem {
  id: string;
  kind: "insight";
  insight_type: InsightType;
  title: string;
  text: string;
  query_id: string;
  sql: string;
  evidence: EvidenceTable;
  matched_numbers: NumberMatch[];
  filters_snapshot: FilterSet;
  dataset_ids: string[];
  computed_at: IsoDateTime;
  dataset_versions: Record<string, number>;
}

export type NumberStyle = "number" | "currency" | "percent";
export type GoodDirection = "up" | "down" | "neutral";

export interface NumberFormat {
  style: NumberStyle;
  currency: string | null;
  decimals: number;
  compact: boolean;
}

export interface KpiSpec {
  spec_version: 1;
  query_id: string;
  value_column: string;
  comparison_column: string | null;
  comparison_label: string | null;
  format: NumberFormat;
  good_direction: GoodDirection;
  metric_name: string | null;
}

/** KPI_Card (Req 38). */
export interface KpiItem {
  id: string;
  kind: "kpi";
  title: string;
  spec: KpiSpec;
}

export type DashboardItem = ChartItem | InsightItem | KpiItem;

export type SectionRole =
  | "kpi_row"
  | "trend"
  | "breakdown"
  | "composition"
  | "distribution"
  | "detail"
  | "other";

export type VisualType =
  | "kpi"
  | "line"
  | "bar"
  | "pie"
  | "scatter"
  | "heatmap"
  | "waterfall"
  | "insight";

export type TimeGrain = "day" | "week" | "month" | "quarter" | "year";

export interface BriefKpi {
  metric: string;
  compare: "previous_period" | "target" | "none";
}

/** Design_Brief (Req 36). */
export interface DesignBrief {
  purpose: string;
  audience: string;
  key_questions: string[];
  kpis: BriefKpi[];
  sections: SectionRole[];
  time_grain: TimeGrain | null;
  assumptions: string[];
}

export interface BlueprintSlot {
  slot_id: string;
  section: SectionRole;
  purpose: string;
  visual: VisualType;
  metrics: string[];
  dimension: string | null;
  layout: LayoutRect | null;
  cross_filter_column: string | null;
}

/** Dashboard_Blueprint (Req 37). */
export interface DashboardBlueprint {
  brief: DesignBrief;
  slots: BlueprintSlot[];
  default_filters: FilterSet;
}

/** Grid 12 kolom; w, h ≥ 1. */
export interface LayoutRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Invariant: keys(layout) == keys(items). */
export interface DashboardContent {
  title: string;
  items: Record<string, DashboardItem>;
  layout: Record<string, LayoutRect>;
  global_filters: FilterSet;
  /** Design_Brief opsional; konten lama tidak memiliki field ini. */
  brief?: DesignBrief | null;
}

// ---------------------------------------------------------------------------
// Ops (hasil resolve command, self-contained) & Command
// ---------------------------------------------------------------------------

export interface AddItemOp {
  op: "add_item";
  item: DashboardItem;
  layout: LayoutRect;
}

export interface RemoveItemOp {
  op: "remove_item";
  item: DashboardItem;
  layout: LayoutRect;
}

export interface SetItemOp {
  op: "set_item";
  id: string;
  before: DashboardItem;
  after: DashboardItem;
}

export interface LayoutChange {
  id: string;
  before: LayoutRect;
  after: LayoutRect;
}

export interface SetLayoutOp {
  op: "set_layout";
  changes: LayoutChange[];
}

export interface SetFiltersOp {
  op: "set_filters";
  before: FilterSet;
  after: FilterSet;
}

export interface SetTitleOp {
  op: "set_title";
  before: string;
  after: string;
}

export interface SetBriefOp {
  op: "set_brief";
  before: DesignBrief | null;
  after: DesignBrief | null;
}

export type Op =
  | AddItemOp
  | RemoveItemOp
  | SetItemOp
  | SetLayoutOp
  | SetFiltersOp
  | SetTitleOp
  | SetBriefOp;

export type OpKind = Op["op"];

/** Command dari Canvas_Editor / Agent_Tools (`POST /dashboards/{id}/patches`). */
export type Command =
  | { type: "add_chart"; title: string; spec: ChartSpec; layout?: LayoutRect }
  | { type: "add_insight"; insight: Omit<InsightItem, "id" | "kind">; layout?: LayoutRect }
  | { type: "update_chart"; id: string; spec: ChartSpec; title?: string }
  | { type: "change_chart_type"; id: string; chart_type: ChartType }
  | { type: "update_insight"; id: string; changes: Partial<Omit<InsightItem, "id" | "kind">> }
  | { type: "remove_item"; id: string }
  | { type: "set_layout"; changes: Record<string, LayoutRect> }
  | { type: "set_global_filters"; filters: FilterSet }
  | { type: "set_title"; title: string }
  | { type: "add_kpi"; title: string; spec: KpiSpec; layout?: LayoutRect }
  | { type: "update_kpi"; id: string; spec: KpiSpec; title?: string }
  | { type: "set_brief"; brief: DesignBrief | null };

export type CommandType = Command["type"];

// ---------------------------------------------------------------------------
// Patch_Event & snapshot
// ---------------------------------------------------------------------------

export type PatchSource = "agent" | "user";

export type PatchKind = "normal" | "undo" | "redo";

export interface PatchEvent {
  id: string;
  dashboard_id: string;
  /** Versi SETELAH patch; selalu base_version + 1. */
  version: number;
  base_version: number;
  source: PatchSource;
  kind: PatchKind;
  target_patch_id: string | null;
  ops: Op[];
  inverse_ops: Op[];
  created_at: IsoDateTime;
}

export interface ItemStatus {
  invalid: boolean;
  stale: boolean;
}

export interface DashboardSnapshot {
  id: string;
  title: string;
  version: number;
  content: DashboardContent;
  can_undo: boolean;
  can_redo: boolean;
  item_status: Record<string, ItemStatus>;
}

// ---------------------------------------------------------------------------
// Kontrak REST
// ---------------------------------------------------------------------------

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  };
}

export interface CreateWorkspaceRequest {
  name: string;
}

export interface RenameWorkspaceRequest {
  name: string;
}

export interface DeleteWorkspaceRequest {
  confirm_name: string;
}

export interface SheetInfo {
  name: string;
  rows_hint: number | null;
}

/** `POST /workspaces/{ws}/uploads` — CSV (202). */
export interface CsvUploadResponse {
  upload_id: string;
  job_id: string;
}

/** `POST /workspaces/{ws}/uploads` — XLSX (200). */
export interface XlsxUploadResponse {
  upload_id: string;
  sheets: SheetInfo[];
}

export type UploadResponse = CsvUploadResponse | XlsxUploadResponse;

export interface SelectSheetsRequest {
  sheets: string[];
}

export interface SelectSheetsResponse {
  jobs: Array<{ sheet: string; job_id: string }>;
}

export type JobState = "queued" | "running" | "done" | "failed";

export interface JobStatus {
  status: JobState;
  progress: number;
  dataset_id?: string;
  error?: ErrorEnvelope["error"];
}

export interface UpdateDatasetRequest {
  privacy_no_samples?: boolean;
}

export interface ReuploadResponse {
  job_id: string;
}

export interface SchemaMismatchDetails {
  missing: string[];
  added: string[];
  changed: Array<{ name: string; old_type: LogicalType; new_type: LogicalType }>;
}

export interface CreateDashboardRequest {
  title: string;
}

export interface PatchesSinceResponse {
  patches?: PatchEvent[];
  version?: number;
  /** Dikirim bila riwayat tidak tersedia. */
  snapshot?: DashboardSnapshot;
}

export interface ApplyPatchRequest {
  base_version: number;
  command: Command;
}

export interface UndoRedoRequest {
  base_version: number;
}

export interface VersionConflictDetails {
  current_version: number;
}

export interface RenderRequest {
  item_ids?: string[];
  cross_filters: Predicate[];
}

export type RenderItemStatus = "ok" | "invalid" | "stale" | "error";

export type KpiSentiment = "positive" | "negative" | "neutral";

/** Hasil render KPI_Card (dihitung backend tanpa LLM, Req 38.4). */
export interface RenderedKpi {
  value: number | null;
  comparison: number | null;
  delta: number | null;
  delta_pct: number | null;
  sentiment: KpiSentiment;
  formatted: {
    value: string | null;
    comparison: string | null;
    delta: string | null;
    delta_pct: string | null;
  };
}

export interface RenderedItem {
  option?: Record<string, unknown>;
  kpi?: RenderedKpi;
  status: RenderItemStatus;
  filter_unaffected: boolean;
  error?: ErrorEnvelope["error"];
}

export interface RenderResponse {
  version: number;
  items: Record<string, RenderedItem>;
}

export interface RefreshInsightRequest {
  base_version: number;
}

export interface QueryDetail {
  sql: string;
  columns: ColumnInfo[];
  /** Snapshot ≤ 1.000 baris. */
  rows: Scalar[][];
  row_count: number;
  executed_at: IsoDateTime;
  filters: FilterSet;
}

export interface ChatRequest {
  session_id?: string;
  message: string;
  /** `selected_slot_ids` untuk persetujuan Blueprint per slot (Req 37.6). */
  approval?: { proposal_id: string; selected_slot_ids?: string[] };
  /** Halaman (Dashboard) yang sedang dilihat; kosong = yang terakhir diubah. */
  dashboard_id?: string;
}

// ---------------------------------------------------------------------------
// Semantic_Model (Req 31, 32)
// ---------------------------------------------------------------------------

export type SemanticKind = "column" | "metric" | "term" | "instruction" | "verified_query";
export type SemanticStatus = "candidate" | "confirmed" | "rejected";

export interface SemanticEntry {
  id: string;
  kind: SemanticKind;
  entry_key: string;
  status: SemanticStatus;
  source: "auto" | "user";
  dataset_id: string | null;
  body: Record<string, unknown>;
  valid: boolean;
  updated_at: IsoDateTime;
}

export interface SemanticDraftRun {
  id: string;
  status: "running" | "done" | "llm_failed";
  discarded: { entry_key: string; reason: string }[];
  started_at: IsoDateTime;
  finished_at: IsoDateTime | null;
}

export interface SemanticModelResponse {
  domain: string | null;
  domain_confidence: number | null;
  assumptions: string[];
  semantic_version: number;
  entries: SemanticEntry[];
  draft_run: SemanticDraftRun | null;
}

export interface SemanticImportIssue {
  path: string;
  entry_key: string | null;
  reason: string;
}

export interface SemanticDraftCardData {
  run_id: string | null;
  domain: string | null;
  summary: string;
  metrics: { id: string; name: string; label: string; expr: string; status: SemanticStatus }[];
  columns_highlight: {
    id: string;
    table: string;
    column: string;
    label: string;
    description: string;
    status: SemanticStatus;
  }[];
  assumptions: string[];
}

export type BlueprintSlotStatus = "pending" | "building" | "done" | "failed" | "skipped";

export interface BlueprintProgress {
  blueprint_id: string;
  slot_id: string;
  status: BlueprintSlotStatus;
  item_id?: string;
  error?: { message?: string; code?: string };
}

export interface ReviewFinding {
  code: string;
  severity: "info" | "warning";
  item_ids: string[];
  message: string;
  suggestion: string;
}

export interface BlueprintRecordResponse {
  id: string;
  status: "active" | "completed" | "stopped";
  blueprint: DashboardBlueprint;
  slot_status: Record<string, { status: BlueprintSlotStatus; item_id?: string; error?: unknown }>;
  created_at: IsoDateTime;
  finished_at: IsoDateTime | null;
}

// ---------------------------------------------------------------------------
// Event SSE
// ---------------------------------------------------------------------------

export type AgentName =
  | "root"
  | "data_profiler"
  | "query"
  | "chart_designer"
  | "insight"
  | (string & {});

/** Event stream chat (`POST /workspaces/{ws}/chat`). */
export type ChatSseEvent =
  | { event: "run.started"; data: { run_id: string; session_id: string } }
  | { event: "agent.active"; data: { agent: AgentName } }
  | { event: "text.delta"; data: { agent: AgentName; text: string } }
  | { event: "thought.delta"; data: { agent: AgentName; text: string } }
  | { event: "tool.call"; data: { agent: AgentName; tool: string; args_summary: string } }
  | { event: "tool.result"; data: { agent: AgentName; tool: string; ok: boolean; summary: string } }
  | { event: "patch.applied"; data: PatchEvent }
  | {
      event: "approval.request";
      data: {
        proposal_id: string;
        summary: string;
        themes: string[];
        kind?: "changes" | "blueprint";
        blueprint?: DashboardBlueprint;
      };
    }
  | { event: "relation.candidates"; data: Relation[] }
  | { event: "profile.summary"; data: { dataset_id: string; summary: string } }
  | { event: "semantic.draft"; data: SemanticDraftCardData }
  | { event: "blueprint.progress"; data: BlueprintProgress }
  | { event: "review.findings"; data: { findings: ReviewFinding[] } }
  | { event: "error"; data: { code: string; message: string; agent?: AgentName } }
  | { event: "run.stopped"; data: { run_id: string } }
  | { event: "run.done"; data: { run_id: string } };

export type ChatSseEventType = ChatSseEvent["event"];

/** Event stream workspace (`GET /workspaces/{ws}/events`). */
export type WorkspaceSseEvent =
  | { event: "patch.applied"; data: PatchEvent }
  | { event: "job.progress"; data: { job_id: string; progress: number } }
  | { event: "job.done"; data: { job_id: string; dataset_id: string } }
  | { event: "job.failed"; data: { job_id: string; error: ErrorEnvelope["error"] } }
  | { event: "dataset.profiled"; data: { dataset_id: string } }
  | { event: "relation.updated"; data: Relation | { relation_ids: string[] } }
  | { event: "semantic.updated"; data: Record<string, unknown> }
  | { event: "semantic.warning"; data: { run_id: string; reason: string } }
  /** Dikirim server (tanpa `id`) bila replay `Last-Event-ID` tidak lengkap: refetch state via REST. */
  | { event: "resync"; data: { reason: "replay_incomplete"; last_event_id: string | null } };

export type WorkspaceSseEventType = WorkspaceSseEvent["event"];

/** Event SSE mentah beserta sequence id (`id:`) untuk `Last-Event-ID`. */
export type SseEnvelope<E extends { event: string; data: unknown }> = E & { id?: string };
