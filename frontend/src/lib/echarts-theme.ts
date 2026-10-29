import * as echarts from "echarts";
import { useMemo, useSyncExternalStore } from "react";

/**
 * Palet kategorikal: seri pertama biru aksen, lalu hue yang jelas berbeda
 * (amber, teal, violet, magenta) agar seri bersebelahan tidak tertukar.
 * Merah/hijau murni sengaja tidak dipakai: dicadangkan untuk makna naik/turun.
 * Nada tengah agar terbaca di latar terang dan gelap (warna dihitung sekali, bukan per tema).
 */
export const PALETTE = [
  "#4a7ee6", "#e09b3d", "#2b9c8f", "#8b6fd6", "#cf5f8f",
  "#5ab4e8", "#8a94a6", "#a39a3a", "#b5793f", "#4f5fb3",
];

/** Warna semantik langkah waterfall, terpisah dari palet kategorikal. */
export const STEP_PALETTE = { total: PALETTE[0], down: "#d0554b", up: "#2f9e5f" } as const;

const FALLBACK = {
  text: "#16191f",
  muted: "#5b6472",
  subtle: "#6b6c75",
  grid: "rgb(0 0 0 / 0.07)",
  axis: "rgb(0 0 0 / 0.12)",
  popover: "#ffffff",
  shadow: "0 4px 16px rgb(0 0 0 / 0.1)",
  font: "sans-serif",
};

/** Baca token warna dari CSS var di `<html>` (sumber yang sama dengan UI). */
function readTokens() {
  if (typeof document === "undefined") return FALLBACK;
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fb: string) => css.getPropertyValue(name).trim() || fb;
  return {
    text: v("--text", FALLBACK.text),
    muted: v("--text-muted", FALLBACK.muted),
    subtle: v("--text-subtle", FALLBACK.subtle),
    grid: v("--border", FALLBACK.grid),
    axis: v("--border-strong", FALLBACK.axis),
    popover: v("--surface-3", FALLBACK.popover),
    shadow: v("--shadow-md", FALLBACK.shadow),
    font: getComputedStyle(document.body).fontFamily || FALLBACK.font,
  };
}

function register(name: string) {
  const { text, muted, subtle, grid, axis, popover, shadow, font } = readTokens();
  const axisCommon = {
    axisLine: { show: false, lineStyle: { color: axis } },
    axisTick: { show: false },
    axisLabel: { color: subtle, fontSize: 11 },
    splitLine: { lineStyle: { color: grid, width: 1 } },
    nameTextStyle: { color: subtle, fontSize: 11 },
  };
  echarts.registerTheme(name, {
    color: PALETTE,
    backgroundColor: "transparent",
    textStyle: { color: text, fontFamily: font },
    legend: { textStyle: { color: muted, fontSize: 11 }, pageTextStyle: { color: muted } },
    tooltip: {
      backgroundColor: popover,
      borderColor: axis,
      borderWidth: 1,
      padding: [8, 10],
      textStyle: { color: text, fontFamily: font, fontSize: 12 },
      extraCssText: `box-shadow: ${shadow}; border-radius: 8px; font-variant-numeric: tabular-nums;`,
    },
    categoryAxis: axisCommon,
    valueAxis: axisCommon,
    visualMap: { textStyle: { color: muted, fontSize: 11 } },
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
