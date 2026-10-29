/** Persiapan workspace lewat REST (plan D3): upload dataset, relasi sesuai preset, semantic. */
import { join } from 'node:path';
import * as api from './api.ts';
import { loadFacts, type DatasetKey, type Facts } from './facts.ts';

export type RelationPreset = 'confirmed' | 'candidate' | 'none';
export type DatasetChoice = DatasetKey | 'seed+eval';

export type PreparedWorkspace = { id: string; facts: Facts[]; relations: api.Relation[] };

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function relKey(r: { from_table: string; from_column: string; to_table: string; to_column: string }): string {
  return [`${r.from_table}.${r.from_column}`, `${r.to_table}.${r.to_column}`].sort().join('|');
}

/** Profiling relasi berjalan di latar belakang setelah upload; tunggu sampai semua kandidat yang diharapkan muncul. */
async function waitRelations(ws: string, expected: Set<string>): Promise<api.Relation[]> {
  const deadline = Date.now() + 120_000;
  let rels: api.Relation[] = [];
  while (Date.now() < deadline) {
    rels = await api.detectRelations(ws).catch(() => api.listRelations(ws));
    if ([...expected].every((k) => rels.some((r) => relKey(r) === k))) return rels;
    await sleep(1000);
  }
  return rels;
}

export async function prepareWorkspace(
  name: string,
  dataset: DatasetChoice,
  relations: RelationPreset,
  { semantic = true }: { semantic?: boolean } = {},
): Promise<PreparedWorkspace> {
  const keys: DatasetKey[] = dataset === 'seed+eval' ? ['seed', 'eval'] : [dataset];
  const facts = keys.map(loadFacts);
  const { id } = await api.createWorkspace(name.slice(0, 80));
  for (const f of facts) for (const file of f.files) await api.upload(id, join(f.dir, file));

  const expected = new Set(
    facts.flatMap((f) => f.relations.map((r) => [r.from, r.to].sort().join('|'))),
  );
  let rels = await waitRelations(id, expected);
  for (const r of rels) {
    if (r.status !== 'candidate') continue;
    if (relations === 'confirmed' && expected.has(relKey(r))) await api.confirmRelation(id, r.id);
    if (relations === 'none') await api.rejectRelation(id, r.id);
  }
  if (semantic) await api.confirmAllSemantic(id).catch(() => {}); // draft bisa belum siap; tidak fatal
  rels = await api.listRelations(id);
  return { id, facts, relations: rels };
}

/** Dashboard kosong + item deterministik tanpa LLM (gate G4-G6, hybrid H*). */
export async function seedDashboard(ws: string): Promise<{ dashboardId: string; items: Record<string, string> }> {
  const dash = await api.createDashboard(ws, 'Overview');
  const byWilayah = await api.executeQuery(
    ws,
    "SELECT w.nama_wilayah, SUM(d.pendapatan) AS pendapatan FROM detail_pesanan d JOIN pesanan p ON d.no_pesanan = p.no_pesanan JOIN toko t ON p.kode_toko = t.kode_toko JOIN wilayah w ON t.kode_wilayah = w.kode_wilayah WHERE p.status = 'selesai' GROUP BY w.nama_wilayah ORDER BY pendapatan DESC",
  );
  const byStatus = await api.executeQuery(ws, 'SELECT status, COUNT(*) AS jumlah FROM pesanan GROUP BY status ORDER BY jumlah DESC');
  const items: Record<string, string> = {};
  let version = dash.version;
  const add = async (title: string, spec: unknown, layout: api.LayoutRect) => {
    const ev = await api.applyCommand(dash.id, version, { type: 'add_chart', title, spec, layout });
    version = ev.version;
    items[title] = ev.ops[0].item.id;
  };
  const bar = (qid: string, x: string, y: string, cross?: string) => ({
    query_id: qid,
    chart_type: 'bar',
    option: { xAxis: { type: 'category' }, yAxis: { type: 'value' }, series: [{ type: 'bar', encode: { x, y } }] },
    ...(cross ? { cross_filter_column: cross } : {}),
  });
  await add('Pendapatan per Wilayah', bar(byWilayah.query_id, 'nama_wilayah', 'pendapatan', 'nama_wilayah'), { x: 0, y: 0, w: 6, h: 8 });
  await add('Pesanan per Status', bar(byStatus.query_id, 'status', 'jumlah'), { x: 6, y: 0, w: 6, h: 8 });
  return { dashboardId: dash.id, items };
}
