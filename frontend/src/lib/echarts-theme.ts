import * as echarts from "echarts";
import { useMemo, useSyncExternalStore } from "react";

/**
 * Palet kategorikal: seri pertama biru aksen, sisanya biru, slate, teal.
 * Nada tengah agar terbaca di latar terang dan gelap (warna dihitung sekali, bukan per tema).
 */
export const PALETTE = [
  "#3b6fe0", "#8a94a6", "#5ab0e8", "#2a8f8a", "#a3b8e0",
  "#6b7280", "#7aa2f7", "#4b9c9a", "#b0b8c4", "#3d5a99",
];

/** Warna semantik langkah waterfall, terpisah dari palet kategorikal. */
export const STEP_PALETTE = { total: PALETTE[0], down: "#d0554b", up: "#2f9e5f" } as const;

const FALLBACK = { text: "#16191f", muted: "#5b6472", grid: "#e2e5ea", axis: "#c9ced6", font: "sans-serif" };

/** Baca token warna dari CSS var di `<html>` (sumber yang sama dengan UI). */
function readTokens() {
  if (typeof document === "undefined") return FALLBACK;
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fb: string) => css.getPropertyValue(name).trim() || fb;
  return {
    text: v("--text", FALLBACK.text),
    muted: v("--text-muted", FALLBACK.muted),
    grid: v("--border", FALLBACK.grid),
    axis: v("--border-strong", FALLBACK.axis),
    font: getComputedStyle(document.body).fontFamily || FALLBACK.font,
  };
}

function register(name: string) {
  const { text, muted, grid, axis, font } = readTokens();
  const axisCommon = {
    axisLine: { lineStyle: { color: axis } },
    axisTick: { lineStyle: { color: axis } },
    axisLabel: { color: muted },
    splitLine: { lineStyle: { color: grid, width: 1 } },
    nameTextStyle: { color: muted },
  };
  echarts.registerTheme(name, {
    color: PALETTE,
    backgroundColor: "transparent",
    textStyle: { color: text, fontFamily: font },
    legend: { textStyle: { color: text }, pageTextStyle: { color: muted } },
    tooltip: { textStyle: { fontFamily: font } },
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    visualMap: { textStyle: { color: muted } },
    bar: { label: { color: text } },
    line: { label: { color: text } },
    pie: { label: { color: text } },
  });
}

// next-themes memasang kelas `.dark` di <html>. Amati kelasnya langsung agar CSS var
// sudah berganti saat theme ECharts dibaca ulang (tanpa reload).
const subscribe = (cb: () => void) => {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => obs.disconnect();
};
const isDark = () => document.documentElement.classList.contains("dark");

/** Nama theme ECharts sesuai mode aktif. Ganti nama memicu echarts-for-react init ulang. */
export function useChartTheme(): string {
  const dark = useSyncExternalStore(subscribe, isDark, () => false);
  return useMemo(() => {
    const name = dark ? "studio-dark" : "studio-light";
    register(name);
    return name;
  }, [dark]);
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
