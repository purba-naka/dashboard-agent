/**
 * Launcher tunggal untuk `app.command` (plan E2): backend :8001 + frontend :3001
 * dengan DATA_DIR sementara. Dihentikan runner e2e; anak dimatikan per pohon proses
 * (Windows: taskkill /T /F) agar next dev dan worker query tidak tertinggal memegang port.
 */
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { createWriteStream, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join } from 'node:path';
import { existsSync } from 'node:fs';
import { API_URL, APP_URL, BACKEND, BACKEND_ENV, BACKEND_PORT, E2E_ROOT, FRONTEND, FRONTEND_PORT, loadEnv, PYTHON } from '../lib/paths.ts';

// Override model per run (matriks) dari run.ts menang atas backend/.env (main.py: override=False).
loadEnv(process.env.E2E_ENV_FILE);
loadEnv(BACKEND_ENV);

const RUN_DIR = process.env.E2E_RUN_DIR ?? join(E2E_ROOT, '.run', 'adhoc');
const DATA_DIR = join(RUN_DIR, 'data');
const LOGS = join(RUN_DIR, 'logs');
mkdirSync(DATA_DIR, { recursive: true });
mkdirSync(LOGS, { recursive: true });

const children: ChildProcess[] = [];
// Di Windows runner mematikan launcher tanpa sinyal yang bisa ditangkap; PID anak dicatat
// agar start berikutnya membersihkan sisa, dan watchdog memantau proses induk.
const PID_FILE = join(E2E_ROOT, '.run', 'launch.pids');

function killPid(pid: number): void {
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore' });
  else {
    try {
      process.kill(-pid, 'SIGKILL');
    } catch {}
  }
}

function cleanupStale(): void {
  if (!existsSync(PID_FILE)) return;
  for (const pid of readFileSync(PID_FILE, 'utf8').split(/\s+/).filter(Boolean).map(Number)) killPid(pid);
  rmSync(PID_FILE, { force: true });
  killPortOwners(); // sisa run kita sebelumnya (PID_FILE ada = launcher lama mati paksa)
}

function recordPids(): void {
  writeFileSync(PID_FILE, children.map((c) => c.pid).filter(Boolean).join('\n'));
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function killTree(child: ChildProcess): void {
  if (child.pid != null && child.exitCode == null) killPid(child.pid);
}

/** Pembersih terakhir: matikan pohon proses apa pun yang masih LISTEN di port suite. */
function killPortOwners(): void {
  const ports = [BACKEND_PORT, FRONTEND_PORT];
  if (process.platform === 'win32') {
    const out = spawnSync('netstat', ['-ano', '-p', 'tcp'], { encoding: 'utf8' }).stdout ?? '';
    const pids = new Set<number>();
    for (const line of out.split(/\r?\n/)) {
      const m = line.match(/:(\d+)\s+\S+\s+LISTENING\s+(\d+)/);
      if (m && ports.includes(Number(m[1])) && Number(m[2]) > 0) pids.add(Number(m[2]));
    }
    pids.forEach(killPid);
  } else {
    for (const port of ports) spawnSync('sh', ['-c', `lsof -ti tcp:${port} -sTCP:LISTEN | xargs -r kill -9`]);
  }
}

function shutdown(code = 0): never {
  for (const c of children.reverse()) killTree(c);
  if (children.length) killPortOwners();
  rmSync(PID_FILE, { force: true });
  process.exit(code);
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGBREAK'] as const) process.on(sig, () => shutdown(0));
process.on('exit', () => children.forEach(killTree));

async function portFree(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const srv = createServer()
      .once('error', () => resolve(false))
      .once('listening', () => srv.close(() => resolve(true)))
      .listen(port, '127.0.0.1');
  });
}

async function waitHttp(url: string, timeoutMs: number, child: ChildProcess, name: string): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode != null) throw new Error(`${name} keluar dengan kode ${child.exitCode}; lihat ${LOGS}`);
    try {
      const res = await fetch(url);
      if (res.status < 500) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${name} tidak siap di ${url} dalam ${timeoutMs / 1000}s`);
}

function start(name: string, cmd: string, args: string[], cwd: string, env: Record<string, string | undefined>): ChildProcess {
  const log = createWriteStream(join(LOGS, `${name}.log`), { flags: 'a' });
  const child = spawn(cmd, args, { cwd, env: env as NodeJS.ProcessEnv, detached: process.platform !== 'win32', windowsHide: true });
  child.stdout?.pipe(log);
  child.stderr?.pipe(log);
  children.push(child);
  return child;
}

/** Env backend: hanya yang perlu; DATABASE_URL tidak diteruskan agar selalu SQLite di DATA_DIR sementara. */
function backendEnv(): Record<string, string | undefined> {
  const pass = Object.fromEntries(
    Object.entries(process.env).filter(([k]) =>
      /^(PATH|PATHEXT|SYSTEMROOT|SystemRoot|COMSPEC|TEMP|TMP|HOME|USERPROFILE|APPDATA|LOCALAPPDATA|WINDIR|PROGRAMDATA|OPENAI_|GEMINI_|ANTHROPIC_|MODEL_|LITELLM_)/i.test(k),
    ),
  );
  return {
    ...pass,
    HOST: '127.0.0.1',
    PORT: String(BACKEND_PORT),
    DATA_DIR,
    DATABASE_URL: '',
    CORS_ORIGINS: APP_URL,
    MODEL_SKIP_CAPABILITY_CHECK: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  };
}

async function main(): Promise<void> {
  cleanupStale();
  await new Promise((r) => setTimeout(r, 500));
  for (const port of [BACKEND_PORT, FRONTEND_PORT])
    if (!(await portFree(port))) throw new Error(`port ${port} sudah dipakai; matikan server lama dulu`);

  const parent = process.ppid;
  setInterval(() => {
    if (!alive(parent)) shutdown(0);
  }, 2000).unref();

  const backend = start('backend', PYTHON, ['main.py'], BACKEND, backendEnv());
  recordPids();
  await waitHttp(`${API_URL}/api/health`, 120_000, backend, 'backend');

  // `next` langsung via node (tanpa `bun run`): perantara yang keluar lebih dulu memutus
  // pohon proses sehingga taskkill /T tidak menjangkau server next.
  const nextBin = join(FRONTEND, 'node_modules', 'next', 'dist', 'bin', 'next');
  const frontend = start('frontend', 'node', [nextBin, 'dev', '-p', String(FRONTEND_PORT)], FRONTEND, {
    ...process.env,
    NEXT_PUBLIC_API_URL: API_URL,
    NEXT_TELEMETRY_DISABLED: '1',
    PORT: String(FRONTEND_PORT),
  });
  recordPids();
  await waitHttp(APP_URL, 240_000, frontend, 'frontend');
  await fetch(`${APP_URL}/`).catch(() => {}); // warm-up kompilasi rute utama

  for (const c of children) c.on('exit', (code) => shutdown(code ?? 1));
  await new Promise(() => {}); // hidup sampai dihentikan runner
}

main().catch((err) => {
  console.error(String(err?.message ?? err));
  shutdown(1);
});
