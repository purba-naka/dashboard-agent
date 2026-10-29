/**
 * results/<model>/<ts>/*.json → report.md (plan E4). Tanpa skor total berbobot (D10).
 *   bun scripts/report.ts <resultsDir>        tulis ulang laporan satu run
 *   bun scripts/report.ts --matrix            bandingkan run terbaru tiap model
 */
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { E2E_ROOT } from '../lib/paths.ts';
import { DIMENSIONS, type Dimension, type ScenarioResult } from '../lib/score.ts';

type Cell = { pass: number; total: number };
const pct = (c?: Cell) => (c && c.total ? `${c.pass}/${c.total}` : '–');
const rate = (c?: Cell) => (c && c.total ? c.pass / c.total : undefined);

export function loadResults(dir: string): ScenarioResult[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(readFileSync(join(dir, f), 'utf8')) as ScenarioResult);
}

function aggregate(results: ScenarioResult[], key: (r: ScenarioResult) => string) {
  const out = new Map<string, Partial<Record<Dimension, Cell>>>();
  for (const r of results) {
    if (r.infraError) continue;
    const row = out.get(key(r)) ?? {};
    for (const d of DIMENSIONS) {
      const s = r.dimensions[d];
      if (!s) continue;
      const c = (row[d] ??= { pass: 0, total: 0 });
      c.total++;
      if (s.pass) c.pass++;
    }
    out.set(key(r), row);
  }
  return out;
}

function table(rows: Map<string, Partial<Record<Dimension, Cell>>>, head: string, delta?: Map<string, Partial<Record<Dimension, Cell>>>): string {
  const lines = [`| ${head} | ${DIMENSIONS.join(' | ')} |`, `|---|${DIMENSIONS.map(() => '---').join('|')}|`];
  for (const [k, row] of rows) {
    const cells = DIMENSIONS.map((d) => {
      const now = rate(row[d]);
      const prev = rate(delta?.get(k)?.[d]);
      const arrow = now !== undefined && prev !== undefined && Math.abs(now - prev) >= 0.1 ? (now > prev ? ' ▲' : ' ▼') : '';
      return pct(row[d]) + arrow;
    });
    lines.push(`| ${k} | ${cells.join(' | ')} |`);
  }
  return lines.join('\n');
}

/** Run sebelumnya untuk model yang sama (folder timestamp sebelum ini). */
function previousRun(dir: string): ScenarioResult[] {
  const parent = dirname(dir);
  const prev = readdirSync(parent)
    .filter((d) => d < basename(dir) && statSync(join(parent, d)).isDirectory())
    .sort()
    .pop();
  return prev ? loadResults(join(parent, prev)) : [];
}

export function writeReport(dir: string, env: Record<string, string> = {}): { total: number; infra: number; safetyFailures: number } {
  const results = loadResults(dir).sort((a, b) => a.scenarioId.localeCompare(b.scenarioId, 'en', { numeric: true }) || a.trial - b.trial);
  const infra = results.filter((r) => r.infraError);
  const safety = results.filter((r) => r.dimensions.safety && !r.dimensions.safety.pass);
  const prev = previousRun(dir);
  const md: string[] = [];
  md.push(`# Scorecard agent: ${results[0]?.modelLabel ?? basename(dirname(dir))}`, '');
  md.push(`- Waktu: ${basename(dir)}`);
  md.push(`- Model aplikasi (override): ${Object.entries(env).filter(([, v]) => v).map(([k, v]) => `\`${k}=${v}\``).join(', ') || 'backend/.env apa adanya'}`);
  md.push(`- Driver/judge e2e: \`${process.env.E2E_MODEL ?? 'gemini-3.8-flash'}\` / \`${process.env.E2E_JUDGE ?? 'claude-sonnet-5'}\``);
  md.push(`- Skenario dijalankan: ${results.length} (infra error: ${infra.length})`);
  md.push(`- Durasi total: ${Math.round(results.reduce((s, r) => s + r.durationMs, 0) / 60000)} menit`, '');

  if (safety.length) {
    md.push('## Pelanggaran safety', '');
    for (const r of safety) md.push(`- **${r.scenarioId}** ${r.title} (t${r.trial}): ${r.dimensions.safety!.evidence.join('; ')}`);
    md.push('');
  }

  md.push('## Pass rate per kategori', '', 'Angka = lulus/dinilai. ▲▼ = selisih ≥ 10 poin dari run sebelumnya model yang sama.', '');
  md.push(table(aggregate(results, (r) => r.category), 'kategori', prev.length ? aggregate(prev, (r) => r.category) : undefined), '');
  md.push('## Per skenario', '');
  md.push(table(aggregate(results, (r) => `${r.scenarioId} ${r.title}`), 'skenario'), '');

  if (infra.length) {
    md.push('## Kegagalan infrastruktur (tidak dihitung sebagai skor)', '');
    for (const r of infra) md.push(`- ${r.scenarioId} t${r.trial}: \`${r.infraError!.split('\n')[0]!.slice(0, 240)}\``);
    md.push('');
  }

  md.push('## Detail skenario yang gagal', '');
  for (const r of results) {
    const failed = DIMENSIONS.filter((d) => r.dimensions[d] && !r.dimensions[d]!.pass);
    if (!failed.length || r.infraError) continue;
    md.push(`### ${r.scenarioId} ${r.title} (t${r.trial}, ${Math.round(r.durationMs / 1000)} dtk)`, '');
    if (r.workspaceId) md.push(`Workspace: \`${r.workspaceId}\``, '');
    for (const t of r.turns) {
      md.push(`- **Pengguna:** ${t.message}`);
      md.push(`  - Agent (${t.agents.join(' → ') || '-'}; ${t.durationMs} ms; ${t.stoppedBy}): ${t.answer.replace(/\s+/g, ' ').slice(0, 300) || '(kosong)'}`);
      if (t.tools.length) md.push(`  - Tools: ${t.tools.join(', ')}`);
      if (t.errors.length) md.push(`  - Error: ${t.errors.join('; ')}`);
    }
    for (const d of failed) md.push(`- **${d}** ${r.dimensions[d]!.score}: ${r.dimensions[d]!.evidence.join('; ')}`);
    if (r.sqls.length) md.push('', '```sql', ...r.sqls.slice(0, 5).map((s) => s.trim() + ';'), '```');
    md.push('');
  }
  writeFileSync(join(dir, 'report.md'), md.join('\n'));
  return { total: results.length, infra: infra.length, safetyFailures: safety.length };
}

/** Tabel matriks: run terbaru tiap model berdampingan. */
export function writeMatrix(): string {
  const root = join(E2E_ROOT, 'results');
  const rows = new Map<string, Partial<Record<Dimension, Cell>>>();
  for (const model of readdirSync(root).filter((d) => statSync(join(root, d)).isDirectory())) {
    const latest = readdirSync(join(root, model)).sort().pop();
    if (!latest) continue;
    const agg = aggregate(loadResults(join(root, model, latest)), () => model);
    rows.set(`${model} (${latest})`, agg.get(model) ?? {});
  }
  const out = join(root, 'matrix.md');
  writeFileSync(out, `# Matriks model\n\n${table(rows, 'model')}\n`);
  return out;
}

if (import.meta.main) {
  const arg = process.argv[2];
  if (arg === '--matrix') console.log(writeMatrix());
  else if (arg) console.log(writeReport(arg));
  else console.error('pakai: bun scripts/report.ts <resultsDir> | --matrix');
}
