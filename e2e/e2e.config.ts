
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { web } from '@e2e-dev/web';
import type { E2EConfig } from 'e2e';
import { APP_URL, BACKEND_ENV, loadEnv } from './lib/paths.ts';

// e2e tidak membaca .env sendiri; kunci & endpoint diambil dari backend/.env (plan C2).
loadEnv(BACKEND_ENV);

const cosmos = createOpenAICompatible({
  name: 'cosmos',
  baseURL: process.env.OPENAI_API_BASE ?? 'https://api.cosmoshub.tech/v1',
  apiKey: process.env.OPENAI_API_KEY,
  // Judge e2e memakai response_format json_schema (closed grammar).
  supportsStructuredOutputs: true,
});

// Config harus deterministik antar proses (CONFIG_NOT_DETERMINISTIC): hanya teruskan
// kunci tetap. Launcher membaca sisanya (OPENAI_*, MODEL_*) dari file env run.ts.
const LAUNCH_KEYS = ['E2E_RUN_DIR', 'E2E_ENV_FILE', 'SYSTEMROOT', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PATHEXT', 'WINDIR', 'PROGRAMDATA'];
const launchEnv: Record<string, string> = Object.fromEntries(
  LAUNCH_KEYS.flatMap((k) => (process.env[k] ? [[k, process.env[k]!]] : [])),
);

export default {
  tests: ['tests/**/*.e2e.ts'],
  timeout: 15 * 60_000,
  actionTimeout: 30_000,
  assertionTimeout: 15_000,
  retries: 0,
  workers: Number(process.env.E2E_WORKERS ?? 1),
  trace: 'retain-on-failure',
  cache: 'off',
  agents: {
    default: {
      model: cosmos.chatModel(process.env.E2E_MODEL ?? 'gemini-3.8-flash'),
      judge: cosmos.chatModel(process.env.E2E_JUDGE ?? 'claude-sonnet-5'),
      judgmentTimeout: 90_000,
      context:
        'Aplikasi Dashboard Studio berbahasa Indonesia. Kanvas dashboard berisi KPI, chart ECharts, dan insight card. Panel chat di kanan.',
    },
  },
  targets: [
    {
      name: 'chromium',
      engine: web({ viewport: { width: 1600, height: 1000 } }),
      app: {
        url: APP_URL,
        command: {
          executable: 'bun',
          args: ['scripts/launch.ts'],
          env: launchEnv,
          startupTimeout: 360_000,
          shutdownTimeout: 15_000,
          log: '.run/launch.log',
        },
      },
    },
  ],
  secrets: { openaiKey: () => process.env.OPENAI_API_KEY ?? '' },
} satisfies E2EConfig;
