"use client";

import { Skeleton } from "@/components/ui/skeleton";
import type { KpiItem, RenderedItem } from "@/lib/types";

export interface KpiTileProps {
  item: KpiItem;
  rendered: RenderedItem | undefined;
}

const SENTIMENT_WORD = { positive: "membaik", negative: "memburuk", neutral: "tetap" } as const;
/** Badge delta. Sentimen dari backend sudah memperhitungkan arah nilai yang baik (setara `positiveIsGood`). */
const SENTIMENT_CLASS = {
  positive: "bg-success-soft text-success",
  negative: "bg-danger-soft text-destructive",
  neutral: "bg-muted text-muted-foreground",
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
    if (rendered?.status !== "error" && rendered?.status !== "invalid") {
      return (
        <div className="flex flex-1 flex-col justify-end gap-2 px-4 pb-4" aria-busy="true">
          <span className="sr-only">Menghitung KPI</span>
          <Skeleton className="h-7 w-2/3" />
          <Skeleton className="h-3.5 w-1/3" />
        </div>
      );
    }
    return (
      <div className="flex flex-1 items-end px-4 pb-4 text-xs text-muted-foreground" role="status">
        {rendered.status === "error"
          ? `KPI tidak dapat dihitung${rendered.error ? ` (${rendered.error.code})` : ""}.`
          : "KPI tidak valid."}
      </div>
    );
  }
  const arrow = kpi.delta === null || kpi.delta === 0 ? "" : kpi.delta > 0 ? "▲" : "▼";
  return (
    <div
      className="flex min-h-0 flex-1 flex-col justify-end gap-2 px-4 pb-4"
      role="group"
      aria-label={kpiAriaLabel(item, rendered)}
    >
      <span
        className="truncate text-display font-semibold tracking-tight"
        title={kpi.formatted.value ?? undefined}
        aria-hidden="true"
      >
        {kpi.formatted.value ?? "-"}
      </span>
      {(kpi.formatted.delta !== null || kpi.formatted.comparison !== null) && (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
          {kpi.formatted.delta !== null && (
            <span
              className={`inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 font-medium ${SENTIMENT_CLASS[kpi.sentiment]}`}
              data-sentiment={kpi.sentiment}
              aria-hidden="true"
            >
              {arrow} {kpi.formatted.delta}
              {kpi.formatted.delta_pct ? ` (${kpi.formatted.delta_pct})` : ""}
            </span>
          )}
          {kpi.formatted.comparison !== null && (
            <span className="truncate text-muted-foreground" aria-hidden="true">
              {item.spec.comparison_label || "Pembanding"}: {kpi.formatted.comparison}
            </span>
          )}
        </span>
      )}
    </div>
  );
}
