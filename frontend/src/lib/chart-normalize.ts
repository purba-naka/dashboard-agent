/* eslint-disable @typescript-eslint/no-explicit-any -- option ECharts bersifat dinamis (JSON dari LLM). */
/**
 * Normalizer presentasi chart: satu tempat untuk merapikan option ECharts yang
 * ditulis LLM (judul, grid, legend, label, pivot series) sebelum dirender.
 * Murni; input tidak dimutasi. Spec tersimpan (v1–v9) tidak berubah.
 */
import { colorAt, parseColor, readableOn, TEXT_DARK, TEXT_LIGHT } from "./contrast";
import { formatNumber } from "./format-number";
import { colorFor, PALETTE } from "./echarts-theme";

type O = Record<string, any>;

export interface NormalizeResult {
  option: O;
  /** Subtitle (dari `title.subtext`) untuk header kartu HTML. */
  subtitle?: string;
  /** Spec tidak bisa dirender benar; UI menampilkan pesan, bukan chart rusak. */
  error?: string;
}

export const LEGEND_MAX_INLINE = 5;
export const BAR_LABEL_MAX_CATEGORIES = 12;
const HEATMAP_LABEL_MAX_CELLS = 80;
const LEGEND_NAME_MAX = 22;
/** Oranye (< acuan) – netral – biru (> acuan); ramah buta warna, default diverging Tableau. */
export const DIVERGING = ["#c8611b", "#f28e2b", "#eeeeee", "#6a9fd4", "#2b5c8f"];

// ponytail: acuan indeks dari nama kolom (NTP/indeks = 100). Upgrade: kirim
// `reference_value` model semantik lewat render response bila ada indeks lain.
const INDEX_NAME = /(^|_)(ntp|ntup|indeks|index|idx|ihk)(_|$)/i;
export function referenceFor(dim: unknown): number | undefined {
  return typeof dim === "string" && INDEX_NAME.test(dim) ? 100 : undefined;
}

function quantile(sorted: number[], q: number): number {
  return sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
}

/** visualMap divergen simetris di sekitar `ref` (netral tepat di tengah), atas dibatasi p95. */
function divergingMap(m: O, values: number[], ref: number): O {
  const sorted = [...values].sort((a, b) => a - b);
  const half = Math.max(ref - sorted[0], quantile(sorted, 0.95) - ref, 1);
  return { ...m, type: "continuous", min: ref - half, max: ref + half, inRange: { color: DIVERGING }, calculable: false };
}

function signed(d: number): string {
  return `${d > 0 ? "+" : d < 0 ? "−" : "±"}${formatNumber(Math.abs(d), 1)}`;
}

const MONTHS = [
  ["januari", "january", "jan"],
  ["februari", "february", "feb"],
  ["maret", "march", "mar"],
  ["april", "apr"],
  ["mei", "may"],
  ["juni", "june", "jun"],
  ["juli", "july", "jul"],
  ["agustus", "august", "agu", "aug"],
  ["september", "sep", "sept"],
  ["oktober", "october", "okt", "oct"],
  ["november", "nov", "nopember"],
  ["desember", "december", "des", "dec"],
];

function monthIndex(v: unknown): number {
  const s = String(v).trim().toLowerCase();
  return MONTHS.findIndex((names) => names.includes(s));
}

/** Urut kronologis bila semua nilai nama bulan; selain itu urutan sumber. */
export function orderCategories(values: unknown[]): unknown[] {
  const uniq = [...new Set(values)];
  if (uniq.length > 1 && uniq.every((v) => monthIndex(v) >= 0)) {
    return uniq.sort((a, b) => monthIndex(a) - monthIndex(b));
  }
  return uniq;
}

/** Warna langkah waterfall dari kolom `step_kind`: total biru, turun oranye, naik hijau. */
export const STEP_COLORS: Record<string, string> = { total: PALETTE[0], down: PALETTE[1], up: PALETTE[4] };

const asArray = (v: unknown): any[] => (Array.isArray(v) ? v : v == null ? [] : [v]);

/** Seri `base` waterfall: bar transparan yang bertumpuk (`stack` sama) dengan bar lain. */
function isWaterfallBase(all: O[], s: O): boolean {
  return (
    s?.type === "bar" &&
    s.stack != null &&
    s.itemStyle?.color === "transparent" &&
    all.some((t) => t !== s && t?.type === "bar" && t.stack === s.stack && t.itemStyle?.color !== "transparent")
  );
}
const first = (v: unknown): unknown => (Array.isArray(v) ? v[0] : v);
const isHorizontal = (o: O): boolean => asArray(o.yAxis)[0]?.type === "category";

function truncate(name: unknown): string {
  const s = String(name);
  return s.length > LEGEND_NAME_MAX ? `${s.slice(0, LEGEND_NAME_MAX - 1)}…` : s;
}

/** Pivot series berencode identik per nilai dimensi (kolom string) yang namanya cocok. */
function pivotSeries(o: O, dims: string[], rows: unknown[][]): string | null {
  const series = asArray(o.series).filter((s) => s?.type === "line" || s?.type === "bar");
  const groups = new Map<string, any[]>();
  for (const s of series) {
    const key = JSON.stringify([s.type, s.encode, s.yAxisIndex ?? 0]);
    groups.set(key, [...(groups.get(key) ?? []), s]);
  }
  const replaced = new Map<any, any[]>();
  const datasets: O[] = [{ dimensions: dims, source: rows }];

  for (const group of groups.values()) {
    const xName = first(group[0].encode?.x);
    const xi = dims.indexOf(xName as string);
    const yi = dims.indexOf(first(group[0].encode?.y) as string);
    if (xi < 0 || yi < 0) continue;
    if (group.length > 1) {
      const names = group.map((s) => String(s.name ?? ""));
      const col = dims.findIndex(
        (d, i) =>
          i !== xi &&
          i !== yi &&
          names.every((n) => rows.some((r) => String(r[i]) === n)),
      );
      if (col < 0) {
        return `${group.length} series memakai kolom yang sama (${String(xName)}, ${String(first(group[0].encode?.y))}) tanpa kolom pemisah yang cocok dengan nama series.`;
      }
      const out = group.map((s) => {
        datasets.push({
          dimensions: dims,
          source: rows.filter((r) => String(r[col]) === String(s.name)),
        });
        return { ...s, datasetIndex: datasets.length - 1 };
      });
      group.forEach((s, i) => replaced.set(s, [out[i]]));
    } else {
      if (new Set(rows.map((r) => r[xi])).size < rows.length) {
        return `Series "${String(group[0].name ?? "")}" punya lebih dari satu titik pada nilai ${String(xName)} yang sama; dimensi series tidak ditentukan.`;
      }
    }
  }
  if (datasets.length === 1) return null;
  o.series = asArray(o.series).flatMap((s) => replaced.get(s) ?? [s]);
  o.dataset = datasets;
  return null;
}

function valueDim(o: O, s: O): string | undefined {
  const e = s.encode ?? {};
  if (s.type === "heatmap") return first(e.value) as string;
  if (s.type === "pie") return first(e.value) as string;
  return (isHorizontal(o) ? first(e.x) : first(e.y)) as string;
}

function rowValue(p: any, idx: number): unknown {
  if (Array.isArray(p?.value)) return p.value[idx];
  if (Array.isArray(p?.data)) return p.data[idx];
  return p?.value;
}

function datasetOf(o: O, s: O): O | undefined {
  return Array.isArray(o.dataset) ? o.dataset[s.datasetIndex ?? 0] : o.dataset;
}

/** "d" (teks gelap) atau "l" (terang), mana yang kontrasnya lebih tinggi pada sel bernilai `v`. */
function heatTextStyle(vm: unknown, v: unknown): "d" | "l" {
  const m = asArray(vm)[0];
  const colors: unknown[] = m?.inRange?.color ?? [];
  const rgb = colors.map(parseColor);
  if (typeof v !== "number" || typeof m?.min !== "number" || typeof m?.max !== "number" || rgb.some((c) => !c)) {
    return "d";
  }
  const t = m.max === m.min ? 0.5 : (v - m.min) / (m.max - m.min);
  const hex = rgb.map((c) => `#${c!.map((n) => Math.round(n).toString(16).padStart(2, "0")).join("")}`);
  return readableOn(colorAt(hex, t)) === TEXT_LIGHT ? "l" : "d";
}

function applyLabels(o: O, categoryCount: number, ref: number | undefined): void {
  const horizontal = isHorizontal(o);
  const all = asArray(o.series);
  const multi = all.length > 1;
  let firstValueSeries: O | undefined;
  o.series = all.map((raw) => {
    const s: O = { ...raw };
    if (isWaterfallBase(all, raw)) {
      s.itemStyle = { ...(s.itemStyle ?? {}), color: "transparent" };
      s.label = { show: false };
      s.tooltip = { ...(s.tooltip ?? {}), show: false };
      s.emphasis = { ...(s.emphasis ?? {}), disabled: true };
      s.silent = true;
      delete s.markLine;
      return s;
    }
    firstValueSeries ??= s;
    const ds = datasetOf(o, s);
    const dims: string[] = ds?.dimensions ?? [];
    const idx = dims.indexOf(valueDim(o, s) as string);
    const fmt = (p: any) => formatNumber(rowValue(p, idx));
    const label: O = { ...(s.label ?? {}) };
    delete label.textShadowColor;
    // zrender menambah outsideStroke otomatis (warna background) bila textBorderColor kosong.
    label.textBorderColor = "transparent";
    label.textBorderWidth = 0;
    label.textShadowBlur = 0;
    const kindIdx = dims.indexOf("step_kind");
    if (s.type === "bar" && kindIdx >= 0 && all.some((t) => isWaterfallBase(all, t))) {
      const fallback = colorFor(s.name);
      s.itemStyle = {
        ...(s.itemStyle ?? {}),
        color: (p: any) => STEP_COLORS[String(rowValue(p, kindIdx))] ?? fallback,
      };
    } else if ((s.type === "line" || s.type === "bar") && multi) {
      s.itemStyle = { ...(s.itemStyle ?? {}), color: colorFor(s.name) };
    } else if (s.type === "pie") {
      s.itemStyle = { ...(s.itemStyle ?? {}), color: (p: any) => colorFor(p.name) };
    }
    if (ref !== undefined && (s.type === "line" || s.type === "bar") && s === firstValueSeries) {
      s.markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { type: "dashed", width: 1, opacity: 0.8 },
        // Label garis menimpa label data; nilai acuan cukup di tooltip.
        label: { show: false },
        tooltip: { formatter: `Acuan ${formatNumber(ref)}` },
        data: [horizontal ? { xAxis: ref } : { yAxis: ref }],
      };
    }

    if (s.type === "bar") {
      label.show = categoryCount <= BAR_LABEL_MAX_CATEGORIES;
      label.position = horizontal ? "right" : "top";
      label.formatter = fmt;
    } else if (s.type === "line") {
      const n = (ds?.source ?? []).length;
      label.show = true;
      label.position = "right";
      label.color = "inherit";
      label.formatter = (p: any) => (p.dataIndex === n - 1 ? fmt(p) : "");
    } else if (s.type === "heatmap") {
      label.show = categoryCount <= HEATMAP_LABEL_MAX_CELLS;
      label.rich = { d: { color: TEXT_DARK }, l: { color: TEXT_LIGHT } };
      label.formatter = (p: any) => {
        const v = rowValue(p, idx);
        return `{${heatTextStyle(o.visualMap, v)}|${formatNumber(v)}}`;
      };
    } else if (s.type === "scatter") {
      label.show = false;
    }
    s.label = label;
    s.labelLayout = { hideOverlap: true, moveOverlap: "shiftY" };
    return s;
  });
}

function categoryTotal(o: O, rows: unknown[][], dims: string[]): number {
  const s = asArray(o.series)[0];
  if (!s) return 0;
  if (s.type === "heatmap") {
    const xi = dims.indexOf(first(s.encode?.x) as string);
    const yi = dims.indexOf(first(s.encode?.y) as string);
    return new Set(rows.map((r) => r[xi])).size * new Set(rows.map((r) => r[yi])).size;
  }
  if (s.type === "pie") return rows.length;
  const catDim = (isHorizontal(o) ? first(s.encode?.y) : first(s.encode?.x)) as string;
  const ci = dims.indexOf(catDim);
  return ci < 0 ? rows.length : new Set(rows.map((r) => r[ci])).size;
}

export function normalizeChart(input: O): NormalizeResult {
  const o: O = structuredClone(input);
  const result: NormalizeResult = { option: o };

  const title = asArray(o.title)[0];
  if (title && typeof title.subtext === "string" && title.subtext.trim()) {
    result.subtitle = title.subtext.trim();
  }
  delete o.title;
  // Warna, latar, dan teks dari theme global, bukan per chart.
  delete o.color;
  delete o.backgroundColor;
  delete o.textStyle;

  const baseDs: O | undefined = Array.isArray(o.dataset) ? o.dataset[0] : o.dataset;
  const dims: string[] = baseDs?.dimensions ?? [];
  const rows: unknown[][] = baseDs?.source ?? [];
  const chartType = asArray(o.series)[0]?.type as string | undefined;

  if (chartType === "line" || chartType === "bar") {
    const err = pivotSeries(o, dims, rows);
    if (err) {
      result.error = err;
      return result;
    }
    // Sumbu kategori: urutan eksplisit (kronologis bila nama bulan), bukan urutan kemunculan.
    const x = asArray(o.xAxis)[0];
    const xs = asArray(o.series)[0]?.encode?.x;
    const xi = dims.indexOf(first(xs) as string);
    if (!isHorizontal(o) && x?.type === "category" && xi >= 0) {
      const ordered = orderCategories(rows.map((r) => r[xi]));
      o.xAxis = Array.isArray(o.xAxis)
        ? o.xAxis.map((a: O, i: number) => (i === 0 ? { ...a, data: ordered } : a))
        : { ...x, data: ordered };
    }
  }

  const visibleSeries = asArray(o.series).filter((s, _i, all) => !isWaterfallBase(all, s));
  const seriesCount = visibleSeries.length;
  const legendItems = chartType === "pie" ? rows.length : seriesCount;
  const needsLegend = legendItems > 1;
  const sideLegend = legendItems > LEGEND_MAX_INLINE;
  const baseLegend: O = needsLegend || o.legend ? { ...(asArray(o.legend)[0] ?? {}) } : {};
  const grid: O = { containLabel: true, left: 12, right: 16, top: 16, bottom: 12 };

  if (needsLegend) {
    const common = { show: true, formatter: truncate, tooltip: { show: true }, itemWidth: 14, itemHeight: 8 };
    if (visibleSeries.length < asArray(o.series).length) baseLegend.data = visibleSeries.map((s) => s.name);
    o.legend = sideLegend
      ? { ...baseLegend, ...common, type: "scroll", orient: "vertical", left: undefined, right: 0, top: 8, bottom: 8, width: 150 }
      : { ...baseLegend, ...common, type: "plain", orient: "horizontal", left: "center", right: undefined, top: 0, bottom: undefined };
    if (sideLegend) grid.right = 170;
    else grid.top = 36;
  } else {
    delete o.legend;
  }

  if (chartType && chartType !== "pie") {
    const mapAxis = (a: O | undefined, role: "x" | "y"): O => {
      a = a ?? {};
      const out: O = { ...a };
      const label: O = { ...(a.axisLabel ?? {}) };
      delete label.rotate;
      delete label.interval;
      delete label.color;
      if (a.type === "category") {
        label.rotate = 0;
        label.width = role === "y" ? 150 : 90;
        // X: wrap (tanpa rotasi); Y: potong + nama lengkap di tooltip.
        label.overflow = role === "x" ? "break" : "truncate";
        if (chartType === "heatmap" && role === "x") {
          label.interval = 0;
          label.width = 64;
        }
      } else {
        label.formatter = (v: number) => formatNumber(v);
      }
      label.hideOverlap = true;
      out.axisLabel = label;
      if (a.name) {
        if (role === "x" && !isHorizontal(o)) {
          out.nameLocation = "middle";
          out.nameGap = 28;
          grid.bottom = 40;
        } else if (role === "y" && isHorizontal(o)) {
          out.nameLocation = "end";
          out.nameTextStyle = { ...(a.nameTextStyle ?? {}), align: "left" };
          grid.top += 22;
        } else if (role === "x") {
          out.nameLocation = "middle";
          out.nameGap = 28;
          grid.bottom = 40;
        } else {
          out.nameLocation = "end";
          out.nameTextStyle = { ...(a.nameTextStyle ?? {}), align: "left" };
          grid.top += 22;
        }
      }
      return out;
    };
    const mapAll = (v: unknown, role: "x" | "y") =>
      Array.isArray(v) ? v.map((a) => mapAxis(a, role)) : mapAxis(v as O | undefined, role);
    o.xAxis = mapAll(o.xAxis, "x");
    o.yAxis = mapAll(o.yAxis, "y");
    // Label kategori terakhir di sumbu X tidak terpotong di tepi kanan.
    if (!isHorizontal(o) && asArray(o.xAxis)[0]?.type === "category") grid.right = Math.max(grid.right, 36);
    // Label nilai di ujung bar horizontal tidak dihitung containLabel.
    if (isHorizontal(o) && chartType === "bar") grid.right = Math.max(grid.right, 56);
    // Label nilai titik terakhir line chart juga di luar containLabel.
    if (chartType === "line") grid.right = sideLegend ? grid.right + 52 : Math.max(grid.right, 52);
    if (o.visualMap) {
      // visualMap vertikal di kanan: tidak menimpa label sumbu X.
      o.visualMap = asArray(o.visualMap).map((m: O) => ({
        ...m, orient: "vertical", left: undefined, bottom: undefined, right: 0, top: "middle", itemHeight: 120,
      }));
      grid.right = Math.max(grid.right, 90);
    }
    o.grid = grid;
  } else {
    delete o.grid;
  }

  const s0 = asArray(o.series)[0];
  const ref = s0 ? referenceFor(valueDim(o, s0)) : undefined;

  if (chartType === "heatmap" && s0) {
    const vi = dims.indexOf(valueDim(o, s0) as string);
    const yi = dims.indexOf(first(s0.encode?.y) as string);
    const values = rows.map((r) => r[vi]).filter((v): v is number => typeof v === "number");
    if (ref !== undefined && values.length && o.visualMap) {
      o.visualMap = asArray(o.visualMap).map((m: O, i: number) => (i === 0 ? divergingMap(m, values, ref) : m));
    }
    // Baris urut rata-rata: tertinggi di atas (hormati `inverse`), sama seperti ranking bar.
    const y = asArray(o.yAxis)[0];
    if (yi >= 0 && vi >= 0 && y?.type === "category") {
      const sums = new Map<unknown, [number, number]>();
      for (const r of rows) {
        if (typeof r[vi] !== "number") continue;
        const [s, n] = sums.get(r[yi]) ?? [0, 0];
        sums.set(r[yi], [s + (r[vi] as number), n + 1]);
      }
      const avg = (k: unknown) => { const [s, n] = sums.get(k) ?? [0, 1]; return s / n; };
      const dir = y.inverse ? -1 : 1;
      const data = [...new Set(rows.map((r) => r[yi]))].sort((a, b) => dir * (avg(a) - avg(b)));
      o.yAxis = Array.isArray(o.yAxis) ? o.yAxis.map((a: O, i: number) => (i === 0 ? { ...a, data } : a)) : { ...y, data };
    }
  }

  applyLabels(o, categoryTotal(o, rows, dims), ref);
  const valueFormatter = (v: unknown) =>
    ref !== undefined && typeof v === "number"
      ? `${formatNumber(v)} (${signed(v - ref)} vs acuan ${formatNumber(ref)})`
      : formatNumber(v);
  o.tooltip = { ...(asArray(o.tooltip)[0] ?? {}), confine: true, valueFormatter };
  return result;
}
