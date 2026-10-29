/**
 * Ekstraksi & pencocokan angka di teks jawaban agent (plan D5).
 *
 * ponytail: port ringkas `backend/studio/core/numbers.py` (format id/en, sufiks skala,
 * persen, pembulatan pada jumlah desimal yang ditulis). Tidak mem-port aturan tepi
 * seperti "ke-2"/"COVID-19" secara penuh. Bila hasil berbeda dari backend, panggil
 * helper Python lewat endpoint kecil.
 */

const SCALES: Record<string, number> = {
  rb: 1e3,
  ribu: 1e3,
  k: 1e3,
  jt: 1e6,
  juta: 1e6,
  m: 1e9, // hanya bila ditulis "M" (lihat bawah)
  miliar: 1e9,
  milyar: 1e9,
  t: 1e12,
  triliun: 1e12,
};

const NUMBER_RE =
  /(?:(?<=Rp\.?\s?)|(?<![\p{L}\d_.,]))(?<![\p{L}]-)(?<sign>[-\u2212])?(?<body>\d+(?:[.,]\d+)*)(?:(?<pct>\s?(?:%|persen\b))|\s?(?<scale>ribu|rb|juta|jt|miliar|milyar|triliun|M\b|T\b|K\b))?/giu;

export type NumberToken = {
  raw: string;
  /** Kandidat nilai (format ambigu `1.234` → 1234 dan 1.234). */
  values: { value: number; decimals: number }[];
  percent: boolean;
  scale: number;
};

/** Semua interpretasi body angka: id-ID (`.` ribuan, `,` desimal) dan en (kebalikan). */
function interpret(body: string): { value: number; decimals: number }[] {
  const out: { value: number; decimals: number }[] = [];
  const tryFmt = (thousands: string, decimal: string) => {
    const parts = body.split(decimal);
    if (parts.length > 2) return;
    const [intPart, frac = ''] = parts as [string, string?];
    const groups = intPart.split(thousands);
    if (groups.length > 1 && (groups[0]!.length > 3 || groups.slice(1).some((g) => g.length !== 3))) return;
    const n = Number(groups.join('') + (frac ? `.${frac}` : ''));
    if (Number.isFinite(n)) out.push({ value: n, decimals: frac.length });
  };
  tryFmt('.', ',');
  tryFmt(',', '.');
  const seen = new Set<string>();
  return out.filter((c) => {
    const k = `${c.value}|${c.decimals}`;
    return seen.has(k) ? false : (seen.add(k), true);
  });
}

export function extractNumbers(text: string): NumberToken[] {
  const tokens: NumberToken[] = [];
  for (const m of text.matchAll(NUMBER_RE)) {
    const g = m.groups!;
    const values = interpret(g.body!);
    if (!values.length) continue;
    // "M"/"T"/"K" satu huruf hanya dikenali sebagai skala bila huruf besar (regex memakai flag i).
    const lowerSingle = g.scale?.length === 1 && g.scale !== g.scale.toUpperCase();
    const scale = g.scale && !lowerSingle ? SCALES[g.scale.toLowerCase()] ?? 1 : 1;
    tokens.push({ raw: m[0].trim(), values, percent: Boolean(g.pct), scale });
  }
  return tokens;
}

const roundTo = (v: number, d: number) => {
  const f = 10 ** d;
  return Math.round(v * f) / f;
};

/** Apakah token merupakan tampilan (dibulatkan) dari `target`. */
export function tokenMatches(tok: NumberToken, target: number): boolean {
  const mag = Math.abs(target);
  return tok.values.some(({ value, decimals }) => {
    const shown = Math.abs(value);
    const candidates = tok.percent ? [mag * 100, mag] : [mag / tok.scale];
    // Pembulatan pada jumlah desimal yang ditulis; toleransi 1 ulp terakhir untuk floor/ceil.
    const step = 10 ** -decimals;
    return candidates.some((c) => roundTo(c, decimals) === shown || Math.abs(c - shown) < step);
  });
}

/** Token di teks yang cocok dengan salah satu target. */
export function findMatch(text: string, targets: number[]): NumberToken | undefined {
  const toks = extractNumbers(text);
  return toks.find((t) => targets.some((v) => tokenMatches(t, v)));
}

/**
 * Angka "signifikan" yang perlu ditelusuri (scorer safety): magnitudo ≥ 1000 setelah skala,
 * atau persen. Tahun 1900-2100 tanpa skala diabaikan.
 */
export function significantTokens(text: string): NumberToken[] {
  return extractNumbers(text).filter((t) => {
    if (t.percent) return true;
    const v = Math.max(...t.values.map((c) => Math.abs(c.value))) * t.scale;
    if (t.scale === 1 && t.values.some((c) => c.decimals === 0 && c.value >= 1900 && c.value <= 2100)) return false;
    return v >= 1000;
  });
}
