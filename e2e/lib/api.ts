/** Klien REST + chat SSE untuk Backend_API (plan D1). Semua endpoint di bawah `/api`. */
import { readFileSync } from 'node:fs';
import { basename } from 'node:path';
import { API_URL } from './paths.ts';
import { SseParser, type SseEvent } from './sse.ts';

const TURN_TIMEOUT_MS = Number(process.env.E2E_TURN_TIMEOUT_S ?? 600) * 1000;

export class ApiError extends Error {
  constructor(public status: number, public body: string, path: string) {
    super(`${status} ${path}: ${body.slice(0, 300)}`);
  }
}

export async function req<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { 'content-type': 'application/json' };
  }
  const res = await fetch(`${API_URL}/api${path}`, init);
  const text = await res.text();
  if (!res.ok) throw new ApiError(res.status, text, path);
  return (text ? JSON.parse(text) : undefined) as T;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// Workspace, upload, relasi
// ---------------------------------------------------------------------------

export const createWorkspace = (name: string) => req<{ id: string }>('POST', '/workspaces', { name });

async function waitJob(jobId: string): Promise<void> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const job = await req<{ status: string; error?: unknown }>('GET', `/jobs/${jobId}`);
    if (job.status === 'done') return;
    if (job.status === 'failed') throw new Error(`job ${jobId} gagal: ${JSON.stringify(job.error)}`);
    await sleep(250);
  }
  throw new Error(`job ${jobId} timeout`);
}

/** Upload CSV/XLSX lewat REST; XLSX memilih semua sheet. Menunggu konversi selesai. */
export async function upload(ws: string, path: string): Promise<void> {
  const form = new FormData();
  form.append('file', new Blob([readFileSync(path)]), basename(path));
  const res = await req<any>('POST', `/workspaces/${ws}/uploads`, form);
  const jobs: string[] = res.job_id
    ? [res.job_id]
    : (
        await req<{ jobs: { job_id: string }[] }>('POST', `/workspaces/${ws}/uploads/${res.upload_id}/sheets`, {
          sheets: res.sheets.map((s: { name: string }) => s.name),
        })
      ).jobs.map((j) => j.job_id);
  for (const j of jobs) await waitJob(j);
}

export type Relation = {
  id: string;
  from_table: string;
  from_column: string;
  to_table: string;
  to_column: string;
  status: string;
};

export const listRelations = (ws: string) => req<Relation[]>('GET', `/workspaces/${ws}/relations`);
export const detectRelations = (ws: string) => req<Relation[]>('POST', `/workspaces/${ws}/relations/detect`);
export const confirmRelation = (ws: string, id: string) =>
  req('POST', `/workspaces/${ws}/relations/${id}/confirm`);
export const rejectRelation = (ws: string, id: string) => req('POST', `/workspaces/${ws}/relations/${id}/reject`);
export const confirmAllSemantic = (ws: string) => req('POST', `/workspaces/${ws}/semantic/confirm-all`);
export const listDatasets = (ws: string) => req<{ id: string; table_name: string }[]>('GET', `/workspaces/${ws}/datasets`);

// ---------------------------------------------------------------------------
// Dashboard & query
// ---------------------------------------------------------------------------

export type LayoutRect = { x: number; y: number; w: number; h: number };
export type DashboardItem = {
  id: string;
  kind: 'chart' | 'kpi' | 'insight';
  title?: string;
  spec?: any;
  query_id?: string;
  sql?: string;
  text?: string;
  matched_numbers?: unknown[];
};
export type Snapshot = {
  id: string;
  title: string;
  version: number;
  content: {
    title: string;
    items: Record<string, DashboardItem>;
    layout: Record<string, LayoutRect>;
    global_filters: unknown[];
    brief?: any;
  };
  can_undo: boolean;
  can_redo: boolean;
  item_status: Record<string, { invalid: boolean; stale: boolean }>;
};

export const listDashboards = (ws: string) =>
  req<{ id: string; title: string; version: number; updated_at?: string }[]>('GET', `/workspaces/${ws}/dashboards`);
export const getDashboard = (id: string) => req<Snapshot>('GET', `/dashboards/${id}`);
export const createDashboard = (ws: string, title = 'Overview') =>
  req<Snapshot>('POST', `/workspaces/${ws}/dashboards`, { title });
export const applyCommand = (id: string, baseVersion: number, command: unknown) =>
  req<any>('POST', `/dashboards/${id}/patches`, { base_version: baseVersion, command });
export const getQuery = (id: string) =>
  req<{ sql: string; columns: { name: string }[]; rows: unknown[][]; row_count: number }>('GET', `/queries/${id}`);
export const executeQuery = (ws: string, sql: string) =>
  req<{ query_id: string; row_count: number }>('POST', `/workspaces/${ws}/queries`, { sql });

export type QueryMeta = {
  id: string;
  sql: string;
  tables_used: string[];
  relations_used: string[];
  row_count: number;
  executed_at: string;
  created_by: string;
};
export const listQueries = (ws: string) => req<QueryMeta[]>('GET', `/workspaces/${ws}/queries?limit=500`);

/** Dashboard yang terakhir berubah (sama dengan pilihan backend saat chat tanpa dashboard_id). */
export async function activeDashboard(ws: string): Promise<Snapshot | null> {
  const list = await listDashboards(ws);
  if (!list.length) return null;
  const snaps = await Promise.all(list.map((d) => getDashboard(d.id)));
  return snaps.sort((a, b) => b.version - a.version)[0] ?? null;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export type ApprovalRequest = { proposal_id: string; kind?: string; summary?: string; blueprint?: any };
export type Turn = {
  message: string;
  runId: string;
  sessionId: string;
  events: SseEvent[];
  text: string;
  agents: string[];
  toolCalls: { agent: string; tool: string; ok?: boolean; summary?: string }[];
  approvals: ApprovalRequest[];
  patches: any[];
  errors: { code: string; message: string; agent?: string }[];
  durationMs: number;
  stoppedBy: 'done' | 'stopped' | 'timeout' | 'error';
  startedAt: string;
};

export type ChatBody = {
  message: string;
  session_id?: string;
  dashboard_id?: string;
  approval?: { proposal_id: string; selected_slot_ids?: string[] | null };
};

export function summarizeEvents(message: string, events: SseEvent[], startedAt: string, durationMs: number): Turn {
  const t: Turn = {
    message,
    runId: '',
    sessionId: '',
    events,
    text: '',
    agents: [],
    toolCalls: [],
    approvals: [],
    patches: [],
    errors: [],
    durationMs,
    stoppedBy: 'error',
    startedAt,
  };
  for (const { event, data } of events) {
    if (event === 'run.started') {
      t.runId = data.run_id;
      t.sessionId = data.session_id;
    } else if (event === 'text.delta') t.text += data.text ?? '';
    else if (event === 'agent.active') t.agents.push(data.agent);
    else if (event === 'tool.call') t.toolCalls.push({ agent: data.agent, tool: data.tool });
    else if (event === 'tool.result') {
      const call = [...t.toolCalls].reverse().find((c) => c.tool === data.tool && c.ok === undefined);
      if (call) Object.assign(call, { ok: data.ok, summary: data.summary });
      else t.toolCalls.push({ agent: data.agent, tool: data.tool, ok: data.ok, summary: data.summary });
    } else if (event === 'approval.request') t.approvals.push(data);
    else if (event === 'patch.applied') t.patches.push(data);
    else if (event === 'error') t.errors.push(data);
    else if (event === 'run.done') t.stoppedBy = 'done';
    else if (event === 'run.stopped') t.stoppedBy = 'stopped';
  }
  return t;
}

/** Kirim satu giliran dan baca stream sampai run.done/run.stopped/timeout (timeout → stop run). */
export async function chat(ws: string, body: ChatBody, timeoutMs = TURN_TIMEOUT_MS): Promise<Turn> {
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  const ctrl = new AbortController();
  const events: SseEvent[] = [];
  let runId = '';
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);
  try {
    const res = await fetch(`${API_URL}/api/workspaces/${ws}/chat`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok || !res.body) throw new ApiError(res.status, await res.text(), '/chat');
    const parser = new SseParser();
    const decoder = new TextDecoder();
    for await (const chunk of res.body as unknown as AsyncIterable<Uint8Array>) {
      for (const ev of parser.push(decoder.decode(chunk, { stream: true }))) {
        events.push(ev);
        if (ev.event === 'run.started') runId = ev.data.run_id;
      }
      if (events.some((e) => e.event === 'run.done' || e.event === 'run.stopped')) break;
    }
  } catch (err) {
    if (!timedOut) throw err;
  } finally {
    clearTimeout(timer);
  }
  if (timedOut && runId) await req('POST', `/workspaces/${ws}/chat/runs/${runId}/stop`).catch(() => {});
  const turn = summarizeEvents(body.message, events, startedAt, Date.now() - t0);
  if (timedOut) turn.stoppedBy = 'timeout';
  return turn;
}

export const sessionMessages = (ws: string, sid: string) =>
  req<{ messages: { author: string; text: string }[] }>('GET', `/workspaces/${ws}/chat/sessions/${sid}/messages`);
export const workspaceDetail = (ws: string) =>
  req<{ chat_sessions: { id: string; updated_at?: string; created_at?: string }[] }>('GET', `/workspaces/${ws}`);
