/**
 * Logika filter Frontend: normalisasi FilterSet, perbandingan snapshot filter
 * insight vs Global_Filter (Req 15.3), toggle Cross_Filter (Req 24.1, 24.4),
 * serta helper pembuat & label predikat untuk UI.
 *
 * Semantik mengikuti `backend/studio/core/filters.py`:
 * - FilterSet adalah himpunan predikat (konjungsi): urutan & duplikat tidak
 *   bermakna.
 * - `in.values` adalah himpunan (tanpa urutan, tanpa duplikat); `values`
 *   kosong tetap bermakna (tidak mencocokkan baris apa pun).
 * - `date_range` tanpa kedua ujung adalah no-op dan dibuang; ujung dibaca
 *   sebagai tanggal ISO (`YYYY-MM-DD`, 10 karakter pertama).
 * - Cross_Filter = `in {v}` pada `(table, column)` hasil lineage kolom
 *   dimensi chart sumber; state tampilan, tidak disimpan sebagai Patch_Event.
 */

import type {
  DateRangePredicate,
  FilterSet,
  InPredicate,
  IsoDate,
  Predicate,
  Scalar,
} from "./types";

// ---------------------------------------------------------------------------
// Kunci kanonik
// ---------------------------------------------------------------------------

/** Kunci kanonik skalar; membedakan tipe (`1` ≠ `"1"`, `null` ≠ `"null"`). */
function valueKey(v: Scalar): string {
  if (v === null) return "null:";
  return `${typeof v}:${JSON.stringify(v)}`;
}

function compareStrings(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function toIsoDate(value: IsoDate | null | undefined): IsoDate | null {
  if (value === null || value === undefined || value === "") return null;
  return String(value).slice(0, 10);
}

/** Nilai `in` unik dalam urutan kanonik. */
function canonicalValues(values: readonly Scalar[]): Scalar[] {
  const byKey = new Map<string, Scalar>();
  for (const v of values) byKey.set(valueKey(v), v);
  return [...byKey.entries()].sort(([a], [b]) => compareStrings(a, b)).map(([, v]) => v);
}

/** Salinan kanonik satu predikat (tanggal dipotong, values unik & terurut). */
function canonicalPredicate(p: Predicate): Predicate {
  if (p.kind === "date_range") {
    return {
      kind: "date_range",
      table: p.table,
      column: p.column,
      start: toIsoDate(p.start),
      end: toIsoDate(p.end),
    };
  }
  return { kind: "in", table: p.table, column: p.column, values: canonicalValues(p.values) };
}

function sortTuple(p: Predicate): string[] {
  const c = canonicalPredicate(p);
  if (c.kind === "date_range") {
    return [c.table, c.column, c.kind, c.start ?? "", c.end ?? ""];
  }
  return [c.table, c.column, c.kind, "", "", ...c.values.map(valueKey)];
}

function compareTuples(a: string[], b: string[]): number {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const c = compareStrings(a[i], b[i]);
    if (c !== 0) return c;
  }
  return a.length - b.length;
}

/**
 * Kunci identitas predikat: dua predikat ekuivalen secara himpunan ⇔ kuncinya
 * sama (urutan `values` dan format waktu setelah tanggal diabaikan).
 */
export function predicateKey(p: Predicate): string {
  return JSON.stringify(sortTuple(p));
}

export function predicatesEqual(a: Predicate, b: Predicate): boolean {
  return predicateKey(a) === predicateKey(b);
}

/** `date_range` tanpa kedua ujung tidak membatasi apa pun. */
export function isNoopPredicate(p: Predicate): boolean {
  return p.kind === "date_range" && toIsoDate(p.start) === null && toIsoDate(p.end) === null;
}

// ---------------------------------------------------------------------------
// Normalisasi & perbandingan
// ---------------------------------------------------------------------------

/**
 * Dedupe (himpunan), buang no-op, dan urutkan secara kanonik. Idempoten dan
 * tidak bergantung urutan masukan; masukan tidak dimutasi.
 */
export function normalizeFilters(fs: readonly Predicate[] | null | undefined): FilterSet {
  const unique = new Map<string, Predicate>();
  for (const p of fs ?? []) {
    if (isNoopPredicate(p)) continue;
    const key = predicateKey(p);
    if (!unique.has(key)) unique.set(key, canonicalPredicate(p));
  }
  return [...unique.values()].sort((a, b) => compareTuples(sortTuple(a), sortTuple(b)));
}

/** Kesetaraan dua FilterSet sebagai himpunan predikat. */
export function filtersEqual(
  a: readonly Predicate[] | null | undefined,
  b: readonly Predicate[] | null | undefined,
): boolean {
  const na = normalizeFilters(a);
  const nb = normalizeFilters(b);
  if (na.length !== nb.length) return false;
  return na.every((p, i) => predicateKey(p) === predicateKey(nb[i]));
}

/**
 * `true` bila insight dihitung dengan filter berbeda dari Global_Filter aktif
 * (`normalize(filters_snapshot) != normalize(global_filters)`, Req 15.3).
 */
export function filtersDiffer(
  snapshot: readonly Predicate[] | null | undefined,
  globalFilters: readonly Predicate[] | null | undefined,
): boolean {
  return !filtersEqual(snapshot, globalFilters);
}

// ---------------------------------------------------------------------------
// Pembuat predikat
// ---------------------------------------------------------------------------

/** Rentang tanggal inklusif; ujung `null` berarti tidak dibatasi. */
export function dateRange(
  table: string,
  column: string,
  start: IsoDate | null,
  end: IsoDate | null,
): DateRangePredicate {
  return { kind: "date_range", table, column, start: toIsoDate(start), end: toIsoDate(end) };
}

/** Keanggotaan himpunan (nilai unik, urutan kanonik). */
export function inValues(table: string, column: string, values: readonly Scalar[]): InPredicate {
  return { kind: "in", table, column, values: canonicalValues(values) };
}

/**
 * Ganti semua predikat pada `(table, column)` dengan `next` (atau hapus bila
 * `next` null/no-op). Berguna untuk kontrol Global_Filter per kolom.
 */
export function setColumnFilter(
  fs: readonly Predicate[],
  table: string,
  column: string,
  next: Predicate | null,
): FilterSet {
  const rest = fs.filter((p) => !(p.table === table && p.column === column));
  return next && !isNoopPredicate(next) ? normalizeFilters([...rest, next]) : normalizeFilters(rest);
}

/** Hapus predikat yang ekuivalen dengan `target` (mis. tombol hapus chip). */
export function removePredicate(fs: readonly Predicate[], target: Predicate): FilterSet {
  const key = predicateKey(target);
  return fs.filter((p) => predicateKey(p) !== key);
}

// ---------------------------------------------------------------------------
// Cross_Filter (Req 24)
// ---------------------------------------------------------------------------

/** Elemen chart yang diklik, sudah di-resolve ke `(table, column)` sumber. */
export interface CrossFilterElement {
  table: string;
  column: string;
  value: Scalar;
}

/** Predikat Cross_Filter untuk elemen: `in {value}` pada `(table, column)`. */
export function crossFilterPredicate(element: CrossFilterElement): InPredicate {
  return { kind: "in", table: element.table, column: element.column, values: [element.value] };
}

/** `true` bila Cross_Filter untuk elemen ini sedang aktif. */
export function isCrossFilterActive(
  fs: readonly Predicate[],
  element: CrossFilterElement,
): boolean {
  const key = predicateKey(crossFilterPredicate(element));
  return fs.some((p) => predicateKey(p) === key);
}

/**
 * Toggle Cross_Filter untuk elemen chart (Property 33). Hasil selalu
 * FilterSet kanonik (`normalizeFilters`):
 * - elemen belum aktif → hasil = `fs` ∪ tepat satu predikat baru `in {value}`
 *   pada `(table, column)` elemen;
 * - elemen sudah aktif (klik kedua) → predikat tersebut dihapus.
 * Sehingga `toggleCrossFilter(toggleCrossFilter(fs, e), e)` identik dengan
 * `fs` untuk `fs` kanonik (semua hasil toggle kanonik) dan selalu sama dengan
 * `fs` sebagai himpunan (`filtersEqual`).
 */
export function toggleCrossFilter(
  fs: readonly Predicate[],
  element: CrossFilterElement,
): FilterSet {
  const pred = crossFilterPredicate(element);
  const key = predicateKey(pred);
  if (fs.some((p) => predicateKey(p) === key)) {
    return normalizeFilters(fs.filter((p) => predicateKey(p) !== key));
  }
  return normalizeFilters([...fs, pred]);
}

// ---------------------------------------------------------------------------
// Label UI
// ---------------------------------------------------------------------------

/** Tampilan skalar untuk chip filter. */
export function formatScalar(v: Scalar): string {
  if (v === null) return "(kosong)";
  if (typeof v === "boolean") return v ? "benar" : "salah";
  return String(v);
}

export interface DescribeOptions {
  /** Sertakan nama tabel (`tabel.kolom`); default `true`. */
  includeTable?: boolean;
  /** Maks nilai `in` yang ditampilkan sebelum diringkas; default 3. */
  maxValues?: number;
}

/** Label ringkas predikat untuk chip/penanda filter. */
export function describePredicate(p: Predicate, opts: DescribeOptions = {}): string {
  const { includeTable = true, maxValues = 3 } = opts;
  const field = includeTable ? `${p.table}.${p.column}` : p.column;
  if (p.kind === "date_range") {
    const start = toIsoDate(p.start);
    const end = toIsoDate(p.end);
    if (start && end) return start === end ? `${field}: ${start}` : `${field}: ${start} – ${end}`;
    if (start) return `${field} ≥ ${start}`;
    if (end) return `${field} ≤ ${end}`;
    return `${field}: semua tanggal`;
  }
  const values = canonicalValues(p.values);
  if (values.length === 0) return `${field}: (tidak ada nilai)`;
  if (values.length === 1) return `${field} = ${formatScalar(values[0])}`;
  const shown = values.slice(0, Math.max(1, maxValues)).map(formatScalar);
  const more = values.length - shown.length;
  return `${field} ∈ {${shown.join(", ")}${more > 0 ? `, +${more} lainnya` : ""}}`;
}

/** Label untuk seluruh FilterSet (ternormalisasi). */
export function describeFilters(fs: readonly Predicate[], opts: DescribeOptions = {}): string[] {
  return normalizeFilters(fs).map((p) => describePredicate(p, opts));
}
