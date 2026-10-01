/**
 * Port data & helper Canvas_Editor (Req 22.1–22.3, 24.x, 26.2).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import {
  applyCommand,
  isApiError,
  renderDashboard,
  verifyDashboardItem,
  type RequestOptions,
} from "@/lib/api";
import type {
  Command,
  DashboardItem,
  Dataset,
  LayoutChange,
  LayoutRect,
  PatchEvent,
  Predicate,
  RenderResponse,
  Scalar,
} from "@/lib/types";

export interface CanvasClient {
  /** Render ulang item dengan Cross_Filter aktif (Req 22.3). */
  render(
    dashboardId: string,
    itemIds: string[] | undefined,
    crossFilters: Predicate[],
    opts?: RequestOptions,
  ): Promise<RenderResponse>;
  /** Simpan perintah (mis. tata letak / hapus item) sebagai User_Edit_Event. */
  command(
    dashboardId: string,
    baseVersion: number,
    command: Command,
    opts?: RequestOptions,
  ): Promise<PatchEvent>;
  /** "Tandai terverifikasi": query item menjadi Verified_Query (Req 34.2). Opsional. */
  verify?(dashboardId: string, itemId: string, opts?: RequestOptions): Promise<void>;
}

export const defaultCanvasClient: CanvasClient = {
  render: (dashboardId, itemIds, crossFilters, opts) =>
    renderDashboard(dashboardId, { item_ids: itemIds, cross_filters: crossFilters }, opts),
  command: (dashboardId, baseVersion, command, opts) =>
    applyCommand(dashboardId, baseVersion, command, opts),
  verify: (dashboardId, itemId, opts) => verifyDashboardItem(dashboardId, itemId, opts).then(() => undefined),
};

export const STATUS_LABELS = {
  ok: "OK",
  invalid: "Tidak valid",
  stale: "Data berubah",
  error: "Error",
} as const;

export function isChart(item: DashboardItem): item is Extract<DashboardItem, { kind: "chart" }> {
  return item.kind === "chart";
}

/** Urutan render item: baris (y) lalu kolom (x) lalu id (deterministik). */
export function sortedItemIds(layout: Readonly<Record<string, LayoutRect>>): string[] {
  return Object.keys(layout).sort((a, b) => {
    const ra = layout[a];
    const rb = layout[b];
    return ra.y - rb.y || ra.x - rb.x || a.localeCompare(b);
  });
}

/** LayoutChange untuk pindah/ubah ukuran satu item. */
export function layoutChange(
  id: string,
  before: LayoutRect,
  after: LayoutRect,
): LayoutChange {
  return { id, before, after };
}

/**
 * Nilai elemen chart yang diklik (kategori bar/pie/line: `params.name`,
 * titik scatter: elemen pertama `params.value`) untuk Cross_Filter (Req 24.1).
 */
export function chartElementValue(params: {
  name?: unknown;
  value?: unknown;
}): Scalar | null {
  if (typeof params.name === "string" && params.name !== "") return params.name;
  if (typeof params.name === "number") return params.name;
  const v = params.value;
  if (Array.isArray(v) && v.length > 0) {
    const first = v[0];
    if (typeof first === "string" || typeof first === "number" || typeof first === "boolean") {
      return first;
    }
    return null;
  }
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return v;
  return null;
}

/**
 * Nama tabel sumber untuk `cross_filter_column`: dataset yang memiliki kolom
 * bernama sama (kesepakatan sederhana FE; lineage penuh ada di Backend).
 */
export function resolveCrossFilterTable(
  datasets: readonly Dataset[],
  column: string,
): string | null {
  for (const dataset of datasets) {
    if (dataset.schema.some((c) => c.name === column)) return dataset.table_name;
  }
  return null;
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
