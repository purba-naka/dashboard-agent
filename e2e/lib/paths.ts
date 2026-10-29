import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const E2E_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const REPO_ROOT = resolve(E2E_ROOT, '..');
export const BACKEND = join(REPO_ROOT, 'backend');
export const FRONTEND = join(REPO_ROOT, 'frontend');
export const SEEDS_DIR = join(BACKEND, 'tests', 'e2e', 'seeds');
export const EVAL_SAMPLES_DIR = join(BACKEND, 'tests', 'eval', 'samples');
export const BACKEND_ENV = join(BACKEND, '.env');

export const BACKEND_PORT = Number(process.env.E2E_BACKEND_PORT ?? 8001);
export const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 3001);
export const API_URL = process.env.E2E_API_URL ?? `http://127.0.0.1:${BACKEND_PORT}`;
// localhost (bukan 127.0.0.1): next dev menolak hidrasi dari origin lain (plan E1).
export const APP_URL = process.env.E2E_APP_URL ?? `http://localhost:${FRONTEND_PORT}`;

/** Muat file .env tanpa menimpa variabel yang sudah ada (Bun tidak punya process.loadEnvFile). */
export function loadEnv(path: string | undefined): void {
  if (!path || !existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*(?:export\s+)?([A-Za-z_][\w.]*)\s*=\s*(.*?)\s*$/);
    if (!m || process.env[m[1]!] !== undefined) continue;
    process.env[m[1]!] = m[2]!.replace(/^(['"])(.*)\1$/, '$2').replace(/\s+#.*$/, '');
  }
}

export const PYTHON = [
  join(BACKEND, '.venv', 'Scripts', 'python.exe'),
  join(BACKEND, '.venv', 'bin', 'python'),
].find(existsSync) ?? 'python';
