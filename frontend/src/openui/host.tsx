"use client";

/**
 * Konteks host canvas OpenUI: state yang dimiliki aplikasi, bukan program (D7).
 *
 * Komponen library membaca statement tersimpan (status, lampiran insight,
 * rujukan Query), filter aktif, dan memanggil aksi host (Cross_Filter, slicer,
 * edit layout) lewat konteks ini.
 */
import { createContext, useContext } from "react";
import type { CanvasStatement, Dataset, FilterSet, ItemStatus, Predicate, Scalar } from "@/lib/types";

export interface CanvasHost {
  dashboardId: string;
  version: number;
  statements: Readonly<Record<string, CanvasStatement>>;
  itemStatus: Readonly<Record<string, ItemStatus>>;
  globalFilters: FilterSet;
  crossFilters: Predicate[];
  datasets: readonly Dataset[];
  datasetVersions: Readonly<Record<string, number>>;
  /** Klik elemen chart: toggle Cross_Filter kolom (Req 24.1). */
  toggleCrossFilter(column: string, value: Scalar): void;
  /** Slicer: ganti Global_Filter satu kolom (`null` = hapus). */
  setColumnFilter(column: string, next: Predicate | null): void;
  /** Kirim patch OpenUI Lang (edit manual, `source = user`). */
  applyPatch(patch: string): void;
  /** Minta konfirmasi lalu hapus tile. */
  requestRemove(name: string): void;
  verify?(name: string): void;
  verified: ReadonlySet<string>;
  /** Tile yang sedang diseret (drag reorder antar container). */
  dragging: string | null;
  setDragging(name: string | null): void;
  /** Edit layout dimatikan saat export/tes read-only. */
  editable: boolean;
}

const CanvasHostContext = createContext<CanvasHost | null>(null);

export const CanvasHostProvider = CanvasHostContext.Provider;

export function useCanvasHost(): CanvasHost {
  const host = useContext(CanvasHostContext);
  if (!host) throw new Error("Komponen canvas OpenUI wajib dirender di dalam CanvasHostProvider.");
  return host;
}
