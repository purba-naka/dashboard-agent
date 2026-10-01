/**
 * Port data untuk komponen Insight (Req 14.7, 15.1, 15.3, 26.2).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import { getQuery, isApiError, refreshInsight, type RequestOptions } from "@/lib/api";
import { filtersDiffer } from "@/lib/filters";
import type { FilterSet, InsightItem, PatchEvent, QueryDetail } from "@/lib/types";

export interface InsightClient {
  /** SQL sumber + tabel bukti insight (modal detail). */
  query(queryId: string, opts?: RequestOptions): Promise<QueryDetail>;
  /** Hitung ulang hasil numerik dengan Global_Filter aktif (Req 15.1). */
  refresh(
    dashboardId: string,
    itemId: string,
    baseVersion: number,
    opts?: RequestOptions,
  ): Promise<PatchEvent>;
}

export const defaultInsightClient: InsightClient = {
  query: getQuery,
  refresh: (dashboardId, itemId, baseVersion, opts) =>
    refreshInsight(dashboardId, itemId, baseVersion, opts),
};

export const INSIGHT_TYPE_LABELS: Record<InsightItem["insight_type"], string> = {
  trend: "Tren",
  anomaly: "Anomali",
  comparison: "Perbandingan",
  top_bottom_contributors: "Kontributor teratas/terbawah",
  cross_dataset_correlation: "Korelasi lintas-dataset",
};

export function insightTypeLabel(type: InsightItem["insight_type"]): string {
  return INSIGHT_TYPE_LABELS[type] ?? type;
}

/**
 * True bila insight dihitung dengan filter berbeda dari Global_Filter aktif
 * (Req 15.3): badge "dihitung dengan filter berbeda".
 */
export function computedWithDifferentFilters(
  insight: InsightItem,
  activeFilters: FilterSet,
): boolean {
  return filtersDiffer(insight.filters_snapshot, activeFilters);
}

/** True bila `data_version` salah satu Dataset sumber lebih baru dari snapshot insight. */
export function isStale(
  insight: InsightItem,
  datasetVersions: Readonly<Record<string, number>>,
): boolean {
  for (const [id, version] of Object.entries(insight.dataset_versions)) {
    const current = datasetVersions[id];
    if (current !== undefined && current > version) return true;
  }
  return false;
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
