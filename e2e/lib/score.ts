/** Model skor scorecard + recorder hasil (plan D4). */
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { Turn } from './api.ts';

export const DIMENSIONS = ['intent', 'numbers', 'sql', 'chart_type', 'canvas', 'insight', 'safety', 'visual'] as const;
export type Dimension = (typeof DIMENSIONS)[number];

export type DimensionScore = { score: number; pass: boolean; evidence: string[] };

export const score = (value: number, evidence: string[] = [], threshold = 1): DimensionScore => ({
  score: Math.round(value * 100) / 100,
  pass: value >= threshold,
  evidence,
});

export type TurnSummary = {
  message: string;
  answer: string;
  agents: string[];
  tools: string[];
  approvals: number;
  patches: number;
  errors: string[];
  durationMs: number;
  stoppedBy: string;
};

export type ScenarioResult = {
  scenarioId: string;
  category: string;
  title: string;
  trial: number;
  modelLabel: string;
  workspaceId?: string;
  dimensions: Partial<Record<Dimension, DimensionScore>>;
  turns: TurnSummary[];
  sqls: string[];
  infraError?: string;
  durationMs: number;
  finishedAt: string;
};

export const summarizeTurn = (t: Turn): TurnSummary => ({
  message: t.message,
  answer: t.text,
  agents: [...new Set(t.agents)],
  tools: t.toolCalls.map((c) => `${c.tool}${c.ok === false ? '(gagal)' : ''}`),
  approvals: t.approvals.length,
  patches: t.patches.length,
  errors: t.errors.map((e) => `${e.code}: ${e.message}`.slice(0, 200)),
  durationMs: t.durationMs,
  stoppedBy: t.stoppedBy,
});

/** Kegagalan infrastruktur (bukan kesalahan agent): dipisah di laporan agar proxy down tidak terbaca sebagai model bodoh. */
const INFRA_PATTERNS = [
  /MODEL_PROVIDER_FAILED/,
  /ECONNREFUSED|fetch failed|ECONNRESET|socket hang up/i,
  /\b(429|502|503|504)\b/,
  /rate.?limit|overloaded|timeout.*provider|APIConnectionError|ServiceUnavailable|InternalServerError/i,
];

export function infraErrorOf(turns: Turn[], err?: unknown): string | undefined {
  const msgs = [
    ...turns.flatMap((t) => t.errors.map((e) => `${e.code}: ${e.message}`)),
    ...(err ? [String((err as Error)?.message ?? err)] : []),
  ];
  return msgs.find((m) => INFRA_PATTERNS.some((p) => p.test(m)));
}

export function writeResult(result: ScenarioResult): void {
  const dir = process.env.E2E_RESULTS_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${result.scenarioId}.t${result.trial}.json`), JSON.stringify(result, null, 2));
}
