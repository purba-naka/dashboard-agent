import type { Dataset } from "@/lib/types";

const label = (name: string) => name.replace(/[_-]+/g, " ").trim().toLowerCase();

/**
 * Contoh pertanyaan dari skema dataset pertama: tren waktu, peringkat, dan
 * perbandingan. Peran kolom diturunkan dari tipe (string = dimensi).
 */
export function suggestQuestions(datasets: readonly Dataset[]): string[] {
  const ds = datasets[0];
  if (!ds) return [];
  const time = ds.schema.find((c) => c.type === "date" || c.type === "datetime");
  // ponytail: heuristik nama untuk kolom kode/ID; ganti dengan metrik model semantik bila perlu.
  const measure = ds.schema.find(
    (c) => (c.type === "integer" || c.type === "float") && !/(^|_)(kode|id|code|tahun|year|bulan|month)(_|$)/i.test(c.name),
  );
  const dims = ds.schema.filter((c) => c.type === "string");
  const out: string[] = [];
  if (measure && time) out.push(`Bagaimana tren ${label(measure.name)} per ${label(time.name)}?`);
  if (measure && dims[0]) out.push(`${label(dims[0].name)} mana yang ${label(measure.name)}-nya tertinggi dan terendah?`);
  if (measure && dims[1]) out.push(`Bandingkan ${label(measure.name)} antar ${label(dims[1].name)}.`);
  out.push("Buatkan dashboard ringkasan dari data ini.");
  return out.slice(0, 4);
}
