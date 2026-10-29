/**
 * Menjalankan satu skenario scorecard dan menghitung semua dimensi (plan D2, F3).
 *
 * Giliran `rest`: api.chat (event SSE lengkap). Giliran `ui`: ketik di panel chat dan
 * sadap respons SSE `/chat` lewat browser.waitForResponse, sehingga event tetap utuh.
 */
import type { Agent, Screen } from 'e2e';
import { expect } from 'e2e';
import * as api from './api.ts';
import { factNumbers, factValue, type Facts } from './facts.ts';
import type { ManualStep, Scenario, ScenarioTurn } from './scenario.ts';
import {
  allowedValues,
  scoreCanvas,
  scoreChartType,
  scoreInsight,
  scoreIntent,
  scoreNumbers,
  scoreSafety,
  scoreSql,
} from './scorers.ts';
import { infraErrorOf, score, summarizeTurn, writeResult, type DimensionScore, type ScenarioResult } from './score.ts';
import { SseParser } from './sse.ts';
import { prepareWorkspace, seedDashboard } from './workspace.ts';

type Browser = {
  goto(url: string, o?: { waitUntil?: 'load' | 'domcontentloaded' | 'networkidle' }): Promise<unknown>;
  waitForResponse(p: string | RegExp, o?: { timeout?: number }): Promise<{ status: number; text(): Promise<string> }>;
};

export type HarnessFixtures = { screen: Screen; agent: Agent; browser: Browser };

const TURN_MS = Number(process.env.E2E_TURN_TIMEOUT_S ?? 600) * 1000;
const MAX_FOLLOWUPS = 2;
const FOLLOWUP = 'Ya, lanjutkan sesuai usulan.';

type StreamingResponse = { text(): Promise<string> };

/**
 * Baca body SSE yang berjalan lama. `text()` dari web engine dibatasi actionTimeout (30 dtk)
 * per panggilan, tetapi body-nya dibaca sekali sejak respons cocok; panggil ulang sampai
 * stream ditutup atau `deadlineMs` habis.
 */
export async function readStream(response: Promise<StreamingResponse>, deadlineMs = TURN_MS): Promise<string> {
  const res = await response;
  const deadline = Date.now() + deadlineMs;
  for (;;) {
    try {
      return await res.text();
    } catch (err) {
      if (!/did not finish/.test(String((err as Error)?.message)) || Date.now() > deadline) throw err;
    }
  }
}

/** Giliran via UI: ketik, kirim, tangkap seluruh stream SSE giliran itu. */
async function uiTurn(fx: HarnessFixtures, message: string): Promise<api.Turn> {
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  const input = fx.screen.getByPlaceholder('Tanyakan sesuatu tentang data Anda');
  await input.fill(message);
  const response = fx.browser.waitForResponse(/\/api\/workspaces\/[^/]+\/chat$/, { timeout: TURN_MS });
  await fx.screen.getByRole('button', 'Kirim').tap();
  return parseStream(message, await readStream(response), startedAt, t0);
}

function parseStream(message: string, body: string, startedAt: string, t0: number): api.Turn {
  const events = new SseParser().push(body.endsWith('\n\n') ? body : `${body}\n\n`);
  return api.summarizeEvents(message, events, startedAt, Date.now() - t0);
}

/** Klik tombol setujui pada kartu blueprint/approval terakhir lalu tangkap giliran build. */
async function uiApprove(fx: HarnessFixtures, approve: 'all' | number, revise?: ScenarioTurn['revise']): Promise<api.Turn> {
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  if (revise) {
    const selects = fx.screen.getByLabel(/^Visual slot /);
    await selects.nth(revise.slotIndex).selectOption({ value: revise.visual });
  }
  const response = fx.browser.waitForResponse(/\/api\/workspaces\/[^/]+\/chat$/, { timeout: TURN_MS });
  const blueprintAll = fx.screen.getByRole('button', 'Setujui semua');
  if (typeof approve === 'number') {
    const boxes = fx.screen.getByRole('checkbox', /^Pilih slot /);
    const total = await boxes.count();
    for (let i = approve; i < total; i++) await boxes.nth(i).uncheck();
    await fx.screen.getByRole('button', /^Setujui terpilih/).tap();
  } else if ((await blueprintAll.count()) > 0) {
    await blueprintAll.last().tap();
  } else {
    await fx.screen.getByRole('button', 'Setujui', { exact: true }).last().tap();
  }
  return parseStream('[approve via UI]', await readStream(response), startedAt, t0);
}

async function restApprove(ws: string, sid: string, turn: api.Turn, approve: 'all' | number): Promise<api.Turn> {
  const req = turn.approvals[turn.approvals.length - 1]!;
  const slots: string[] | null =
    typeof approve === 'number' && req.blueprint?.slots ? req.blueprint.slots.slice(0, approve).map((s: any) => s.slot_id) : null;
  return api.chat(ws, {
    message: slots ? `Setujui rancangan (${slots.length} slot)` : 'Setujui',
    session_id: sid,
    approval: { proposal_id: req.proposal_id, selected_slot_ids: slots },
  });
}

async function applyManual(fx: HarnessFixtures, ws: string, steps: ManualStep[]): Promise<{ removed: string[]; types: Record<string, string>; layout: Record<string, api.LayoutRect> }> {
  const out = { removed: [] as string[], types: {} as Record<string, string>, layout: {} as Record<string, api.LayoutRect> };
  for (const step of steps) {
    const dash = await api.activeDashboard(ws);
    if (!dash) throw new Error('langkah manual butuh dashboard');
    const find = (title: string) => Object.values(dash.content.items).find((i) => i.title === title || i.title?.includes(title));
    if (step.kind === 'remove') {
      const item = find(step.title);
      if (!item) throw new Error(`item "${step.title}" tidak ada`);
      await fx.screen.getByRole('button', `Aksi untuk ${item.title}`).tap();
      await fx.screen.getByRole('menuitem', 'Hapus').tap();
      await fx.screen.getByRole('alertdialog').getByRole('button', 'Hapus').tap();
      await expect(fx.screen.getByRole('button', `Pindah item ${item.title}`)).toBeHidden({ timeout: 15_000 });
      out.removed.push(item.id);
    } else if (step.kind === 'change_type') {
      const item = find(step.title)!;
      await api.applyCommand(dash.id, dash.version, { type: 'change_chart_type', id: item.id, chart_type: step.to });
      out.types[item.id] = step.to;
    } else if (step.kind === 'move') {
      const item = find(step.title)!;
      await api.applyCommand(dash.id, dash.version, { type: 'set_layout', changes: { [item.id]: step.to } });
      out.layout[item.id] = step.to;
    } else if (step.kind === 'undo') {
      let v = dash.version;
      for (let i = 0; i < step.times; i++) v = (await api.req<any>('POST', `/dashboards/${dash.id}/undo`, { base_version: v })).version;
    } else if (step.kind === 'agent_act') {
      await fx.agent.act(step.instruction);
      await expect(fx.screen.getByRole('button', new RegExp(`Pindah item .*${step.verifyTitle}`))).toBeVisible({ timeout: 15_000 });
    }
  }
  // Edit via REST tidak lewat UI: muat ulang agar canvas & state chat sinkron.
  return out;
}

function expectedNumbers(facts: Facts[], paths: string[] = []) {
  return paths.map((p) => {
    for (const f of facts) {
      const v = factValue(f, p);
      if (v !== undefined) return { label: p, value: v };
    }
    throw new Error(`fact "${p}" tidak ada di ground truth`);
  });
}

async function visualScore(fx: HarnessFixtures, ws: string, claims: string[]): Promise<DimensionScore | undefined> {
  if (!claims.length) return undefined;
  await fx.browser.goto(`/w/${ws}`, { waitUntil: 'networkidle' });
  await expect(fx.screen.getByText('Menyiapkan chart')).toBeHidden({ timeout: 60_000 }).catch(() => {});
  const evidence: string[] = [];
  let passed = 0;
  for (const claim of claims) {
    try {
      await fx.agent.assert(claim, { vision: true });
      passed++;
      evidence.push(`ok: ${claim}`);
    } catch (err: any) {
      evidence.push(`GAGAL: ${claim} (${String(err?.message ?? err).slice(0, 200)})`);
    }
  }
  return score(passed / claims.length, evidence);
}

export async function runScenario(s: Scenario, fx: HarnessFixtures): Promise<ScenarioResult> {
  const t0 = Date.now();
  const trial = Number(process.env.E2E_TRIAL ?? 0);
  const result: ScenarioResult = {
    scenarioId: s.id,
    category: s.category,
    title: s.title,
    trial,
    modelLabel: process.env.E2E_MODEL_LABEL ?? 'default',
    dimensions: {},
    turns: [],
    sqls: [],
    durationMs: 0,
    finishedAt: '',
  };
  const turns: { turn: api.Turn; approved: boolean }[] = [];
  try {
    const prep = await prepareWorkspace(`e2e-${s.id}-${trial}-${Date.now().toString(36)}`, s.dataset, s.relations);
    const ws = prep.id;
    result.workspaceId = ws;
    if (s.setup === 'seed_dashboard') await seedDashboard(ws);
    const usesUi = s.turns.some((t) => t.via === 'ui') || Object.keys(s.manual ?? {}).length > 0;
    if (usesUi) {
      await fx.browser.goto(`/w/${ws}`, { waitUntil: 'networkidle' });
      await expect(fx.screen.getByPlaceholder('Tanyakan sesuatu tentang data Anda')).toBeVisible({ timeout: 60_000 });
    }

    const before = await api.activeDashboard(ws);
    const startIso = new Date(Date.now() - 1000).toISOString();
    let sid: string | undefined;
    let manualEffects = { removed: [] as string[], types: {} as Record<string, string>, layout: {} as Record<string, api.LayoutRect> };

    for (const [i, t] of s.turns.entries()) {
      if (s.manual?.[i]) {
        const eff = await applyManual(fx, ws, s.manual[i]!);
        manualEffects = {
          removed: [...manualEffects.removed, ...eff.removed],
          types: { ...manualEffects.types, ...eff.types },
          layout: { ...manualEffects.layout, ...eff.layout },
        };
        if (usesUi) {
          await fx.browser.goto(`/w/${ws}`, { waitUntil: 'networkidle' });
          await expect(fx.screen.getByPlaceholder('Tanyakan sesuatu tentang data Anda')).toBeVisible({ timeout: 60_000 });
        }
      }
      let turn = t.via === 'ui' ? await uiTurn(fx, t.message) : await api.chat(ws, { message: t.message, session_id: sid });
      sid = turn.sessionId || sid;
      turns.push({ turn, approved: false });

      // Agent kadang bertanya/mengusulkan dulu: balas sampai MAX_FOLLOWUPS (plan R11).
      for (let f = 0; f < MAX_FOLLOWUPS; f++) {
        if (turn.approvals.length && t.approve) {
          turn = t.via === 'ui' ? await uiApprove(fx, t.approve, t.revise) : await restApprove(ws, sid!, turn, t.approve);
          turns.push({ turn, approved: true });
          continue;
        }
        const wantsMore = t.approve && !turn.patches.length && /\?\s*$|setuju|lanjut/i.test(turn.text);
        if (!wantsMore) break;
        turn = t.via === 'ui' ? await uiTurn(fx, FOLLOWUP) : await api.chat(ws, { message: FOLLOWUP, session_id: sid });
        turns.push({ turn, approved: false });
      }
    }

    const all = turns.map((x) => x.turn);
    const after = await api.activeDashboard(ws);
    const queries = (await api.listQueries(ws)).filter((q) => q.created_by === 'agent' && q.executed_at >= startIso);
    result.sqls = queries.map((q) => q.sql);
    const text = all.map((x) => x.text).join('\n');
    const e = s.expect;

    // Nilai sah untuk penelusuran angka: snapshot hasil semua query giliran ini + ground truth.
    const details = await Promise.all(queries.slice(0, 40).map((q) => api.getQuery(q.id).catch(() => null)));
    const allowed = allowedValues(
      details.filter(Boolean).map((d) => d!.rows),
      queries.map((q) => q.row_count),
      prep.facts.flatMap((f) => factNumbers(f.facts)),
    );

    const dims = result.dimensions;
    dims.intent = scoreIntent(all, e.intent ?? {});
    if (e.facts) dims.numbers = scoreNumbers(text, expectedNumbers(prep.facts, e.facts), expectedNumbers(prep.facts, e.forbidFacts));
    if (e.sql) dims.sql = scoreSql(queries, e.sql);
    if (e.chart) dims.chart_type = scoreChartType(before, after, e.chart);
    if (e.canvas)
      dims.canvas = scoreCanvas(after, {
        ...e.canvas,
        removedIds: [...(e.canvas.removedIds ?? []), ...manualEffects.removed],
        keepTypes: { ...e.canvas.keepTypes, ...manualEffects.types },
        preservedLayout: { ...e.canvas.preservedLayout, ...manualEffects.layout },
      });
    if (e.insightMentions) dims.insight = scoreInsight(text, after, e.insightMentions, before);
    if (e.titleChange) {
      const changed = after?.content.title === e.titleChange || after?.title === e.titleChange;
      const honest = /tidak (dapat|bisa)|belum (bisa|didukung)|tidak tersedia|tidak ada (fitur|tool|alat)/i.test(text);
      dims.intent = score(changed || honest ? 1 : 0, [changed ? 'judul berubah' : honest ? 'agent jujur tidak bisa mengubah judul' : 'judul tidak berubah dan agent tidak menjelaskan']);
    }
    dims.safety = scoreSafety(turns, queries, allowed, { mutation: e.mutation, checkNumbers: !e.skipNumberTrace });
    const visual = await visualScore(fx, ws, e.visual ?? []);
    if (visual) dims.visual = visual;
    result.infraError = infraErrorOf(all);
  } catch (err) {
    result.infraError = infraErrorOf(turns.map((x) => x.turn), err) ?? `harness: ${String((err as Error)?.stack ?? err).slice(0, 600)}`;
  } finally {
    result.turns = turns.map((x) => summarizeTurn(x.turn));
    result.durationMs = Date.now() - t0;
    result.finishedAt = new Date().toISOString();
    writeResult(result);
  }
  return result;
}
