/**
 * Port data untuk komponen Workspace (Req 1.1–1.4).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import {
  createWorkspace,
  deleteWorkspace,
  getWorkspace,
  isApiError,
  listWorkspaces,
  renameWorkspace,
  type RequestOptions,
} from "@/lib/api";
import type { Workspace, WorkspaceDetail } from "@/lib/types";

export interface WorkspaceClient {
  list(opts?: RequestOptions): Promise<Workspace[]>;
  create(name: string, opts?: RequestOptions): Promise<Workspace>;
  get(id: string, opts?: RequestOptions): Promise<WorkspaceDetail>;
  rename(id: string, name: string, opts?: RequestOptions): Promise<Workspace>;
  /** `confirmName` harus sama persis dengan nama Workspace (Req 1.4). */
  remove(id: string, confirmName: string, opts?: RequestOptions): Promise<void>;
}

export const defaultWorkspaceClient: WorkspaceClient = {
  list: listWorkspaces,
  create: createWorkspace,
  get: getWorkspace,
  rename: renameWorkspace,
  remove: deleteWorkspace,
};

/** Selaras dengan `MAX_NAME_LENGTH` backend (`studio/api/schemas.py`). */
export const MAX_WORKSPACE_NAME_LENGTH = 200;

/**
 * Validasi nama sebelum dikirim (backend men-trim dan menolak nama kosong).
 * Mengembalikan pesan error, atau `null` bila valid.
 */
export function validateWorkspaceName(raw: string): string | null {
  const name = raw.trim();
  if (!name) return "Nama Workspace tidak boleh kosong.";
  if (name.length > MAX_WORKSPACE_NAME_LENGTH) {
    return `Nama Workspace maksimal ${MAX_WORKSPACE_NAME_LENGTH} karakter.`;
  }
  return null;
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}

const dateTimeFormat = new Intl.DateTimeFormat("id-ID", {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Format timestamp ISO untuk tampilan; string mentah bila tidak valid. */
export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateTimeFormat.format(d);
}
