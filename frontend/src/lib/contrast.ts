type RGB = [number, number, number];

function parseHex(c: string): RGB | null {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(c.trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
}

/** Warna ECharts (hex atau rgb()/rgba()) → RGB; null bila tak dikenali. */
export function parseColor(c: unknown): RGB | null {
  if (typeof c !== "string") return null;
  const hex = parseHex(c);
  if (hex) return hex;
  const m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)/i.exec(c.trim());
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** Luminance relatif WCAG. */
function luminance([r, g, b]: RGB): number {
  const lin = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** Warna pada posisi t∈[0,1] di gradasi `colors` (interpolasi linear RGB); null bila bukan hex. */
export function colorAt(colors: readonly string[], t: number): RGB | null {
  const rgb = colors.map(parseHex);
  if (rgb.length === 0 || rgb.some((c) => c === null)) return null;
  const stops = rgb as RGB[];
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(Math.floor(x), stops.length - 2);
  if (i < 0) return stops[0];
  const f = x - i;
  return stops[i].map((v, k) => v + (stops[i + 1][k] - v) * f) as RGB;
}

export const TEXT_DARK = "#111827";
export const TEXT_LIGHT = "#ffffff";

/** Teks gelap atau terang, mana yang kontrasnya lebih tinggi terhadap latar `bg`. */
export function readableOn(bg: RGB | null): string {
  if (!bg) return TEXT_DARK;
  const l = luminance(bg);
  const vsLight = 1.05 / (l + 0.05);
  const vsDark = (l + 0.05) / (luminance(parseHex(TEXT_DARK)!) + 0.05);
  return vsLight > vsDark ? TEXT_LIGHT : TEXT_DARK;
}
