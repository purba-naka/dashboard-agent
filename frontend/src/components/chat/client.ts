/**
 * Port data & state murni Chat_Panel (Req 6.4, 7.3, 8.5, 11.4, 16.1, 16.4,
 * 16.6, 21.1, 21.2).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai `streamChat` dan REST.
 */

import {
  confirmAllSemantic,
  confirmRelation,
  confirmSemanticEntry,
  getChatMessages,
  isApiError,
  rejectRelation,
  rejectSemanticEntry,
  stopChatRun,
} from "@/lib/api";
import { streamChat, type ChatStreamHandlers, type ChatStreamResult } from "@/lib/sse";
import type {
  AgentName,
  BlueprintSlotStatus,
  ChatRequest,
  ChatSseEvent,
  DashboardBlueprint,
  Relation,
  ReviewFinding,
  SemanticDraftCardData,
} from "@/lib/types";

export interface ChatClient {
  /** Kirim pesan; resolve saat stream berakhir. */
  send(
    workspaceId: string,
    request: ChatRequest,
    handlers: ChatStreamHandlers,
    options?: { signal?: AbortSignal },
  ): Promise<ChatStreamResult>;
  /** Hentikan run (202); stream lalu mengirim `run.stopped` (Req 16.6). */
  stop(workspaceId: string, runId: string): Promise<void>;
  /** Riwayat sesi, sudah dipetakan ke entri timeline. */
  history(workspaceId: string, sessionId: string): Promise<HistoryEntry[]>;
  confirmRelation(workspaceId: string, relationId: string): Promise<Relation>;
  rejectRelation(workspaceId: string, relationId: string): Promise<Relation>;
  /** Kartu "Pemahaman data" (Req 32.10); opsional agar test lama tetap valid. */
  confirmSemantic?(workspaceId: string, entryId: string): Promise<unknown>;
  rejectSemantic?(workspaceId: string, entryId: string): Promise<unknown>;
  confirmAllSemantic?(workspaceId: string): Promise<unknown>;
}

export const defaultChatClient: ChatClient = {
  send: (ws, req, handlers, options) => streamChat(ws, req, handlers, options),
  stop: (ws, runId) => stopChatRun(ws, runId),
  history: async (ws, sid) => mapHistory(await getChatMessages(ws, sid)),
  confirmRelation: (ws, id) => confirmRelation(ws, id),
  rejectRelation: (ws, id) => rejectRelation(ws, id),
  confirmSemantic: (ws, id) => confirmSemanticEntry(ws, id),
  rejectSemantic: (ws, id) => rejectSemanticEntry(ws, id),
  confirmAllSemantic: (ws) => confirmAllSemantic(ws),
};

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

export type ChatEntry =
  | { kind: "user"; id: number; text: string }
  | { kind: "agent"; id: number; agent: AgentName; text: string; thought?: string }
  | { kind: "relations"; id: number; relations: Relation[] }
  | {
      kind: "approval";
      id: number;
      proposalId: string;
      summary: string;
      themes: string[];
      approved: boolean;
      /** Ada bila usulan berupa Dashboard_Blueprint (Req 37.5). */
      blueprint?: DashboardBlueprint;
      /** Slot yang disetujui (kosong = belum disetujui). */
      selectedSlotIds?: string[];
      /** Progres pembangunan per slot (`blueprint.progress`, Req 37.8). */
      progress: Record<string, BlueprintSlotStatus>;
    }
  | { kind: "profile"; id: number; datasetId: string; summary: string }
  | {
      kind: "semantic";
      id: number;
      data: SemanticDraftCardData;
      /** Keputusan pengguna per entri (id entri semantik). */
      decided: Record<string, "confirmed" | "rejected">;
    }
  | { kind: "review"; id: number; findings: ReviewFinding[] }
  | { kind: "error"; id: number; code: string; message: string; agent?: AgentName }
  | { kind: "notice"; id: number; text: string };

export type HistoryEntry =
  | { kind: "user"; text: string }
  | { kind: "agent"; agent: AgentName; text: string };

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
type NewEntry = DistributiveOmit<ChatEntry, "id">;

export interface RunningTool {
  agent: AgentName;
  tool: string;
  argsSummary: string;
}

export interface ChatState {
  entries: ChatEntry[];
  nextId: number;
  sessionId: string | null;
  runId: string | null;
  running: boolean;
  activeAgent: AgentName | null;
  runningTool: RunningTool | null;
}

export const initialChatState = (sessionId: string | null = null): ChatState => ({
  entries: [],
  nextId: 1,
  sessionId,
  runId: null,
  running: false,
  activeAgent: null,
  runningTool: null,
});

export type ChatAction =
  | { type: "send"; text: string }
  | { type: "event"; event: ChatSseEvent }
  | { type: "ended" }
  | { type: "failed"; message: string }
  | { type: "history"; sessionId: string; entries: HistoryEntry[] }
  | { type: "relation"; relation: Relation }
  | { type: "approved"; proposalId: string; selectedSlotIds?: string[] }
  | { type: "semanticDecided"; entryId: number; decisions: Record<string, "confirmed" | "rejected"> };

function push(state: ChatState, entry: NewEntry): ChatState {
  return {
    ...state,
    entries: [...state.entries, { ...entry, id: state.nextId } as ChatEntry],
    nextId: state.nextId + 1,
  };
}

const idle = { running: false, runId: null, activeAgent: null, runningTool: null } as const;

function applyEvent(state: ChatState, ev: ChatSseEvent): ChatState {
  switch (ev.event) {
    case "run.started":
      return { ...state, runId: ev.data.run_id, sessionId: ev.data.session_id, running: true };
    case "agent.active":
      return { ...state, activeAgent: ev.data.agent };
    case "text.delta":
    case "thought.delta": {
      const field = ev.event === "text.delta" ? "text" : "thought";
      const last = state.entries.at(-1);
      // Thought baru setelah jawaban dimulai = langkah berikutnya → entri baru.
      const continues =
        last?.kind === "agent" &&
        last.agent === ev.data.agent &&
        (field === "text" || !last.text);
      if (continues) {
        const entries = state.entries.slice(0, -1);
        entries.push({ ...last, [field]: (last[field] ?? "") + ev.data.text });
        return { ...state, entries, activeAgent: ev.data.agent };
      }
      return push({ ...state, activeAgent: ev.data.agent }, {
        kind: "agent",
        agent: ev.data.agent,
        text: "",
        [field]: ev.data.text,
      });
    }
    case "tool.call":
      return {
        ...state,
        activeAgent: ev.data.agent,
        runningTool: { agent: ev.data.agent, tool: ev.data.tool, argsSummary: ev.data.args_summary },
      };
    case "tool.result":
      return state.runningTool?.tool === ev.data.tool ? { ...state, runningTool: null } : state;
    case "patch.applied":
      return state;
    case "approval.request":
      return push(state, {
        kind: "approval",
        proposalId: ev.data.proposal_id,
        summary: ev.data.summary,
        themes: ev.data.themes ?? [],
        approved: false,
        ...(ev.data.kind === "blueprint" && ev.data.blueprint ? { blueprint: ev.data.blueprint } : {}),
        progress: {},
      });
    case "semantic.draft":
      return push(state, { kind: "semantic", data: ev.data, decided: {} });
    case "review.findings":
      return push(state, { kind: "review", findings: ev.data.findings ?? [] });
    case "blueprint.progress": {
      const { slot_id, status } = ev.data;
      // Kartu Blueprint terbaru yang memuat slot ini.
      const index = state.entries.findLastIndex(
        (e) => e.kind === "approval" && !!e.blueprint?.slots.some((s) => s.slot_id === slot_id),
      );
      if (index < 0) return state;
      const entries = state.entries.slice();
      const entry = entries[index] as Extract<ChatEntry, { kind: "approval" }>;
      entries[index] = { ...entry, progress: { ...entry.progress, [slot_id]: status } };
      return { ...state, entries };
    }
    case "relation.candidates":
      return ev.data.length ? push(state, { kind: "relations", relations: ev.data }) : state;
    case "profile.summary":
      return push(state, { kind: "profile", datasetId: ev.data.dataset_id, summary: ev.data.summary });
    case "error":
      return push(state, { kind: "error", ...ev.data });
    case "run.stopped":
      return push({ ...state, ...idle }, { kind: "notice", text: "Proses agent dihentikan." });
    case "run.done":
      return { ...state, ...idle };
  }
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send":
      return push({ ...state, ...idle, running: true }, { kind: "user", text: action.text });
    case "event":
      return applyEvent(state, action.event);
    case "ended":
      return { ...state, ...idle };
    case "failed":
      return push({ ...state, ...idle }, { kind: "error", code: "CLIENT", message: action.message });
    case "history": {
      let next: ChatState = { ...initialChatState(action.sessionId), nextId: state.nextId };
      for (const e of action.entries) next = push(next, e);
      return next;
    }
    case "relation":
      return {
        ...state,
        entries: state.entries.map((e) =>
          e.kind === "relations" && e.relations.some((r) => r.id === action.relation.id)
            ? { ...e, relations: e.relations.map((r) => (r.id === action.relation.id ? action.relation : r)) }
            : e,
        ),
      };
    case "approved":
      return {
        ...state,
        entries: state.entries.map((e) =>
          e.kind === "approval" && e.proposalId === action.proposalId
            ? {
                ...e,
                approved: true,
                ...(action.selectedSlotIds ? { selectedSlotIds: action.selectedSlotIds } : {}),
              }
            : e,
        ),
      };
    case "semanticDecided":
      return {
        ...state,
        entries: state.entries.map((e) =>
          e.kind === "semantic" && e.id === action.entryId
            ? { ...e, decided: { ...e.decided, ...action.decisions } }
            : e,
        ),
      };
  }
}

// ---------------------------------------------------------------------------
// Riwayat (bentuk respons belum final → mapper toleran)
// ---------------------------------------------------------------------------

function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(textOf).join("");
  if (value && typeof value === "object") {
    const o = value as Record<string, unknown>;
    return textOf(o.text ?? o.parts ?? o.content ?? "");
  }
  return "";
}

/**
 * Terima `[...]` atau `{messages|events|items: [...]}`; tiap item boleh
 * `{role|author, text|content|parts, agent?}`. Item tanpa teks dibuang.
 */
export function mapHistory(raw: unknown): HistoryEntry[] {
  const o = raw as Record<string, unknown> | null;
  const list = Array.isArray(raw) ? raw : (o?.messages ?? o?.events ?? o?.items);
  if (!Array.isArray(list)) return [];
  const out: HistoryEntry[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const m = item as Record<string, unknown>;
    const text = textOf(m.text ?? m.content ?? m.parts);
    if (!text) continue;
    const role = String(m.role ?? m.author ?? "");
    if (role === "user") out.push({ kind: "user", text });
    else out.push({ kind: "agent", agent: String(m.agent ?? (role && role !== "model" && role !== "assistant" ? role : "root")), text });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Tampilan
// ---------------------------------------------------------------------------

const AGENT_LABELS: Record<string, string> = {
  root: "Root",
  data_profiler: "Data Profiler",
  query: "Query",
  chart_designer: "Chart Designer",
  insight: "Insight",
  Root_Agent: "Root",
  Data_Profiler_Agent: "Data Profiler",
  Dashboard_Architect_Agent: "Architect BI",
  Query_Agent: "Query",
  Chart_Designer_Agent: "Chart Designer",
  Insight_Agent: "Insight",
};

export function agentLabel(agent: AgentName): string {
  return AGENT_LABELS[agent] ?? agent;
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
