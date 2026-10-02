import * as echarts from "echarts";
import { useSyncExternalStore } from "react";

/** Tableau 10. Semua chart memakai palet ini lewat theme, bukan warna per chart. */
export const PALETTE = [
  "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
];

// Teks ≥4.5:1 terhadap --surface (#161a21 gelap, #ffffff terang); gridline samar.
function theme(text: string, muted: string, grid: string, axis: string) {
  const axisCommon = {
    axisLine: { lineStyle: { color: axis } },
    axisTick: { lineStyle: { color: axis } },
    axisLabel: { color: muted },
    splitLine: { lineStyle: { color: grid, width: 1 } },
    nameTextStyle: { color: muted },
  };
  return {
    color: PALETTE,
    backgroundColor: "transparent",
    textStyle: { color: text },
    legend: { textStyle: { color: text }, pageTextStyle: { color: muted } },
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    visualMap: { textStyle: { color: muted } },
    bar: { label: { color: text } },
    line: { label: { color: text } },
    pie: { label: { color: text } },
  };
}

echarts.registerTheme("studio-dark", theme("#e6e9ee", "#b4bcc8", "#232832", "#3a4250"));
echarts.registerTheme("studio-light", theme("#16191f", "#4b5563", "#eef0f3", "#c9ced6"));

const QUERY = "(prefers-color-scheme: dark)";
const media = () => (typeof window.matchMedia === "function" ? window.matchMedia(QUERY) : null);
const subscribe = (cb: () => void) => {
  const m = media();
  m?.addEventListener("change", cb);
  return () => m?.removeEventListener("change", cb);
};

/** Nama theme ECharts sesuai `prefers-color-scheme` (sumber mode gelap yang sama dengan CSS). */
export function useChartTheme(): string {
  return useSyncExternalStore(
    subscribe,
    () => (media()?.matches ? "studio-dark" : "studio-light"),
    () => "studio-light",
  );
}

const registry = new Map<string, string>();

/** Warna stabil per nilai dimensi: nilai yang sama selalu warna yang sama di semua chart. */
// ponytail: urutan kemunculan per sesi tab, cycle setelah 10 nilai; simpan di model semantik bila perlu stabil lintas reload.
export function colorFor(value: unknown): string {
  const k = String(value);
  let c = registry.get(k);
  if (!c) {
    c = PALETTE[registry.size % PALETTE.length];
    registry.set(k, c);
  }
  return c;
}
