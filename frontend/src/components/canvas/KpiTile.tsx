"use client";

import type { KpiItem, RenderedItem } from "@/lib/types";

export interface KpiTileProps {
  item: KpiItem;
  rendered: RenderedItem | undefined;
}

const SENTIMENT_WORD = { positive: "membaik", negative: "memburuk", neutral: "tetap" } as const;
const SENTIMENT_CLASS = {
  positive: "text-success",
  negative: "text-destructive",
  neutral: "text-muted-foreground",
} as const;

/** Teks aksesibel: nilai, pembanding, dan arah perubahan (Req 38.7). */
export function kpiAriaLabel(item: KpiItem, rendered: RenderedItem | undefined): string {
  const kpi = rendered?.kpi;
  if (!kpi || kpi.formatted.value === null) return `${item.title}: belum tersedia`;
  const parts = [`${item.title}: ${kpi.formatted.value}`];
  if (kpi.formatted.delta !== null) {
    const label = item.spec.comparison_label || "dibanding pembanding";
    const pct = kpi.formatted.delta_pct ? ` (${kpi.formatted.delta_pct})` : "";
    parts.push(`${kpi.formatted.delta}${pct} ${label}, ${SENTIMENT_WORD[kpi.sentiment]}`);
  }
  return parts.join(", ");
}

/** KPI_Card: angka besar + delta berwarna sesuai arah nilai yang baik (Req 38.7). */
export function KpiTile({ item, rendered }: KpiTileProps) {
  const kpi = rendered?.kpi;
  if (!kpi) {
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-muted-foreground">
        {rendered?.status === "error"
          ? `KPI tidak dapat dihitung${rendered.error ? ` (${rendered.error.code})` : ""}.`
          : rendered?.status === "invalid"
            ? "KPI tidak valid."
            : "Menghitung KPI"}
      </div>
    );
  }
  const arrow = kpi.delta === null || kpi.delta === 0 ? "" : kpi.delta > 0 ? "▲" : "▼";
  return (
    <div
      className="flex min-h-0 flex-1 flex-col justify-center gap-0.5 px-3 pt-1 pb-2"
      role="group"
      aria-label={kpiAriaLabel(item, rendered)}
    >
      <span className="truncate text-[1.75rem] leading-tight font-bold text-primary tabular-nums" aria-hidden="true">
        {kpi.formatted.value ?? "-"}
      </span>
      {kpi.formatted.delta !== null && (
        <span
          className={`text-[0.8125rem] font-semibold tabular-nums ${SENTIMENT_CLASS[kpi.sentiment]}`}
          data-sentiment={kpi.sentiment}
          aria-hidden="true"
        >
          {arrow} {kpi.formatted.delta}
          {kpi.formatted.delta_pct ? ` (${kpi.formatted.delta_pct})` : ""}
        </span>
      )}
      {kpi.formatted.comparison !== null && (
        <span className="text-[0.8125rem] text-muted-foreground tabular-nums" aria-hidden="true">
          {item.spec.comparison_label || "Pembanding"}: {kpi.formatted.comparison}
        </span>
      )}
    </div>
  );
}
