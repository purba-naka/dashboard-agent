/**
 * Scorecard: satu test per skenario (plan F2). Skor ditulis ke E2E_RESULTS_DIR.
 * Test hanya gagal bila dimensi safety gagal (D10); skor rendah lain bukan kegagalan.
 */
import { describe, test } from '@e2e-dev/web';

import { runScenario } from '../../lib/harness.ts';
import { SCENARIOS } from '../../scenarios/index.ts';

describe('scorecard', { timeout: 20 * 60_000, retries: 0 }, () => {
  for (const s of SCENARIOS) {
    test(`${s.id} ${s.title}`, { tags: ['scorecard', s.category, s.id] }, async ({ screen, agent, browser }) => {
      const r = await runScenario(s, { screen, agent, browser: browser as any });
      if (r.infraError) test.skip(true, `infra: ${r.infraError.slice(0, 200)}`);

      if (r.dimensions.safety?.pass === false) throw new Error(`safety gagal: ${r.dimensions.safety.evidence.join('; ')}`);
    });
  }
});
