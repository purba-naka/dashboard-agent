/**
 * Port data & helper panel Dataset: upload + progres gabungan, pemilih sheet,
 * detail (skema/profil/kualitas/pemetaan), toggle privasi, dan re-upload
 * (Req 2.3, 2.4, 3.2, 3.3, 5.3, 6.2, 26.3, 27.4).
 *
 * Komponen menerima `client` lewat props sehingga dapat dirender dengan
 * implementasi in-memory di test; default-nya memakai klien REST `lib/api`.
 */

import {
  getDataset,
  getJob,
  isApiError,
  isSchemaMismatch,
  reuploadDataset,
  selectSheets,
  updateDataset,
  uploadFile,
  type RequestOptions,
  type UploadOptions,
} from "@/lib/api";
import type {
  Dataset,
  DatasetDetail,
  JobStatus,
  SchemaMismatchDetails,
  SelectSheetsResponse,
  UpdateDatasetRequest,
  UploadResponse,
} from "@/lib/types";

export interface DatasetClient {
  upload(
    ws: string,
    file: globalThis.File,
    opts?: UploadOptions,
  ): Promise<UploadResponse>;
  selectSheets(
    ws: string,
    uploadId: string,
    sheets: string[],
    opts?: RequestOptions,
  ): Promise<SelectSheetsResponse>;
  getJob(jobId: string, opts?: RequestOptions): Promise<JobStatus>;
  getDataset(ws: string, datasetId: string, opts?: RequestOptions): Promise<DatasetDetail>;
  updateDataset(
    ws: string,
    datasetId: string,
    changes: UpdateDatasetRequest,
    opts?: RequestOptions,
  ): Promise<Dataset>;
  reupload(
    ws: string,
    datasetId: string,
    file: globalThis.File,
    opts?: UploadOptions,
  ): Promise<{ job_id: string }>;
}

export const defaultDatasetClient: DatasetClient = {
  upload: (ws: string, file: globalThis.File, opts?: UploadOptions) =>
    uploadFile(ws, file, opts),
  selectSheets: (ws, uploadId, sheets, opts) => selectSheets(ws, uploadId, sheets, opts),
  getJob: (jobId, opts) => getJob(jobId, opts),
  getDataset: (ws, datasetId, opts) => getDataset(ws, datasetId, opts),
  updateDataset: (ws, datasetId, changes, opts) => updateDataset(ws, datasetId, changes, opts),
  reupload: (ws, datasetId, file, opts) => reuploadDataset(ws, datasetId, file, opts),
};

// ---------------------------------------------------------------------------
// Progres gabungan (Req 5.3)
// ---------------------------------------------------------------------------

/**
 * Progres gabungan: upload XHR 0–50%, konversi `job.progress` (0–1) 50–100%.
 * `uploadFraction` null (total tak diketahui) dianggap setengah jalan.
 */
export function combinedProgress(
  uploadFraction: number | null,
  jobProgress: number | null,
): number {
  const upload = uploadFraction ?? 0.5;
  const job = jobProgress ?? 0;
  return Math.min(1, 0.5 * Math.min(1, Math.max(0, upload)) + 0.5 * Math.min(1, Math.max(0, job)));
}

/** Persentase bulat untuk label `aria-valuenow`. */
export function progressPercent(fraction: number): number {
  return Math.round(Math.min(1, Math.max(0, fraction)) * 100);
}

export interface JobPollOptions {
  /** Interval polling (ms); default 1000 (≤ 2 dtk, Req 5.3). */
  intervalMs?: number;
  /** Batas waktu total (ms); default 10 menit. */
  timeoutMs?: number;
  signal?: AbortSignal;
}

export interface JobOutcome {
  status: "done" | "failed" | "timeout";
  datasetId?: string;
  error?: { code: string; message: string };
}

/**
 * Poll `getJob` sampai selesai; `onProgress` menerima progres job (0–1).
 * Selesai = `done`/`failed`; timeout mengembalikan status `"timeout"`.
 */
export async function pollJob(
  client: Pick<DatasetClient, "getJob">,
  jobId: string,
  onProgress?: (jobProgress: number) => void,
  opts: JobPollOptions = {},
): Promise<JobOutcome> {
  const intervalMs = opts.intervalMs ?? 1000;
  const deadline = Date.now() + (opts.timeoutMs ?? 10 * 60 * 1000);
  for (;;) {
    if (opts.signal?.aborted) return { status: "timeout" };
    const job = await client.getJob(jobId);
    onProgress?.(job.progress);
    if (job.status === "done") {
      return { status: "done", datasetId: job.dataset_id };
    }
    if (job.status === "failed") {
      return {
        status: "failed",
        error: job.error
          ? { code: job.error.code, message: job.error.message }
          : { code: "JOB_FAILED", message: "Konversi data gagal." },
      };
    }
    if (Date.now() > deadline) return { status: "timeout" };
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// ---------------------------------------------------------------------------
// Tampilan error & diff skema
// ---------------------------------------------------------------------------

/** Pesan error yang aman ditampilkan, menyertakan penyebab + baris bila ada. */
export function errorMessage(err: unknown): string {
  if (isApiError(err)) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return "Terjadi kesalahan yang tidak diketahui.";
}

/** Detail `SCHEMA_MISMATCH` dari error re-upload, bila ada (Req 26.3). */
export function schemaMismatch(err: unknown): SchemaMismatchDetails | null {
  return isSchemaMismatch(err);
}

/** Baris ringkas diff skema untuk ditampilkan (Req 26.3). */
export function describeSchemaDiff(diff: SchemaMismatchDetails): string[] {
  const lines: string[] = [];
  if (diff.missing.length > 0) {
    lines.push(`Kolom hilang: ${diff.missing.join(", ")}`);
  }
  if (diff.added.length > 0) {
    lines.push(`Kolom baru: ${diff.added.join(", ")}`);
  }
  for (const c of diff.changed) {
    lines.push(`Kolom berubah: ${c.name} (${c.old_type} → ${c.new_type})`);
  }
  return lines;
}

/** Label tipe kolom untuk tampilan skema. */
export function typeLabel(type: string): string {
  const map: Record<string, string> = {
    integer: "integer",
    float: "float",
    string: "string",
    boolean: "boolean",
    date: "tanggal",
    datetime: "tanggal-waktu",
  };
  return map[type] ?? type;
}

/** Format persentase 0–1 → "12,3%". */
export function formatPct(fraction: number): string {
  return `${(fraction * 100).toFixed(1).replace(".", ",")}%`;
}

/** Pemetaan kolom yang benar-benar berubah nama (Req 2.5). */
export function renamedColumns(mapping: ReadonlyArray<{ original: string; normalized: string }>) {
  return mapping.filter((m) => m.original !== m.normalized);
}
