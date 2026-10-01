/**
 * Klien REST bertipe untuk Backend_API (design.md "REST & SSE API contracts").
 *
 * - Base URL: `NEXT_PUBLIC_API_URL` (alias `NEXT_PUBLIC_API_BASE_URL`), default
 *   `http://127.0.0.1:8000`. Semua endpoint berada di bawah prefix `/api`.
 * - Error non-2xx diparse dari envelope `{error: {code, message, details}}`
 *   menjadi {@link ApiError}; kegagalan jaringan → `ApiError` status 0
 *   `NETWORK_ERROR`. Pembatalan lewat `AbortSignal` tetap dilempar sebagai
 *   error `AbortError` (cek dengan {@link isAbortError}).
 * - Upload memakai `XMLHttpRequest` agar progres upload dapat dilaporkan
 *   (Req 2.1, 5.3); fallback ke `fetch` bila XHR tidak tersedia (SSR/test).
 * - Stream chat (`POST /workspaces/{ws}/chat`) dan EventSource workspace
 *   ditangani `sse.ts`; modul ini hanya menyediakan URL-nya.
 */

import type {
  ApplyPatchRequest,
  Command,
  CreateDashboardRequest,
  CreateWorkspaceRequest,
  DashboardSnapshot,
  DashboardSummary,
  Dataset,
  DatasetDetail,
  DeleteWorkspaceRequest,
  ErrorEnvelope,
  JobStatus,
  PatchEvent,
  PatchesSinceResponse,
  QueryDetail,
  RefreshInsightRequest,
  Relation,
  RelationStatus,
  RenameWorkspaceRequest,
  RenderRequest,
  RenderResponse,
  ReuploadResponse,
  SchemaMismatchDetails,
  SelectSheetsRequest,
  SelectSheetsResponse,
  UndoRedoRequest,
  UpdateDatasetRequest,
  UploadResponse,
  Workspace,
  WorkspaceDetail,
  XlsxUploadResponse,
  BlueprintRecordResponse,
  SemanticEntry,
  SemanticImportIssue,
  SemanticKind,
  SemanticModelResponse,
} from "./types";

// ---------------------------------------------------------------------------
// Konfigurasi & URL
// ---------------------------------------------------------------------------

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

/** Origin Backend_API tanpa garis miring akhir (mis. `http://127.0.0.1:8000`). */
export const API_BASE_URL: string = (
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

/** Prefix router backend. */
export const API_PREFIX = "/api";

export type QueryValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | ReadonlyArray<string | number | boolean>;

export type QueryParams = Record<string, QueryValue>;

/** Encode satu segmen path (id/nama dari pengguna tidak boleh memecah path). */
const seg = (value: string): string => encodeURIComponent(value);

/**
 * URL absolut untuk `path` di bawah `/api`. Nilai `null`/`undefined` dilewati;
 * array menjadi parameter berulang (`?status=a&status=b`).
 */
export function apiUrl(path: string, query?: QueryParams): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  let url = `${API_BASE_URL}${API_PREFIX}${normalized}`;
  if (query) {
    const params = new URLSearchParams();
    for (const [key, raw] of Object.entries(query)) {
      if (raw === null || raw === undefined) continue;
      const values = Array.isArray(raw) ? raw : [raw];
      for (const v of values) params.append(key, String(v));
    }
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

export type ApiErrorDetails = Record<string, unknown>;

/** Error dari Backend_API (envelope `{error: {code, message, details}}`). */
export class ApiError extends Error {
  /** HTTP status; `0` untuk kegagalan jaringan. */
  readonly status: number;
  readonly code: string;
  readonly details: ApiErrorDetails;

  constructor(status: number, code: string, message: string, details: ApiErrorDetails = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  toEnvelope(): ErrorEnvelope["error"] {
    return { code: this.code, message: this.message, details: this.details };
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

/** `true` bila `err` berasal dari pembatalan `AbortSignal`. */
export function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    (err as { name?: unknown }).name === "AbortError"
  );
}

/**
 * `current_version` bila `err` adalah 409 `VERSION_CONFLICT` (Req 18.5),
 * selain itu `null`.
 */
export function isVersionConflict(err: unknown): number | null {
  if (!(err instanceof ApiError) || err.code !== "VERSION_CONFLICT") return null;
  const v = err.details.current_version;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Detail `SCHEMA_MISMATCH` (409 re-upload, Req 26.3) atau `null`. */
export function isSchemaMismatch(err: unknown): SchemaMismatchDetails | null {
  if (!(err instanceof ApiError) || err.code !== "SCHEMA_MISMATCH") return null;
  const d = err.details as Partial<SchemaMismatchDetails>;
  return {
    missing: Array.isArray(d.missing) ? d.missing : [],
    added: Array.isArray(d.added) ? d.added : [],
    changed: Array.isArray(d.changed) ? d.changed : [],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Bangun {@link ApiError} dari body respons error (envelope atau bukan). */
export function parseApiError(status: number, bodyText: string, statusText = ""): ApiError {
  let parsed: unknown;
  try {
    parsed = bodyText ? JSON.parse(bodyText) : undefined;
  } catch {
    parsed = undefined;
  }
  if (isRecord(parsed) && isRecord(parsed.error) && typeof parsed.error.code === "string") {
    const { code, message, details } = parsed.error;
    return new ApiError(
      status,
      code,
      typeof message === "string" && message ? message : code,
      isRecord(details) ? details : {},
    );
  }
  const fallback = statusText || (bodyText && bodyText.length <= 200 ? bodyText : "");
  return new ApiError(status, `HTTP_${status}`, fallback || `Permintaan gagal (HTTP ${status}).`);
}

function networkError(cause: unknown): ApiError {
  return new ApiError(0, "NETWORK_ERROR", "Tidak dapat terhubung ke server.", {
    cause: cause instanceof Error ? cause.message : String(cause),
  });
}

function abortError(signal?: AbortSignal): unknown {
  if (signal && isAbortError(signal.reason)) return signal.reason;
  return new DOMException("Permintaan dibatalkan.", "AbortError");
}

function parseSuccessBody<T>(status: number, text: string): T {
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(status, "INVALID_RESPONSE", "Respons server bukan JSON yang valid.");
  }
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

export interface RequestOptions {
  signal?: AbortSignal;
}

type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

interface InternalRequest extends RequestOptions {
  method: HttpMethod;
  body?: unknown;
  query?: QueryParams;
}

/** Kirim request JSON (atau `FormData`) dan parse respons; 204 → `undefined`. */
async function request<T>(path: string, init: InternalRequest): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (typeof FormData !== "undefined" && init.body instanceof FormData) {
    body = init.body;
  } else if (init.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.body);
  }

  let res: Response;
  try {
    res = await fetch(apiUrl(path, init.query), {
      method: init.method,
      headers,
      body,
      signal: init.signal,
    });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw networkError(err);
  }

  let text: string;
  try {
    text = await res.text();
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw networkError(err);
  }
  if (!res.ok) throw parseApiError(res.status, text, res.statusText);
  return parseSuccessBody<T>(res.status, text);
}

export interface UploadProgress {
  loaded: number;
  /** `null` bila ukuran total tidak diketahui. */
  total: number | null;
  /** 0–1, atau `null` bila total tidak diketahui. */
  fraction: number | null;
}

export interface UploadOptions extends RequestOptions {
  onProgress?: (progress: UploadProgress) => void;
}

/** POST multipart dengan progres upload via `XMLHttpRequest`. */
function sendMultipart<T>(path: string, form: FormData, opts: UploadOptions = {}): Promise<T> {
  const { signal, onProgress } = opts;
  if (typeof XMLHttpRequest === "undefined") {
    return request<T>(path, { method: "POST", body: form, signal });
  }
  return new Promise<T>((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError(signal));
      return;
    }
    const xhr = new XMLHttpRequest();
    const onSignalAbort = () => xhr.abort();
    const cleanup = () => signal?.removeEventListener("abort", onSignalAbort);

    xhr.open("POST", apiUrl(path));
    xhr.setRequestHeader("Accept", "application/json");

    if (onProgress) {
      xhr.upload.onprogress = (e: ProgressEvent) => {
        const total = e.lengthComputable && e.total > 0 ? e.total : null;
        onProgress({
          loaded: e.loaded,
          total,
          fraction: total === null ? null : Math.min(1, e.loaded / total),
        });
      };
    }

    xhr.onload = () => {
      cleanup();
      const text = typeof xhr.responseText === "string" ? xhr.responseText : "";
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(parseSuccessBody<T>(xhr.status, text));
        } catch (err) {
          reject(err);
        }
      } else {
        reject(parseApiError(xhr.status, text, xhr.statusText));
      }
    };
    xhr.onerror = () => {
      cleanup();
      reject(networkError(new Error("XMLHttpRequest error")));
    };
    xhr.ontimeout = () => {
      cleanup();
      reject(new ApiError(0, "TIMEOUT", "Upload melewati batas waktu."));
    };
    xhr.onabort = () => {
      cleanup();
      reject(abortError(signal));
    };

    signal?.addEventListener("abort", onSignalAbort, { once: true });
    xhr.send(form);
  });
}

// ---------------------------------------------------------------------------
// Workspace (Req 1)
// ---------------------------------------------------------------------------

export function listWorkspaces(opts: RequestOptions = {}): Promise<Workspace[]> {
  return request("/workspaces", { method: "GET", ...opts });
}

export function createWorkspace(name: string, opts: RequestOptions = {}): Promise<Workspace> {
  const body: CreateWorkspaceRequest = { name };
  return request("/workspaces", { method: "POST", body, ...opts });
}

/** Workspace beserta Dataset, Dashboard, dan sesi chat (Req 1.2). */
export function getWorkspace(ws: string, opts: RequestOptions = {}): Promise<WorkspaceDetail> {
  return request(`/workspaces/${seg(ws)}`, { method: "GET", ...opts });
}

export function renameWorkspace(
  ws: string,
  name: string,
  opts: RequestOptions = {},
): Promise<Workspace> {
  const body: RenameWorkspaceRequest = { name };
  return request(`/workspaces/${seg(ws)}`, { method: "PATCH", body, ...opts });
}

/** Hapus Workspace; `confirmName` harus sama persis dengan nama (Req 1.4). */
export function deleteWorkspace(
  ws: string,
  confirmName: string,
  opts: RequestOptions = {},
): Promise<void> {
  const body: DeleteWorkspaceRequest = { confirm_name: confirmName };
  return request(`/workspaces/${seg(ws)}`, { method: "DELETE", body, ...opts });
}

// ---------------------------------------------------------------------------
// Upload & job (Req 2, 3, 5)
// ---------------------------------------------------------------------------

/** Respons upload XLSX membawa daftar sheet; CSV membawa `job_id`. */
export function isXlsxUpload(res: UploadResponse): res is XlsxUploadResponse {
  return "sheets" in res;
}

/** Upload CSV (202 `{upload_id, job_id}`) atau XLSX (200 `{upload_id, sheets}`). */
export function uploadFile(
  ws: string,
  file: File,
  opts: UploadOptions = {},
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  return sendMultipart(`/workspaces/${seg(ws)}/uploads`, form, opts);
}

/** Pilih sheet XLSX; satu job konversi per sheet (Req 3.2). */
export function selectSheets(
  ws: string,
  uploadId: string,
  sheets: string[],
  opts: RequestOptions = {},
): Promise<SelectSheetsResponse> {
  const body: SelectSheetsRequest = { sheets };
  return request(`/workspaces/${seg(ws)}/uploads/${seg(uploadId)}/sheets`, {
    method: "POST",
    body,
    ...opts,
  });
}

export function getJob(jobId: string, opts: RequestOptions = {}): Promise<JobStatus> {
  return request(`/jobs/${seg(jobId)}`, { method: "GET", ...opts });
}

// ---------------------------------------------------------------------------
// Dataset (Req 6, 26, 27)
// ---------------------------------------------------------------------------

export function listDatasets(ws: string, opts: RequestOptions = {}): Promise<Dataset[]> {
  return request(`/workspaces/${seg(ws)}/datasets`, { method: "GET", ...opts });
}

export function getDataset(
  ws: string,
  datasetId: string,
  opts: RequestOptions = {},
): Promise<DatasetDetail> {
  return request(`/workspaces/${seg(ws)}/datasets/${seg(datasetId)}`, { method: "GET", ...opts });
}

export function updateDataset(
  ws: string,
  datasetId: string,
  changes: UpdateDatasetRequest,
  opts: RequestOptions = {},
): Promise<Dataset> {
  return request(`/workspaces/${seg(ws)}/datasets/${seg(datasetId)}`, {
    method: "PATCH",
    body: changes,
    ...opts,
  });
}

export interface ReuploadOptions extends UploadOptions {
  /** Sheet XLSX; default `dataset.sheet_name` lalu sheet pertama. */
  sheet?: string;
}

/**
 * Re-upload data Dataset (Req 26.1). Skema tidak cocok → `ApiError` 409
 * `SCHEMA_MISMATCH` (lihat {@link isSchemaMismatch}).
 */
export function reuploadDataset(
  ws: string,
  datasetId: string,
  file: File,
  opts: ReuploadOptions = {},
): Promise<ReuploadResponse> {
  const { sheet, ...rest } = opts;
  const form = new FormData();
  form.append("file", file, file.name);
  if (sheet) form.append("sheet", sheet);
  return sendMultipart(`/workspaces/${seg(ws)}/datasets/${seg(datasetId)}/reupload`, form, rest);
}

// ---------------------------------------------------------------------------
// Relasi (Req 7)
// ---------------------------------------------------------------------------

export type RelationStatusFilter = Exclude<RelationStatus, "deleted">;

export function listRelations(
  ws: string,
  status?: RelationStatusFilter | RelationStatusFilter[],
  opts: RequestOptions = {},
): Promise<Relation[]> {
  return request(`/workspaces/${seg(ws)}/relations`, {
    method: "GET",
    query: { status },
    ...opts,
  });
}

/** Jalankan ulang deteksi; mengembalikan kandidat Workspace. */
export function detectRelations(ws: string, opts: RequestOptions = {}): Promise<Relation[]> {
  return request(`/workspaces/${seg(ws)}/relations/detect`, { method: "POST", ...opts });
}

export function confirmRelation(
  ws: string,
  relationId: string,
  opts: RequestOptions = {},
): Promise<Relation> {
  return request(`/workspaces/${seg(ws)}/relations/${seg(relationId)}/confirm`, {
    method: "POST",
    ...opts,
  });
}

export function rejectRelation(
  ws: string,
  relationId: string,
  opts: RequestOptions = {},
): Promise<Relation> {
  return request(`/workspaces/${seg(ws)}/relations/${seg(relationId)}/reject`, {
    method: "POST",
    ...opts,
  });
}

export function deleteRelation(
  ws: string,
  relationId: string,
  opts: RequestOptions = {},
): Promise<void> {
  return request(`/workspaces/${seg(ws)}/relations/${seg(relationId)}`, {
    method: "DELETE",
    ...opts,
  });
}

// ---------------------------------------------------------------------------
// Dashboard, patch, undo/redo, render (Req 17–19, 22, 24)
// ---------------------------------------------------------------------------

export function createDashboard(
  ws: string,
  title: string,
  opts: RequestOptions = {},
): Promise<DashboardSnapshot> {
  const body: CreateDashboardRequest = { title };
  return request(`/workspaces/${seg(ws)}/dashboards`, { method: "POST", body, ...opts });
}

export function listDashboards(ws: string, opts: RequestOptions = {}): Promise<DashboardSummary[]> {
  return request(`/workspaces/${seg(ws)}/dashboards`, { method: "GET", ...opts });
}

export function getDashboard(
  dashboardId: string,
  opts: RequestOptions = {},
): Promise<DashboardSnapshot> {
  return request(`/dashboards/${seg(dashboardId)}`, { method: "GET", ...opts });
}

/**
 * Patch_Event dengan `version > sinceVersion` (resync SSE, Req 16.5). Bila
 * riwayat tidak dapat menyambung, respons berisi `snapshot`.
 */
export function getPatchesSince(
  dashboardId: string,
  sinceVersion: number,
  opts: RequestOptions = {},
): Promise<PatchesSinceResponse> {
  return request(`/dashboards/${seg(dashboardId)}/patches`, {
    method: "GET",
    query: { since_version: sinceVersion },
    ...opts,
  });
}

/**
 * Terapkan Command (User_Edit_Event). 409 `VERSION_CONFLICT` bila
 * `baseVersion` basi (lihat {@link isVersionConflict}); 422 bila tidak valid.
 */
export function applyCommand(
  dashboardId: string,
  baseVersion: number,
  command: Command,
  opts: RequestOptions = {},
): Promise<PatchEvent> {
  const body: ApplyPatchRequest = { base_version: baseVersion, command };
  return request(`/dashboards/${seg(dashboardId)}/patches`, { method: "POST", body, ...opts });
}

/** 400 `NOTHING_TO_UNDO` bila tidak ada yang dapat di-undo. */
export function undoDashboard(
  dashboardId: string,
  baseVersion: number,
  opts: RequestOptions = {},
): Promise<PatchEvent> {
  const body: UndoRedoRequest = { base_version: baseVersion };
  return request(`/dashboards/${seg(dashboardId)}/undo`, { method: "POST", body, ...opts });
}

/** 400 `NOTHING_TO_REDO` bila tidak ada yang dapat di-redo. */
export function redoDashboard(
  dashboardId: string,
  baseVersion: number,
  opts: RequestOptions = {},
): Promise<PatchEvent> {
  const body: UndoRedoRequest = { base_version: baseVersion };
  return request(`/dashboards/${seg(dashboardId)}/redo`, { method: "POST", body, ...opts });
}

/** Render item dengan Global_Filter (dari content) + Cross_Filter; tanpa LLM. */
export function renderDashboard(
  dashboardId: string,
  body: RenderRequest,
  opts: RequestOptions = {},
): Promise<RenderResponse> {
  return request(`/dashboards/${seg(dashboardId)}/render`, { method: "POST", body, ...opts });
}

/**
 * Hitung ulang insight dengan filter aktif (Req 15.1). Mungkin 409
 * `VERSION_CONFLICT` atau 409 `INSIGHT_TEXT_OUTDATED`.
 */
export function refreshInsight(
  dashboardId: string,
  itemId: string,
  baseVersion: number,
  opts: RequestOptions = {},
): Promise<PatchEvent> {
  const body: RefreshInsightRequest = { base_version: baseVersion };
  return request(`/dashboards/${seg(dashboardId)}/insights/${seg(itemId)}/refresh`, {
    method: "POST",
    body,
    ...opts,
  });
}

// ---------------------------------------------------------------------------
// Query tersimpan (Req 14.7)
// ---------------------------------------------------------------------------

export function getQuery(queryId: string, opts: RequestOptions = {}): Promise<QueryDetail> {
  return request(`/queries/${seg(queryId)}`, { method: "GET", ...opts });
}

// ---------------------------------------------------------------------------
// Chat & SSE (stream dibaca oleh sse.ts)
// ---------------------------------------------------------------------------

/** URL `POST` stream chat (`text/event-stream`). */
export function chatUrl(ws: string): string {
  return apiUrl(`/workspaces/${seg(ws)}/chat`);
}

/** URL EventSource workspace; `lastEventId` untuk replay saat koneksi baru. */
export function workspaceEventsUrl(ws: string, lastEventId?: string | null): string {
  return apiUrl(`/workspaces/${seg(ws)}/events`, { last_event_id: lastEventId || undefined });
}

/** Hentikan run agent; stream chat akan mengirim `run.stopped` (202). */
export function stopChatRun(ws: string, runId: string, opts: RequestOptions = {}): Promise<void> {
  return request(`/workspaces/${seg(ws)}/chat/runs/${seg(runId)}/stop`, {
    method: "POST",
    ...opts,
  });
}

/** Riwayat pesan sesi chat dari Session_Service. */
export function getChatMessages<T = unknown>(
  ws: string,
  sessionId: string,
  opts: RequestOptions = {},
): Promise<T> {
  return request(`/workspaces/${seg(ws)}/chat/sessions/${seg(sessionId)}/messages`, {
    method: "GET",
    ...opts,
  });
}

// ---------------------------------------------------------------------------
// Semantic_Model, verifikasi item, Blueprint (Req 31, 32, 34, 37)
// ---------------------------------------------------------------------------

export function getSemanticModel(
  ws: string,
  opts: RequestOptions & { status?: string; kind?: SemanticKind } = {},
): Promise<SemanticModelResponse> {
  const { status, kind, ...rest } = opts;
  return request(`/workspaces/${seg(ws)}/semantic`, {
    method: "GET",
    query: { status, kind },
    ...rest,
  });
}

export function createSemanticEntry(
  ws: string,
  kind: SemanticKind,
  body: Record<string, unknown>,
  opts: RequestOptions = {},
): Promise<SemanticEntry> {
  return request(`/workspaces/${seg(ws)}/semantic/entries`, {
    method: "POST",
    body: { kind, body },
    ...opts,
  });
}

/** Edit entri (pengguna) → status `confirmed`, sumber `user` (Req 31.7). */
export function updateSemanticEntry(
  ws: string,
  entryId: string,
  body: Record<string, unknown>,
  opts: RequestOptions = {},
): Promise<SemanticEntry> {
  return request(`/workspaces/${seg(ws)}/semantic/entries/${seg(entryId)}`, {
    method: "PATCH",
    body: { body },
    ...opts,
  });
}

export function confirmSemanticEntry(
  ws: string,
  entryId: string,
  opts: RequestOptions = {},
): Promise<SemanticEntry> {
  return request(`/workspaces/${seg(ws)}/semantic/entries/${seg(entryId)}/confirm`, {
    method: "POST",
    ...opts,
  });
}

export function rejectSemanticEntry(
  ws: string,
  entryId: string,
  opts: RequestOptions = {},
): Promise<SemanticEntry> {
  return request(`/workspaces/${seg(ws)}/semantic/entries/${seg(entryId)}/reject`, {
    method: "POST",
    ...opts,
  });
}

export function confirmAllSemantic(
  ws: string,
  opts: RequestOptions = {},
): Promise<{ confirmed: number }> {
  return request(`/workspaces/${seg(ws)}/semantic/confirm-all`, { method: "POST", ...opts });
}

/** Ekspor Semantic_Model sebagai teks YAML. */
export async function exportSemanticYaml(ws: string, opts: RequestOptions = {}): Promise<string> {
  let res: Response;
  try {
    res = await fetch(apiUrl(`/workspaces/${seg(ws)}/semantic/export`), {
      method: "GET",
      signal: opts.signal,
    });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw networkError(err);
  }
  const text = await res.text();
  if (!res.ok) throw parseApiError(res.status, text, res.statusText);
  return text;
}

/**
 * Impor YAML; seluruhnya ditolak (422 `SEMANTIC_IMPORT_INVALID`, `details.issues`)
 * bila ada entri tidak valid (Req 31.11).
 */
export async function importSemanticYaml(
  ws: string,
  yamlText: string,
  opts: RequestOptions = {},
): Promise<SemanticModelResponse> {
  let res: Response;
  try {
    res = await fetch(apiUrl(`/workspaces/${seg(ws)}/semantic/import`), {
      method: "POST",
      headers: { "Content-Type": "text/yaml", Accept: "application/json" },
      body: yamlText,
      signal: opts.signal,
    });
  } catch (err) {
    if (isAbortError(err)) throw err;
    throw networkError(err);
  }
  const text = await res.text();
  if (!res.ok) throw parseApiError(res.status, text, res.statusText);
  return parseSuccessBody<SemanticModelResponse>(res.status, text);
}

/** Masalah impor YAML dari error 422, atau `null`. */
export function semanticImportIssues(err: unknown): SemanticImportIssue[] | null {
  if (!(err instanceof ApiError) || err.code !== "SEMANTIC_IMPORT_INVALID") return null;
  const issues = (err.details as { issues?: unknown }).issues;
  return Array.isArray(issues) ? (issues as SemanticImportIssue[]) : [];
}

/** "Tandai terverifikasi": query item menjadi Verified_Query `confirmed` (Req 34.2). */
export function verifyDashboardItem(
  dashboardId: string,
  itemId: string,
  opts: RequestOptions = {},
): Promise<SemanticEntry> {
  return request(`/dashboards/${seg(dashboardId)}/items/${seg(itemId)}/verify`, {
    method: "POST",
    ...opts,
  });
}

export function getDashboardBlueprint(
  dashboardId: string,
  opts: RequestOptions = {},
): Promise<BlueprintRecordResponse | null> {
  return request(`/dashboards/${seg(dashboardId)}/blueprint`, { method: "GET", ...opts });
}
