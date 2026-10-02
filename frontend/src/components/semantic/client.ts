/**
 * Port data & helper panel Model Semantik (Req 31, 32.10).
 */

import {
  confirmAllSemantic,
  confirmSemanticEntry,
  exportSemanticYaml,
  getSemanticModel,
  importSemanticYaml,
  isApiError,
  redraftSemantic,
  rejectSemanticEntry,
  semanticImportIssues,
  updateSemanticEntry,
} from "@/lib/api";
import type {
  SemanticEntry,
  SemanticImportIssue,
  SemanticKind,
  SemanticModelResponse,
  SemanticStatus,
} from "@/lib/types";

export interface SemanticClient {
  load(ws: string): Promise<SemanticModelResponse>;
  update(ws: string, entryId: string, body: Record<string, unknown>): Promise<SemanticEntry>;
  confirm(ws: string, entryId: string): Promise<SemanticEntry>;
  reject(ws: string, entryId: string): Promise<SemanticEntry>;
  confirmAll(ws: string): Promise<{ confirmed: number }>;
  redraft(ws: string): Promise<{ scheduled: boolean }>;
  exportYaml(ws: string): Promise<string>;
  importYaml(ws: string, text: string): Promise<SemanticModelResponse>;
}

export const defaultSemanticClient: SemanticClient = {
  load: (ws) => getSemanticModel(ws),
  update: (ws, id, body) => updateSemanticEntry(ws, id, body),
  confirm: (ws, id) => confirmSemanticEntry(ws, id),
  reject: (ws, id) => rejectSemanticEntry(ws, id),
  confirmAll: (ws) => confirmAllSemantic(ws),
  redraft: (ws) => redraftSemantic(ws),
  exportYaml: (ws) => exportSemanticYaml(ws),
  importYaml: (ws, text) => importSemanticYaml(ws, text),
};

export const KIND_LABELS: Record<SemanticKind, string> = {
  metric: "Metrik",
  column: "Kolom",
  term: "Istilah",
  instruction: "Instruksi",
  verified_query: "Query terverifikasi",
};

export const STATUS_LABELS: Record<SemanticStatus, string> = {
  candidate: "Usulan",
  confirmed: "Dikonfirmasi",
  rejected: "Ditolak",
};

export const KIND_ORDER: SemanticKind[] = ["metric", "column", "term", "instruction", "verified_query"];

/** Judul satu entri untuk tampilan. */
export function entryTitle(entry: SemanticEntry): string {
  const b = entry.body;
  switch (entry.kind) {
    case "metric":
      return String(b.label || b.name || entry.entry_key);
    case "column":
      return `${String(b.label || b.column)} (${String(b.table)}.${String(b.column)})`;
    case "term":
      return String(b.term ?? entry.entry_key);
    case "instruction":
      return String(b.text ?? "");
    case "verified_query":
      return String(b.question ?? entry.entry_key);
  }
}

/** Field yang dapat diedit inline per jenis entri (bukan field kunci). */
export const EDITABLE_FIELDS: Record<SemanticKind, string[]> = {
  metric: ["label", "description", "expr"],
  column: ["label", "description"],
  term: ["description"],
  instruction: [],
  verified_query: ["question"],
};

export const FIELD_LABELS: Record<string, string> = {
  label: "Label",
  description: "Deskripsi",
  expr: "Rumus SQL",
  question: "Pertanyaan",
};

export function groupEntries(
  entries: readonly SemanticEntry[],
  status: SemanticStatus | "all",
): Record<SemanticKind, SemanticEntry[]> {
  const out = Object.fromEntries(KIND_ORDER.map((k) => [k, [] as SemanticEntry[]])) as Record<
    SemanticKind,
    SemanticEntry[]
  >;
  for (const e of entries) if (status === "all" || e.status === status) out[e.kind].push(e);
  return out;
}

export function importIssues(err: unknown): SemanticImportIssue[] | null {
  return semanticImportIssues(err);
}

export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
