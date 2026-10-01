/**
 * Port data & helper panel Global_Filter (Req 22.1, 22.3, 24.x).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import { applyCommand, getDataset, isApiError, type RequestOptions } from "@/lib/api";
import type {
  ColumnInfo,
  Command,
  Dataset,
  DatasetDetail,
  PatchEvent,
  Scalar,
} from "@/lib/types";

export interface FilterClient {
  /** Simpan command (mis. `set_global_filters`) sebagai User_Edit_Event. */
  command(
    dashboardId: string,
    baseVersion: number,
    command: Command,
    opts?: RequestOptions,
  ): Promise<PatchEvent>;
  /** Ambil detail dataset (profil kolom → nilai top untuk filter kategorikal). */
  getDataset(
    ws: string,
    datasetId: string,
    opts?: RequestOptions,
  ): Promise<DatasetDetail>;
}

export const defaultFilterClient: FilterClient = {
  command: (dashboardId, baseVersion, command, opts) =>
    applyCommand(dashboardId, baseVersion, command, opts),
  getDataset: (ws, datasetId, opts) => getDataset(ws, datasetId, opts),
};

/** Kolom waktu (rentang tanggal); sisanya diperlakukan kategorikal (Req 22.1). */
export function isTimeColumn(column: ColumnInfo): boolean {
  return column.type === "date" || column.type === "datetime";
}

export interface DatasetColumn {
  dataset: Dataset;
  column: ColumnInfo;
}

/** Kolom waktu dari semua dataset (untuk filter rentang tanggal). */
export function timeColumns(datasets: readonly Dataset[]): DatasetColumn[] {
  const out: DatasetColumn[] = [];
  for (const dataset of datasets) {
    for (const column of dataset.schema) {
      if (isTimeColumn(column)) out.push({ dataset, column });
    }
  }
  return out;
}

/** Kolom non-waktu (kategorikal) dari semua dataset (Req 22.1). */
export function dimensionColumns(datasets: readonly Dataset[]): DatasetColumn[] {
  const out: DatasetColumn[] = [];
  for (const dataset of datasets) {
    for (const column of dataset.schema) {
      if (!isTimeColumn(column)) out.push({ dataset, column });
    }
  }
  return out;
}

/** Nilai unik teratas sebuah kolom dari profil dataset (untuk pilihan nilai). */
export function topValues(detail: DatasetDetail, column: string): Scalar[] {
  const profile = detail.column_profiles.find((p) => p.name === column);
  if (!profile) return [];
  return profile.top_values.map(([value]) => value);
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
