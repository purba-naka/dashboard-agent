import { describe, expect, it } from "vitest";
import { DIVERGING, normalizeChart, orderCategories, referenceFor, STEP_COLORS } from "./chart-normalize";

const MONTHS = ["Januari", "Februari", "Maret"];
const GROUPS = ["A", "B", "C"];
const dims = ["bulan", "periode", "kelompok", "rata_ntp"];
// Urutan baris sengaja alfabetis (seperti query asli: ORDER BY bulan, kelompok bisa tetap diacak di UI).
const rows = MONTHS.flatMap((m, i) => GROUPS.map((g, j) => [i + 1, m, g, 100 + i * 10 + j]));

const lineOption = () => ({
  title: { text: "Judul", subtext: "Subjudul" },
  legend: { top: 30 },
  xAxis: { type: "category", name: "Bulan" },
  yAxis: { type: "value", name: "NTP" },
  series: GROUPS.map((g) => ({ type: "line", name: g, encode: { x: "periode", y: "rata_ntp" } })),
  dataset: { dimensions: dims, source: rows },
});

describe("normalizeChart", () => {
  it("hapus title dari canvas dan pindahkan subtext ke subtitle", () => {
    const r = normalizeChart(lineOption());
    expect(r.option.title).toBeUndefined();
    expect(r.subtitle).toBe("Subjudul");
  });

  it("pivot: satu series per kelompok, satu titik per bulan", () => {
    const r = normalizeChart(lineOption());
    expect(r.error).toBeUndefined();
    const series = r.option.series as { name: string; datasetIndex: number }[];
    expect(series.map((s) => s.name)).toEqual(GROUPS);
    for (const s of series) {
      const src = (r.option.dataset as { source: unknown[][] }[])[s.datasetIndex].source;
      expect(src).toHaveLength(MONTHS.length);
      expect(new Set(src.map((x) => x[1])).size).toBe(MONTHS.length);
      expect(src.every((x) => x[2] === s.name)).toBe(true);
    }
  });

  it("sumbu X kronologis, bukan alfabetis", () => {
    const shuffled = [...rows].reverse();
    const opt = lineOption();
    opt.dataset.source = shuffled;
    const r = normalizeChart(opt);
    expect((r.option.xAxis as { data: string[] }).data).toEqual(MONTHS);
  });

  it("guard: series ganda tanpa kolom pemisah → error, bukan render diam-diam", () => {
    const opt = lineOption();
    opt.series = opt.series.map((s, i) => ({ ...s, name: `X${i}` }));
    expect(normalizeChart(opt).error).toMatch(/series memakai kolom yang sama/);
  });

  it("guard: satu series dengan X berulang → error", () => {
    const opt = lineOption();
    opt.series = [opt.series[0]];
    expect(normalizeChart(opt).error).toMatch(/lebih dari satu titik/);
  });

  it("legend: ≤5 series di atas tanpa paginasi, >5 vertikal di kanan", () => {
    const few = normalizeChart(lineOption()).option.legend as Record<string, unknown>;
    expect(few).toMatchObject({ type: "plain", orient: "horizontal", top: 0 });
    const many = lineOption();
    const names = ["A", "B", "C", "D", "E", "F"];
    many.series = names.map((g) => ({ type: "line", name: g, encode: { x: "periode", y: "rata_ntp" } }));
    many.dataset.source = MONTHS.flatMap((m, i) => names.map((g) => [i + 1, m, g, 1]));
    const r = normalizeChart(many);
    expect(r.option.legend).toMatchObject({ type: "scroll", orient: "vertical", right: 0 });
    expect((r.option.grid as { right: number }).right).toBeGreaterThan(100);
  });

  it("label: tanpa outline; line hanya titik terakhir", () => {
    const opt = lineOption();
    (opt.series[0] as Record<string, unknown>).label = { show: true, textBorderColor: "#000", textBorderWidth: 3 };
    const s = (normalizeChart(opt).option.series as { label: Record<string, unknown> }[])[0];
    expect(s.label.textBorderWidth).toBe(0);
    expect(s.label.textBorderColor).toBe("transparent");
    const f = s.label.formatter as (p: unknown) => string;
    expect(f({ dataIndex: 0, value: [1, "Januari", "A", 100] })).toBe("");
    expect(f({ dataIndex: 2, value: [3, "Maret", "A", 120.5] })).toBe("120,5");
  });

  it("grid containLabel dan label sumbu tidak diputar", () => {
    const r = normalizeChart(lineOption());
    expect((r.option.grid as { containLabel: boolean }).containLabel).toBe(true);
    expect((r.option.xAxis as { axisLabel: { rotate: number } }).axisLabel.rotate).toBe(0);
  });

  it("heatmap: teks putih di sel gelap, gelap di sel terang", () => {
    const r = normalizeChart({
      xAxis: { type: "category" },
      yAxis: { type: "category" },
      visualMap: { min: 0, max: 10, inRange: { color: ["#ffffff", "#000000"] } },
      series: [{ type: "heatmap", encode: { x: "k", y: "p", value: "v" } }],
      dataset: { dimensions: ["k", "p", "v"], source: [["a", "b", 0], ["a", "c", 10]] },
    });
    const f = (r.option.series as { label: { formatter: (p: unknown) => string } }[])[0].label.formatter;
    expect(f({ value: ["a", "b", 0] })).toBe("{d|0}");
    expect(f({ value: ["a", "c", 10] })).toBe("{l|10}");
  });

  it("heatmap: desimal id-ID, visualMap vertikal di kanan", () => {
    const r = normalizeChart({
      xAxis: { type: "category" },
      yAxis: { type: "category" },
      visualMap: { min: 85, max: 230, orient: "horizontal", left: "center", inRange: { color: ["#f28e2b", "#ffffff", "#4c9f70"] } },
      series: [{ type: "heatmap", encode: { x: "kelompok", y: "provinsi", value: "rata_ntp" }, label: { show: true } }],
      dataset: { dimensions: ["provinsi", "kelompok", "rata_ntp"], source: [["Aceh", "A", 128.58]] },
    });
    const f = (r.option.series as { label: { formatter: (p: unknown) => string } }[])[0].label.formatter;
    expect(f({ value: ["Aceh", "A", 128.58] })).toMatch(/\|128,58}$/);
    expect((r.option.visualMap as Record<string, unknown>[])[0]).toMatchObject({ orient: "vertical", right: 0 });
  });

  it("heatmap indeks: divergen oranye–biru, netral tepat 100, atas dibatasi p95, baris urut rata-rata", () => {
    const source = [["Aceh", "A", 90], ["Bali", "A", 110], ["Riau", "A", 105], ...Array.from({ length: 30 }, (_, i) => ["Riau", `K${i}`, 105]), ["Bali", "Z", 228]];
    const r = normalizeChart({
      xAxis: { type: "category" },
      yAxis: { type: "category" },
      visualMap: { min: 85, max: 230, inRange: { color: ["#f28e2b", "#ffffff", "#4c9f70"] } },
      series: [{ type: "heatmap", encode: { x: "kelompok", y: "provinsi", value: "rata_ntp" } }],
      dataset: { dimensions: ["provinsi", "kelompok", "rata_ntp"], source },
    });
    const vm = (r.option.visualMap as { min: number; max: number; inRange: { color: string[] } }[])[0];
    expect((vm.min + vm.max) / 2).toBe(100);
    expect(vm.max).toBeLessThan(228);
    expect(vm.inRange.color).toEqual(DIVERGING);
    expect((r.option.yAxis as { data: string[] }).data).toEqual(["Aceh", "Riau", "Bali"]);
  });

  it("kolom indeks: markLine di 100 dan tooltip selisih acuan", () => {
    const r = normalizeChart({
      xAxis: { type: "category" },
      yAxis: { type: "value" },
      series: [{ type: "bar", encode: { x: "provinsi", y: "rata_ntp" } }],
      dataset: { dimensions: ["provinsi", "rata_ntp"], source: [["Aceh", 123.6]] },
    });
    const s = (r.option.series as { markLine: { data: unknown[] } }[])[0];
    expect(s.markLine.data).toEqual([{ yAxis: 100 }]);
    const tf = (r.option.tooltip as { valueFormatter: (v: number) => string }).valueFormatter;
    expect(tf(123.6)).toBe("123,6 (+23,6 vs acuan 100)");
  });

  it("warna konsisten per nilai dimensi antar-chart", () => {
    const a = normalizeChart(lineOption()).option.series as { name: string; itemStyle: { color: string } }[];
    const b = normalizeChart({ ...lineOption(), series: [...GROUPS].reverse().map((g) => ({ type: "line", name: g, encode: { x: "periode", y: "rata_ntp" } })) }).option.series as { name: string; itemStyle: { color: string } }[];
    for (const s of a) expect(b.find((x) => x.name === s.name)!.itemStyle.color).toBe(s.itemStyle.color);
    expect(new Set(a.map((s) => s.itemStyle.color)).size).toBe(GROUPS.length);
  });

  it("bukan indeks: tanpa markLine", () => {
    const r = normalizeChart({ series: [{ type: "bar", encode: { x: "k", y: "omzet" } }] });
    expect((r.option.series as { markLine?: unknown }[])[0].markLine).toBeUndefined();
    expect(referenceFor("omzet")).toBeUndefined();
    expect(referenceFor("rata_ntp")).toBe(100);
  });
});

describe("waterfall", () => {
  const wf = (withKind: boolean) => ({
    legend: { show: false },
    xAxis: { type: "category" },
    yAxis: { type: "value" },
    series: [
      { type: "bar", name: "base", stack: "total", itemStyle: { color: "transparent" }, encode: { x: "step", y: "base" } },
      { type: "bar", name: "Nilai", stack: "total", encode: { x: "step", y: "delta" } },
    ],
    dataset: {
      dimensions: withKind ? ["step", "base", "delta", "step_kind"] : ["step", "base", "delta"],
      source: withKind
        ? [["Revenue", 0, 100, "total"], ["COGS", 60, 40, "down"], ["Bonus", 60, 10, "up"]]
        : [["Revenue", 0, 100], ["COGS", 60, 40]],
    },
  });
  type S = { name: string; itemStyle: { color: unknown }; label: { show: boolean }; tooltip?: { show: boolean }; emphasis?: { disabled: boolean }; silent?: boolean; markLine?: unknown };

  it("base tetap transparan, tanpa label/tooltip/hover, tidak di legend", () => {
    const r = normalizeChart(wf(false));
    const [base, delta] = r.option.series as S[];
    expect(base.itemStyle.color).toBe("transparent");
    expect(base.label.show).toBe(false);
    expect(base.tooltip?.show).toBe(false);
    expect(base.emphasis?.disabled).toBe(true);
    expect(base.silent).toBe(true);
    expect(base.markLine).toBeUndefined();
    expect(delta.label.show).toBe(true);
    expect(r.option.legend).toBeUndefined();
  });

  it("base tidak diberi markLine acuan walau kolom indeks", () => {
    const o = wf(false);
    o.series[0].encode.y = "rata_ntp";
    o.dataset.dimensions = ["step", "rata_ntp", "delta"];
    const [base] = normalizeChart(o).option.series as S[];
    expect(base.markLine).toBeUndefined();
  });

  it("warna delta mengikuti step_kind", () => {
    const [, delta] = normalizeChart(wf(true)).option.series as S[];
    const f = delta.itemStyle.color as (p: unknown) => string;
    expect(f({ value: ["Revenue", 0, 100, "total"] })).toBe(STEP_COLORS.total);
    expect(f({ value: ["COGS", 60, 40, "down"] })).toBe(STEP_COLORS.down);
    expect(f({ value: ["Bonus", 60, 10, "up"] })).toBe(STEP_COLORS.up);
  });

  it("tanpa step_kind: satu warna", () => {
    const [, delta] = normalizeChart(wf(false)).option.series as S[];
    expect(typeof delta.itemStyle.color).toBe("string");
  });
});

describe("orderCategories", () => {
  it("bukan bulan → urutan sumber", () => {
    expect(orderCategories(["b", "a", "b"])).toEqual(["b", "a"]);
  });
});
