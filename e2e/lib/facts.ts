/** Loader ground truth: seed e2e + sampel eval lama dengan bentuk yang sama (plan A3). */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { EVAL_SAMPLES_DIR, SEEDS_DIR } from './paths.ts';

export type DatasetKey = 'seed' | 'eval';

export type Facts = {
  dir: string;
  files: string[];
  facts: Record<string, unknown>;
  relations: { from: string; to: string }[];
  raw: any;
};

function load(dir: string): any {
  return JSON.parse(readFileSync(join(dir, 'expected_facts.json'), 'utf8'));
}

export function loadFacts(key: DatasetKey): Facts {
  if (key === 'seed') {
    const raw = load(SEEDS_DIR);
    return {
      dir: SEEDS_DIR,
      files: Object.values(raw.tables).map((t: any) => t.file),
      facts: raw.facts,
      relations: raw.relations,
      raw,
    };
  }
  // Sampel lama: fakta ada di root (`top_region.revenue`, `finance_monthly.revenue_posted_2024`).
  const raw = load(EVAL_SAMPLES_DIR);
  return {
    dir: EVAL_SAMPLES_DIR,
    files: ['customers.csv', 'transactions.csv', 'products.xlsx', 'finance_monthly.csv'],
    facts: raw,
    relations: raw.relations,
    raw,
  };
}

/** Ambil nilai fact dengan path bertitik, mis. `revenue_by_wilayah_selesai.Jawa`. */
export function factValue(f: Facts, path: string): unknown {
  return path.split('.').reduce<any>((v, k) => (v == null ? undefined : v[k]), f.facts);
}

/** Semua angka di sebuah fact (skalar, map, atau list) untuk pencocokan longgar. */
export function factNumbers(value: unknown): number[] {
  if (typeof value === 'number') return [value];
  if (Array.isArray(value)) return value.flatMap(factNumbers);
  if (value && typeof value === 'object') return Object.values(value).flatMap(factNumbers);
  return [];
}
