/**
 * Port data & helper tampilan untuk komponen relasi (Req 7.2–7.5, 7.8).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import {
  confirmRelation,
  deleteRelation,
  detectRelations,
  isApiError,
  listRelations,
  rejectRelation,
  type RequestOptions,
} from "@/lib/api";
import type { Relation, RelationCardinality, RelationStatus } from "@/lib/types";

export interface RelationClient {
  /** Semua relasi Workspace kecuali yang berstatus `deleted`. */
  list(workspaceId: string, opts?: RequestOptions): Promise<Relation[]>;
  /** Jalankan ulang deteksi; mengembalikan seluruh kandidat Workspace. */
  detect(workspaceId: string, opts?: RequestOptions): Promise<Relation[]>;
  /** Simpan sebagai Confirmed_Relation (Req 7.4). */
  confirm(workspaceId: string, relationId: string, opts?: RequestOptions): Promise<Relation>;
  /** Tandai ditolak agar tidak diusulkan lagi (Req 7.5). */
  reject(workspaceId: string, relationId: string, opts?: RequestOptions): Promise<Relation>;
  /** Hapus relasi; item Dashboard dependen menjadi `invalid` (Req 7.8). */
  remove(workspaceId: string, relationId: string, opts?: RequestOptions): Promise<void>;
}

export const defaultRelationClient: RelationClient = {
  list: (ws, opts) => listRelations(ws, undefined, opts),
  detect: detectRelations,
  confirm: confirmRelation,
  reject: rejectRelation,
  remove: deleteRelation,
};

/** dataset_id → nama tabel SQL; fallback bila `from_table`/`to_table` tidak ada. */
export type TableNames = Readonly<Record<string, string>>;

export const CARDINALITY_LABELS: Record<RelationCardinality, string> = {
  one_to_one: "one-to-one (1:1)",
  one_to_many: "one-to-many (1:N)",
  many_to_many: "many-to-many (N:N)",
};

export const STATUS_LABELS: Record<RelationStatus, string> = {
  candidate: "Kandidat",
  confirmed: "Terkonfirmasi",
  rejected: "Ditolak",
  deleted: "Dihapus",
};

export function formatCardinality(cardinality: RelationCardinality): string {
  return CARDINALITY_LABELS[cardinality] ?? cardinality;
}

const percentFormat = new Intl.NumberFormat("id-ID", {
  style: "percent",
  maximumFractionDigits: 1,
});

/** `overlap_pct` (0–100) → "85,5%" (id-ID). */
export function formatOverlap(overlapPct: number): string {
  return Number.isFinite(overlapPct) ? percentFormat.format(overlapPct / 100) : "–";
}

/** "tabel.kolom" untuk sisi `from`/`to` relasi. */
export function relationSide(
  relation: Relation,
  side: "from" | "to",
  tableNames: TableNames = {},
): string {
  const table =
    side === "from"
      ? (relation.from_table ?? tableNames[relation.from_dataset_id] ?? relation.from_dataset_id)
      : (relation.to_table ?? tableNames[relation.to_dataset_id] ?? relation.to_dataset_id);
  const column = side === "from" ? relation.from_column : relation.to_column;
  return `${table}.${column}`;
}

/** Judul relasi, mis. "customers.id → orders.customer_id" (sisi "one" di kiri). */
export function relationTitle(relation: Relation, tableNames?: TableNames): string {
  return `${relationSide(relation, "from", tableNames)} → ${relationSide(relation, "to", tableNames)}`;
}

/**
 * Terapkan relasi terbaru (hasil aksi atau event `relation.updated`) ke daftar:
 * ganti berdasarkan id atau tambahkan di akhir; relasi `deleted` dikeluarkan.
 */
export function upsertRelation(list: readonly Relation[], relation: Relation): Relation[] {
  if (relation.status === "deleted") return list.filter((r) => r.id !== relation.id);
  const idx = list.findIndex((r) => r.id === relation.id);
  if (idx === -1) return [...list, relation];
  const next = list.slice();
  next[idx] = relation;
  return next;
}

/**
 * Gabungkan hasil `detect` (seluruh kandidat Workspace) ke daftar: kandidat
 * lama diganti kandidat hasil deteksi, relasi berstatus lain dipertahankan.
 */
export function mergeDetected(list: readonly Relation[], candidates: readonly Relation[]): Relation[] {
  const ids = new Set(candidates.map((r) => r.id));
  const kept = list.filter((r) => r.status !== "candidate" && !ids.has(r.id));
  return [...kept, ...candidates.filter((r) => r.status !== "deleted")];
}

export type ListedStatus = Exclude<RelationStatus, "deleted">;

export function groupByStatus(list: readonly Relation[]): Record<ListedStatus, Relation[]> {
  const groups: Record<ListedStatus, Relation[]> = { candidate: [], confirmed: [], rejected: [] };
  for (const r of list) if (r.status !== "deleted") groups[r.status].push(r);
  return groups;
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}
