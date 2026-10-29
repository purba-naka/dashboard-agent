/**
 * Props semantik komponen chart → option ECharts (D4).
 *
 * Data berasal dari baris hasil `render_query` (dataset `{dimensions, source}`
 * seperti `bind_data` backend), lalu dirapikan `normalizeChart` agar tampilan
 * sama dengan canvas legacy. Tidak ada angka yang dibuat di sini selain
 * turunan langsung dari baris (mis. akumulasi waterfall).
 */
import { STEP_PALETTE } from "@/lib/echarts-theme";
import type { Scalar } from "@/lib/types";

type O = Record<string, unknown>;
export type Row = Record<string, Scalar>;

export interface ChartInput {
  component: string;
  props: Record<string, unknown>;
  columns: string[];
  rows: Row[];
}

const opt = (props: Record<string, unknown>): O => (props.options as O | undefined) ?? {};
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
const strs = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : typeof v === "string" ? [v] : [];

function dataset(columns: string[], rows: Row[]): O {
  return { dimensions: columns, source: rows.map((r) => columns.map((c) => r[c] ?? null)) };
}

const BASE: O = { tooltip: { trigger: "axis" }, grid: { containLabel: true, left: 8, right: 16, top: 32, bottom: 8 } };

export function buildChartOption({ component, props, columns, rows }: ChartInput): O {
  const o = opt(props);
  const subtitle = str(o.subtitle);
  const title = subtitle ? { title: { subtext: subtitle } } : {};
  switch (component) {
    case "LineChart": {
      const x = str(props.x) as string;
      return {
        ...BASE,
        ...title,
        legend: {},
        dataset: dataset(columns, rows),
        xAxis: { type: "category" },
        yAxis: { type: "value" },
        series: strs(props.y).map((y) => ({
          type: "line",
          name: y,
          encode: { x, y },
          smooth: o.smooth === true || undefined,
          areaStyle: o.area === true ? {} : undefined,
        })),
      };
    }
    case "BarChart": {
      const x = str(props.x) as string;
      const horizontal = o.horizontal === true;
      const lines = new Set(strs(o.lines));
      const cat = { type: "category" };
      const val = { type: "value" };
      return {
        ...BASE,
        ...title,
        legend: {},
        dataset: dataset(columns, rows),
        xAxis: horizontal ? val : cat,
        yAxis: horizontal ? cat : val,
        series: strs(props.y).map((y) => ({
          type: lines.has(y) ? "line" : "bar",
          name: y,
          encode: horizontal ? { y: x, x: y } : { x, y },
          stack: o.stack === true && !lines.has(y) ? "total" : undefined,
        })),
      };
    }
    case "PieChart":
      return {
        ...title,
        tooltip: { trigger: "item" },
        legend: {},
        dataset: dataset(columns, rows),
        series: [
          {
            type: "pie",
            radius: o.donut === true ? ["45%", "70%"] : "70%",
            encode: { itemName: str(props.category), value: str(props.value) },
          },
        ],
      };
    case "ScatterChart": {
      const x = str(props.x) as string;
      const y = str(props.y) as string;
      const series = str(o.series);
      if (!series) {
        return {
          ...BASE,
          ...title,
          tooltip: { trigger: "item" },
          dataset: dataset(columns, rows),
          xAxis: { type: "value", name: x },
          yAxis: { type: "value", name: y },
          series: [{ type: "scatter", encode: { x, y } }],
        };
      }
      const groups = [...new Set(rows.map((r) => r[series]))];
      return {
        ...BASE,
        ...title,
        tooltip: { trigger: "item" },
        legend: {},
        dataset: groups.map((g) => dataset(columns, rows.filter((r) => r[series] === g))),
        xAxis: { type: "value", name: x },
        yAxis: { type: "value", name: y },
        series: groups.map((g, i) => ({ type: "scatter", name: String(g), datasetIndex: i, encode: { x, y } })),
      };
    }
    case "Heatmap": {
      const value = str(props.value) as string;
      const nums = rows.map((r) => r[value]).filter((v): v is number => typeof v === "number");
      return {
        ...title,
        tooltip: { trigger: "item" },
        grid: { containLabel: true, left: 8, right: 16, top: 16, bottom: 48 },
        dataset: dataset(columns, rows),
        xAxis: { type: "category" },
        yAxis: { type: "category" },
        visualMap: {
          min: nums.length ? Math.min(...nums) : 0,
          max: nums.length ? Math.max(...nums) : 1,
          dimension: value,
          calculable: true,
          orient: "horizontal",
          left: "center",
          bottom: 0,
        },
        series: [{ type: "heatmap", encode: { x: str(props.x), y: str(props.y), value } }],
      };
    }
    case "Waterfall":
      return waterfall(props, rows, title);
    default:
      return {};
  }
}

/** Waterfall: batang transparan `base` + naik/turun dari nilai bertanda per baris. */
function waterfall(props: Record<string, unknown>, rows: Row[], title: O): O {
  const category = str(props.category) as string;
  const value = str(props.value) as string;
  let running = 0;
  const source: (Scalar | number)[][] = [];
  for (const r of rows) {
    const v = typeof r[value] === "number" ? (r[value] as number) : 0;
    const start = running;
    running += v;
    source.push([r[category] ?? null, Math.min(start, running), v >= 0 ? v : 0, v < 0 ? -v : 0]);
  }
  source.push(["Total", 0, running >= 0 ? running : 0, running < 0 ? -running : 0]);
  return {
    ...BASE,
    ...title,
    legend: { data: ["Naik", "Turun"] },
    dataset: { dimensions: [category, "base", "Naik", "Turun"], source },
    xAxis: { type: "category" },
    yAxis: { type: "value" },
    series: [
      { type: "bar", name: "base", stack: "wf", encode: { x: category, y: "base" }, itemStyle: { color: "transparent" }, tooltip: { show: false } },
      { type: "bar", name: "Naik", stack: "wf", encode: { x: category, y: "Naik" }, itemStyle: { color: STEP_PALETTE.up } },
      { type: "bar", name: "Turun", stack: "wf", encode: { x: category, y: "Turun" }, itemStyle: { color: STEP_PALETTE.down } },
    ],
  };
}
