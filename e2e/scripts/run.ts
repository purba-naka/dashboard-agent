/**
 * CLI suite (plan E3):
 *   bun scripts/run.ts [--layer gate|scorecard|smoke|all] [--category a,b] [--scenario F1,J2]
 *                      [--trials N] [--matrix glm-5.3,deepseek-v4-pro] [--agent-model ROOT=x,QUERY=y]
 *                      [--no-reasoning] [--keep-data]
 *
 * Exit: 1 bila gate gagal atau safety gagal; 2 bila semua skenario gagal karena infrastruktur.
 */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { parseArgs } from 'node:util';
import { SCENARIOS } from '../scenarios/index.ts';
import { E2E_ROOT, PYTHON, SEEDS_DIR } from '../lib/paths.ts';
import { writeMatrix, writeReport } from './report.ts';

const { values: args } = parseArgs({
  options: {
    layer: { type: 'string', default: 'scorecard' },
    category: { type: 'string' },
    scenario: { type: 'string' },
    trials: { type: 'string', default: '1' },
    matrix: { type: 'string' },
    'agent-model': { type: 'string' },
    'no-reasoning': { type: 'boolean', default: false },
    'keep-data': { type: 'boolean', default: false },
    headed: { type: 'boolean', default: false },
  },
});

const AGENT_VARS = ['ROOT', 'PROFILER', 'QUERY', 'CHART', 'INSIGHT', 'ARCHITECT', 'SEMANTIC'];
const NODE24 = join(E2E_ROOT, '.run', 'node');
const stamp = () => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

/** e2e butuh Node >= 22.22.3/24.8; pakai node portable di .run/node bila node sistem terlalu lama. */
function nodeBin(): string {
  const ok = (v: string) => {
    const [maj, min, pat] = v.replace(/^v/, '').split('.').map(Number) as [number, number, number];
    return maj > 24 || (maj === 24 && min >= 8) || (maj === 22 && (min > 22 || (min === 22 && pat >= 3)));
  };
  const sys = spawnSync('node', ['--version'], { encoding: 'utf8' }).stdout?.trim() ?? '';
  if (sys && ok(sys)) return 'node';
  const local = existsSync(NODE24) ? readdirSync(NODE24).find((d) => d.startsWith('node-v')) : undefined;
  if (local) return join(NODE24, local, process.platform === 'win32' ? 'node.exe' : 'bin/node');
  throw new Error(`Node ${sys} terlalu lama untuk e2e. Pasang Node 24 atau ekstrak ke ${NODE24} (lihat README).`);
}

function ensureSeeds(): void {
  const gen = join(SEEDS_DIR, 'generate.py');
  const hash = createHash('sha256').update(readFileSync(gen)).digest('hex');
  const marker = join(SEEDS_DIR, '.generator.sha256');
  if (existsSync(join(SEEDS_DIR, 'expected_facts.json')) && existsSync(marker) && readFileSync(marker, 'utf8') === hash) return;
  const r = spawnSync(PYTHON, [gen], { stdio: 'inherit' });
  if (r.status !== 0) throw new Error('generator seed gagal');
  writeFileSync(marker, hash);
}

type MatrixEntry = { label: string; env: Record<string, string> };

function matrixEntries(): MatrixEntry[] {
  const perAgent: Record<string, string> = {};
  for (const pair of args['agent-model']?.split(',').filter(Boolean) ?? []) {
    const [agent, model] = pair.split('=');
    perAgent[`MODEL_${agent!.trim().toUpperCase()}`] = model!.includes('/') ? model! : `openai/${model}`;
  }
  const reasoning = args['no-reasoning'] ? Object.fromEntries(AGENT_VARS.map((a) => [`MODEL_${a}_REASONING`, ''])) : {};
  const models = args.matrix?.split(',').filter(Boolean) ?? [];
  if (!models.length) {
    const label = Object.keys(perAgent).length ? Object.entries(perAgent).map(([k, v]) => `${k.slice(6)}=${v.split('/').pop()}`).join('+') : 'env-default';
    return [{ label, env: { ...reasoning, ...perAgent } }];
  }
  return models.map((m) => {
    const full = m.includes('/') ? m : `openai/${m}`;
    const all = Object.fromEntries(['DEFAULT', ...AGENT_VARS].map((a) => [`MODEL_${a}`, full]));
    return { label: m.replace(/[\\/:]/g, '_'), env: { ...all, ...reasoning, ...perAgent } };
  });
}

function selection(): { files: string[]; tags: string[] } {
  const layer = args.layer!;
  const files = layer === 'all' ? ['tests/gate', 'tests/scorecard'] : [`tests/${layer}`];
  const tags = [...(args.category?.split(',') ?? []), ...(args.scenario?.split(',') ?? [])].filter(Boolean);
  return { files, tags };
}

function estimate(entries: MatrixEntry[], trials: number): void {
  if (args.layer === 'gate' || args.layer === 'smoke') return;
  const { tags } = selection();
  const picked = tags.length ? SCENARIOS.filter((s) => tags.includes(s.category) || tags.includes(s.id)) : SCENARIOS;
  const minutes = picked.reduce((m, s) => m + (['dashboard', 'hybrid'].includes(s.category) ? 6 : s.category === 'chart' || s.category === 'insight' ? 3 : 1.5), 0);
  const total = picked.length * trials * entries.length;
  console.log(`Estimasi: ${picked.length} skenario × ${trials} percobaan × ${entries.length} model = ${total} run, ±${Math.round(minutes * trials * entries.length)} menit. Setiap run memanggil LLM berbayar.`);
}

function runOnce(entry: MatrixEntry, runDir: string, resultsDir: string, trials: number): number {
  mkdirSync(resultsDir, { recursive: true });
  const envFile = join(runDir, 'model.env');
  writeFileSync(envFile, Object.entries(entry.env).map(([k, v]) => `${k}=${v}`).join('\n'));
  const { files, tags } = selection();
  const cli = join(E2E_ROOT, 'node_modules', 'e2e', 'dist', 'cli', 'bin.js');
  const output = join('.run', runDir.slice(join(E2E_ROOT, '.run').length + 1), 'e2e').replace(/\\/g, '/');
  const cliArgs = [cli, 'run', ...files, '--no-cache', '--output', output];
  if (tags.length) cliArgs.push('--tag', tags.join(','));
  if (trials > 1) cliArgs.push('--repeat-each', String(trials));
  if (args.headed) cliArgs.push('--headed');
  console.log(`\n▶ ${entry.label}: ${files.join(' ')}${tags.length ? ` --tag ${tags.join(',')}` : ''}`);
  const r = spawnSync(nodeBin(), cliArgs, {
    cwd: E2E_ROOT,
    stdio: 'inherit',
    env: {
      ...process.env,
      ...entry.env,
      E2E_TELEMETRY_DISABLED: '1',
      E2E_RUN_DIR: runDir,
      E2E_ENV_FILE: envFile,
      E2E_RESULTS_DIR: resultsDir,
      E2E_MODEL_LABEL: entry.label,
      AI_SDK_LOG_WARNINGS: 'false',
    },
  });
  return r.status ?? 1;
}

function main(): number {
  ensureSeeds();
  const trials = Math.max(1, Number(args.trials));
  const entries = matrixEntries();
  estimate(entries, trials);
  let exit = 0;
  const reports: string[] = [];
  for (const entry of entries) {
    const ts = stamp();
    const runDir = join(E2E_ROOT, '.run', entry.label, ts);
    const resultsDir = join(E2E_ROOT, 'results', entry.label, ts);
    mkdirSync(runDir, { recursive: true });
    const code = runOnce(entry, runDir, resultsDir, trials);
    // e2e exit 1 = ada test gagal (gate, atau safety di scorecard); 2+ = konfigurasi/infra.
    if (code !== 0) exit = Math.max(exit, code === 1 ? 1 : 2);
    if (args.layer !== 'gate' && args.layer !== 'smoke') {
      const summary = writeReport(resultsDir, entry.env);
      reports.push(join(resultsDir, 'report.md'));
      if (summary.total > 0 && summary.infra === summary.total) exit = Math.max(exit, 2);
      if (summary.safetyFailures > 0) exit = Math.max(exit, 1);
    }
    if (!args['keep-data']) rmSync(join(runDir, 'data'), { recursive: true, force: true });
  }
  if (reports.length > 1) reports.push(writeMatrix());
  for (const r of reports) console.log(`Laporan: ${r}`);
  return exit;
}

process.exit(main());
