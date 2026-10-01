"use client";

import type { KpiItem, RenderedItem } from "@/lib/types";
import styles from "./canvas.module.css";

export interface KpiTileProps {
  item: KpiItem;
  rendered: RenderedItem | undefined;
}

const SENTIMENT_WORD = { positive: "membaik", negative: "memburuk", neutral: "tetap" } as const;

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
      <div className={styles.placeholder}>
        {rendered?.status === "error"
          ? `KPI tidak dapat dihitung${rendered.error ? ` (${rendered.error.code})` : ""}.`
          : rendered?.status === "invalid"
            ? "KPI tidak valid."
            : "Menghitung KPI…"}
      </div>
    );
  }
  const arrow = kpi.delta === null || kpi.delta === 0 ? "" : kpi.delta > 0 ? "▲" : "▼";
  return (
    <div className={styles.kpi} role="group" aria-label={kpiAriaLabel(item, rendered)}>
      <span className={styles.kpiValue} aria-hidden="true">
        {kpi.formatted.value ?? "—"}
      </span>
      {kpi.formatted.delta !== null && (
        <span className={`${styles.kpiDelta} ${styles[`kpi_${kpi.sentiment}`]}`} aria-hidden="true">
          {arrow} {kpi.formatted.delta}
          {kpi.formatted.delta_pct ? ` (${kpi.formatted.delta_pct})` : ""}
        </span>
      )}
      {kpi.formatted.comparison !== null && (
        <span className={styles.muted} aria-hidden="true">
          {item.spec.comparison_label || "Pembanding"}: {kpi.formatted.comparison}
        </span>
      )}
    </div>
  );
}
