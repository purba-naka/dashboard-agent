export type DateGroup = "Hari ini" | "Kemarin" | "7 hari terakhir" | "Lebih lama";
const ORDER: DateGroup[] = ["Hari ini", "Kemarin", "7 hari terakhir", "Lebih lama"];
const DAY = 86_400_000;

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

export function dateGroup(iso: string, now = new Date()): DateGroup {
  const days = Math.round((startOfDay(now) - startOfDay(new Date(iso))) / DAY);
  if (days <= 0) return "Hari ini";
  if (days === 1) return "Kemarin";
  if (days <= 7) return "7 hari terakhir";
  return "Lebih lama";
}

/** Kelompok berurutan (terbaru dulu); kelompok kosong dibuang. Urutan item dalam kelompok dipertahankan. */
export function groupByDate<T extends { created_at: string }>(items: readonly T[], now = new Date()) {
  const map = new Map<DateGroup, T[]>();
  for (const it of items) {
    const g = dateGroup(it.created_at, now);
    map.set(g, [...(map.get(g) ?? []), it]);
  }
  return ORDER.filter((g) => map.has(g)).map((g) => ({ group: g, items: map.get(g)! }));
}
