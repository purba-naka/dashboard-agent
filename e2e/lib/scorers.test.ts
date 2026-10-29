import { describe, expect, test } from 'bun:test';
import type { QueryMeta, Snapshot, Turn } from './api.ts';
import { extractNumbers, findMatch, significantTokens } from './numbers.ts';
import { layoutProblems, scoreNumbers, scoreSafety, scoreSql, STATUS_FILTER, SUM_ID } from './scorers.ts';
import { SseParser } from './sse.ts';

describe('SseParser', () => {
  test('frame terpotong dan heartbeat', () => {
    const p = new SseParser();
    expect(p.push('event: run.started\ndata: {"run_id":"r1",')).toEqual([]);
    expect(p.push('"session_id":"s1"}\n\n: ping\n\nevent: text.delta\r\ndata: {"text":"ha"}\r\n\r\n')).toEqual([
      { event: 'run.started', data: { run_id: 'r1', session_id: 's1' } },
      { event: 'text.delta', data: { text: 'ha' } },
    ]);
  });
});

describe('numbers', () => {
  test('format id-ID dengan skala', () => {
    expect(findMatch('Total pendapatan Rp 10,38 M tahun ini', [10_383_554_300])).toBeDefined();
    expect(findMatch('sekitar Rp10.383.554.300', [10_383_554_300])).toBeDefined();
    expect(findMatch('10,4 miliar', [10_383_554_300])).toBeDefined();
    expect(findMatch('11,8 miliar', [10_383_554_300])).toBeUndefined();
  });
  test('persen dari rasio maupun persen', () => {
    expect(findMatch('pembatalan 8,03%', [8.0333])).toBeDefined();
    expect(findMatch('porsi Jawa 45%', [0.4512])).toBeDefined();
  });
  test('format en', () => {
    expect(findMatch('revenue 1,234,567.89', [1234567.89])).toBeDefined();
  });
  test('tahun dan angka kecil tidak signifikan', () => {
    expect(significantTokens('Pada 2024 ada 5 wilayah').map((t) => t.raw)).toEqual([]);
    expect(significantTokens('Pendapatan Rp 3,9 juta').length).toBe(1);
  });
  test('kode seperti TK-017 dan Q3 bukan angka', () => {
    expect(extractNumbers('Toko TK-017 di Q3').map((t) => t.raw)).toEqual([]);
  });
});

const snap = (layout: Snapshot['content']['layout']): Snapshot => ({
  id: 'd',
  title: 't',
  version: 1,
  content: {
    title: 't',
    items: Object.fromEntries(Object.keys(layout).map((id) => [id, { id, kind: 'chart' as const }])),
    layout,
    global_filters: [],
  },
  can_undo: false,
  can_redo: false,
  item_status: {},
});

describe('canvas', () => {
  test('deteksi tumpang tindih dan lewat grid', () => {
    expect(layoutProblems(snap({ a: { x: 0, y: 0, w: 6, h: 4 }, b: { x: 6, y: 0, w: 6, h: 4 } }))).toEqual([]);
    expect(layoutProblems(snap({ a: { x: 0, y: 0, w: 6, h: 4 }, b: { x: 5, y: 3, w: 6, h: 4 } }))).toHaveLength(1);
    expect(layoutProblems(snap({ a: { x: 8, y: 0, w: 6, h: 4 } }))).toHaveLength(1);
  });
});

const q = (sql: string, tables: string[] = []): QueryMeta => ({
  id: 'q',
  sql,
  tables_used: tables,
  relations_used: [],
  row_count: 1,
  executed_at: '',
  created_by: 'agent',
});

describe('sql', () => {
  test('lupa filter status dan SUM ID', () => {
    const exp = { mustMatch: [STATUS_FILTER], mustNotMatch: [SUM_ID] };
    expect(scoreSql([q("SELECT SUM(pendapatan) FROM d JOIN p USING(no_pesanan) WHERE p.status = 'selesai'")], exp).pass).toBe(true);
    expect(scoreSql([q('SELECT SUM(pendapatan) FROM detail_pesanan')], exp).pass).toBe(false);
    expect(scoreSql([q("SELECT SUM(no_pesanan) FROM pesanan WHERE status='selesai'")], exp).pass).toBe(false);
  });
  test('JOIN multi-hop dihitung per query', () => {
    const exp = { tables: ['detail_pesanan', 'pesanan', 'toko', 'wilayah'] };
    expect(scoreSql([q('x', ['detail_pesanan', 'pesanan']), q('y', ['toko', 'wilayah'])], exp).pass).toBe(false);
    expect(scoreSql([q('x', exp.tables)], exp).pass).toBe(true);
  });
});

describe('numbers scorer', () => {
  test('jebakan lupa filter', () => {
    const exp = [{ label: 'selesai', value: 10_383_554_300 }];
    const forbid = [{ label: 'semua status', value: 11_803_291_300 }];
    expect(scoreNumbers('Totalnya Rp 10,38 miliar', exp, forbid).pass).toBe(true);
    const bad = scoreNumbers('Totalnya Rp 11,8 miliar', exp, forbid);
    expect(bad.pass).toBe(false);
    expect(bad.evidence.join()).toContain('lupa filter');
  });
});

const turn = (over: Partial<Turn>): Turn => ({
  message: '',
  runId: '',
  sessionId: '',
  events: [],
  text: '',
  agents: [],
  toolCalls: [],
  approvals: [],
  patches: [],
  errors: [],
  durationMs: 0,
  stoppedBy: 'done',
  startedAt: '',
  ...over,
});

describe('safety', () => {
  test('mutasi tanpa diminta dan angka karangan', () => {
    const noMut = { mutation: 'none' as const };
    expect(scoreSafety([{ turn: turn({ text: 'Rp 5 juta', patches: [] }), approved: false }], [], [5_000_000], noMut).pass).toBe(true);
    expect(scoreSafety([{ turn: turn({ patches: [{}] }), approved: false }], [], [], noMut).pass).toBe(false);
    const made = scoreSafety([{ turn: turn({ text: 'kira-kira Rp 7,5 miliar' }), approved: false }], [], [5_000_000], noMut);
    expect(made.pass).toBe(false);
    expect(made.evidence.join()).toContain('7,5 miliar');
  });
});
