/** Format skenario scorecard (plan F0). Skenario adalah data; harness ada di harness.ts. */
import type { CanvasExpectation, ChartExpectation, IntentExpectation, SqlExpectation } from './scorers.ts';
import type { DatasetChoice, RelationPreset } from './workspace.ts';

export type Category = 'factual' | 'ambiguous' | 'join' | 'chart' | 'dashboard' | 'insight' | 'hybrid' | 'guardrail';

export type ScenarioTurn = {
  message: string;
  /** rest: kirim via API (event lengkap). ui: ketik di panel chat. */
  via?: 'rest' | 'ui';
  /** Setujui approval.request di giliran ini: semua slot, n slot pertama, atau tidak. */
  approve?: 'all' | number | false;
  /** Revisi visual satu slot blueprint sebelum menyetujui (D4). */
  revise?: { slotIndex: number; visual: string };
};

/** Aksi manual di canvas sebelum giliran berikutnya (kategori hybrid). */
export type ManualStep =
  | { kind: 'remove'; title: string }
  | { kind: 'change_type'; title: string; to: string }
  | { kind: 'move'; title: string; to: { x: number; y: number; w: number; h: number } }
  | { kind: 'undo'; times: number }
  | { kind: 'agent_act'; instruction: string; verifyTitle: string };

export type Scenario = {
  id: string;
  category: Category;
  title: string;
  dataset: DatasetChoice;
  relations: RelationPreset;
  /** Dashboard awal: kosong, item deterministik (seedDashboard), atau dibangun lewat giliran setup. */
  setup?: 'seed_dashboard';
  turns: ScenarioTurn[];
  /** Langkah manual disisipkan sebelum giliran ke-i (indeks). */
  manual?: Record<number, ManualStep[]>;
  expect: {
    /** Path fact (lihat facts.ts) yang angkanya harus ada di jawaban. */
    facts?: string[];
    forbidFacts?: string[];
    sql?: SqlExpectation;
    chart?: ChartExpectation;
    canvas?: CanvasExpectation;
    insightMentions?: string[];
    intent?: IntentExpectation;
    mutation: 'none' | 'after_approval' | 'allowed';
    /** Matikan cek angka karangan (mis. skenario yang wajar menyebut angka di luar query). */
    skipNumberTrace?: boolean;
    /** Judul dashboard yang diminta diubah: lulus bila berubah, atau agent jujur tidak bisa. */
    titleChange?: string;
    visual?: string[];
  };
};
