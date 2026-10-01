"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { isXlsxUpload } from "@/lib/api";
import type { Dataset, DatasetDetail, SheetInfo } from "@/lib/types";
import {
  combinedProgress,
  defaultDatasetClient,
  describeSchemaDiff,
  errorMessage,
  formatPct,
  pollJob,
  progressPercent,
  renamedColumns,
  schemaMismatch,
  typeLabel,
  type DatasetClient,
} from "./client";
import styles from "./datasets.module.css";

export interface DatasetsPanelProps {
  workspaceId: string;
  datasets: readonly Dataset[];
  /** Daftar dataset berubah (upload/re-upload selesai, toggle privasi). */
  onDatasetsChanged: () => void;
  client?: DatasetClient;
}

interface SheetJob {
  sheet: string;
  progress: number;
  state: "running" | "done" | "failed";
  error?: string;
}

/**
 * Panel Dataset: upload dengan progres gabungan (XHR 0–50% + job 50–100%,
 * Req 5.3), pemilih sheet XLSX (Req 3.2), tampilan error (format/parse/baris,
 * sheet kosong, Req 2.3/2.4/3.3), detail dataset (skema, profil, kualitas,
 * pemetaan kolom, Req 6.2/2.5), toggle privasi (Req 27.4), dan re-upload
 * dengan diff skema (Req 26.3).
 */
export function DatasetsPanel({
  workspaceId,
  datasets,
  onDatasetsChanged,
  client = defaultDatasetClient,
}: DatasetsPanelProps) {
  // --- Upload baru -------------------------------------------------------
  const [file, setFile] = useState<globalThis.File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [picker, setPicker] = useState<{ uploadId: string; sheets: SheetInfo[] } | null>(null);
  const [chosenSheets, setChosenSheets] = useState<string[]>([]);
  const [sheetJobs, setSheetJobs] = useState<SheetJob[]>([]);

  // --- Detail & re-upload ------------------------------------------------
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<{ id: string; detail: DatasetDetail } | null>(null);
  // Detail dataset lain (pilihan berganti/ditutup) dianggap belum dimuat.
  const detail = loaded && loaded.id === selectedId ? loaded.detail : null;
  const [detailError, setDetailError] = useState<string | null>(null);
  const [reuploadProgress, setReuploadProgress] = useState<number | null>(null);
  const [mismatch, setMismatch] = useState<string[] | null>(null);
  const [privacyBusy, setPrivacyBusy] = useState(false);
  const detailFileRef = useRef<HTMLInputElement | null>(null);
  const uploadFileRef = useRef<HTMLInputElement | null>(null);

  const refreshDetail = useCallback(
    (datasetId: string) => {
      client
        .getDataset(workspaceId, datasetId)
        .then((res) => {
          setLoaded({ id: datasetId, detail: res });
          setDetailError(null);
        })
        .catch((err: unknown) => setDetailError(errorMessage(err)));
    },
    [client, workspaceId],
  );

  useEffect(() => {
    if (selectedId) refreshDetail(selectedId);
  }, [selectedId, refreshDetail]);

  /** Upload file baru: CSV → poll job; XLSX → pemilih sheet. */
  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    setUploadProgress(0);
    setUploadError(null);
    setPicker(null);
    setSheetJobs([]);
    try {
      const res = await client.upload(workspaceId, file, {
        onProgress: (p) => setUploadProgress(combinedProgress(p.fraction, 0)),
      });
      if (isXlsxUpload(res)) {
        setPicker({ uploadId: res.upload_id, sheets: res.sheets });
        setChosenSheets(res.sheets.map((s) => s.name));
        return; // tunggu pemilihan sheet
      }
      const outcome = await pollJob(client, res.job_id, (jp) =>
        setUploadProgress(combinedProgress(1, jp)),
      );
      if (outcome.status === "done") {
        setUploadProgress(1);
        onDatasetsChanged();
      } else if (outcome.status === "failed") {
        setUploadError(outcome.error?.message ?? "Konversi data gagal.");
      } else {
        setUploadError("Konversi melewati batas waktu.");
      }
    } catch (err: unknown) {
      setUploadError(errorMessage(err));
    } finally {
      setUploading(false);
      if (uploadFileRef.current) uploadFileRef.current.value = "";
      setFile(null);
    }
  }

  /** Konversi sheet XLSX terpilih; satu job per sheet (Req 3.2). */
  async function handleSelectSheets() {
    if (!picker || chosenSheets.length === 0) return;
    setSheetJobs(chosenSheets.map((sheet) => ({ sheet, progress: 0, state: "running" })));
    try {
      const res = await client.selectSheets(workspaceId, picker.uploadId, chosenSheets);
      const outcomes = await Promise.all(
        res.jobs.map(async ({ sheet, job_id }) => {
          const outcome = await pollJob(client, job_id, (jp) =>
            setSheetJobs((prev) =>
              prev.map((j) => (j.sheet === sheet ? { ...j, progress: jp } : j)),
            ),
          );
          return { sheet, outcome };
        }),
      );
      const failed = outcomes.filter((o) => o.outcome.status !== "done");
      for (const f of failed) {
        setSheetJobs((prev) =>
          prev.map((j) =>
            j.sheet === f.sheet
              ? { ...j, state: "failed", error: f.outcome.error?.message ?? "Gagal." }
              : j,
          ),
        );
      }
      if (failed.length === 0) {
        setPicker(null);
        setSheetJobs([]);
      }
      onDatasetsChanged();
    } catch (err: unknown) {
      setUploadError(errorMessage(err));
    }
  }

  /** Toggle privasi "jangan kirim sample rows" (Req 27.4). */
  async function handlePrivacy(dataset: Dataset, noSamples: boolean) {
    setPrivacyBusy(true);
    try {
      await client.updateDataset(workspaceId, dataset.id, {
        privacy_no_samples: noSamples,
      });
      onDatasetsChanged();
      refreshDetail(dataset.id);
    } catch (err: unknown) {
      setDetailError(errorMessage(err));
    } finally {
      setPrivacyBusy(false);
    }
  }

  /** Re-upload dataset; skema beda → tampilkan diff (Req 26.3). */
  async function handleReupload(dataset: Dataset, file: globalThis.File) {
    setMismatch(null);
    setDetailError(null);
    setReuploadProgress(0);
    try {
      const res = await client.reupload(workspaceId, dataset.id, file, {
        onProgress: (p) => setReuploadProgress(combinedProgress(p.fraction, 0)),
      });
      const outcome = await pollJob(client, res.job_id, (jp) =>
        setReuploadProgress(combinedProgress(1, jp)),
      );
      if (outcome.status === "failed") {
        setDetailError(outcome.error?.message ?? "Konversi data gagal.");
      } else if (outcome.status === "timeout") {
        setDetailError("Konversi melewati batas waktu.");
      }
      onDatasetsChanged();
      refreshDetail(dataset.id);
    } catch (err: unknown) {
      const diff = schemaMismatch(err);
      if (diff) {
        setMismatch(describeSchemaDiff(diff));
      } else {
        setDetailError(errorMessage(err));
      }
    } finally {
      setReuploadProgress(null);
      if (detailFileRef.current) detailFileRef.current.value = "";
    }
  }

  const selected = datasets.find((d) => d.id === selectedId) ?? detail?.dataset ?? null;

  return (
    <div className={styles.panel}>
      <h2 className={styles.title}>Dataset</h2>

      {/* Upload baru */}
      <div className={styles.upload}>
        <input
          ref={uploadFileRef}
          type="file"
          aria-label="File dataset"
          accept=".csv,.xlsx"
          disabled={uploading}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button
          type="button"
          className={styles.button}
          disabled={!file || uploading}
          onClick={handleUpload}
        >
          {uploading ? "Mengunggah…" : "Unggah"}
        </button>
        {uploading && (
          <div
            role="progressbar"
            aria-valuenow={progressPercent(uploadProgress)}
            aria-valuemin={0}
            aria-valuemax={100}
            className={styles.progress}
          >
            <div className={styles.progressBar} style={{ width: `${progressPercent(uploadProgress)}%` }} />
            <span className={styles.progressLabel}>{progressPercent(uploadProgress)}%</span>
          </div>
        )}
        {uploadError && (
          <p role="alert" className={styles.error}>
            {uploadError}
          </p>
        )}
      </div>

      {/* Pemilih sheet XLSX */}
      {picker && (
        <fieldset className={styles.picker} aria-label="Pemilih sheet">
          <legend>Pilih sheet XLSX</legend>
          {picker.sheets.map((s) => (
            <label key={s.name} className={styles.value}>
              <input
                type="checkbox"
                checked={chosenSheets.includes(s.name)}
                onChange={(e) =>
                  setChosenSheets((prev) =>
                    e.target.checked ? [...prev, s.name] : prev.filter((x) => x !== s.name),
                  )
                }
              />
              {s.name}
              {s.rows_hint !== null && <span className={styles.muted}> · {s.rows_hint} baris</span>}
            </label>
          ))}
          <button
            type="button"
            className={styles.button}
            disabled={chosenSheets.length === 0}
            onClick={handleSelectSheets}
          >
            Konversi sheet terpilih
          </button>
          {sheetJobs.map((j) => (
            <p key={j.sheet} className={j.state === "failed" ? styles.error : styles.muted}>
              {j.sheet}: {j.state === "failed" ? j.error : `${progressPercent(j.progress)}%`}
            </p>
          ))}
        </fieldset>
      )}

      {/* Daftar dataset */}
      {datasets.length === 0 ? (
        <p className={styles.muted}>Belum ada dataset — unggah CSV atau XLSX.</p>
      ) : (
        <ul className={styles.list}>
          {datasets.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                className={`${styles.row} ${d.id === selectedId ? styles.rowActive : ""}`}
                aria-pressed={d.id === selectedId}
                onClick={() => setSelectedId(d.id === selectedId ? null : d.id)}
              >
                <span className={styles.rowName}>
                  {d.source_name}
                  {d.sheet_name ? ` · ${d.sheet_name}` : ""}
                </span>
                <span className={styles.muted}>
                  {d.row_count.toLocaleString("id-ID")} baris · v{d.data_version}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Detail dataset */}
      {selected && (
        <section aria-label={`Detail dataset ${selected.source_name}`} className={styles.detail}>
          <h3 className={styles.detailTitle}>
            {selected.source_name}
            {selected.sheet_name ? ` · ${selected.sheet_name}` : ""}
          </h3>
          <p className={styles.muted}>
            Tabel <code>{selected.table_name}</code> · {selected.row_count.toLocaleString("id-ID")}{" "}
            baris · data v{selected.data_version}
          </p>

          <label className={styles.value}>
            <input
              type="checkbox"
              checked={selected.privacy_no_samples}
              disabled={privacyBusy}
              onChange={(e) => handlePrivacy(selected, e.target.checked)}
            />
            Jangan kirim sample rows ke LLM
          </label>

          {detailError && (
            <p role="alert" className={styles.error}>
              {detailError}
            </p>
          )}

          {/* Re-upload */}
          <div className={styles.reupload}>
            <input
              ref={detailFileRef}
              type="file"
              aria-label={`File re-upload ${selected.source_name}`}
              accept=".csv,.xlsx"
              disabled={reuploadProgress !== null}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleReupload(selected, f);
              }}
            />
            {reuploadProgress !== null && (
              <div
                role="progressbar"
                aria-valuenow={progressPercent(reuploadProgress)}
                aria-valuemin={0}
                aria-valuemax={100}
                className={styles.progress}
              >
                <div
                  className={styles.progressBar}
                  style={{ width: `${progressPercent(reuploadProgress)}%` }}
                />
                <span className={styles.progressLabel}>{progressPercent(reuploadProgress)}%</span>
              </div>
            )}
            {mismatch && (
              <ul className={styles.mismatch} aria-label="Perbedaan skema">
                {mismatch.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>

          {detail && (
            <>
              {/* Skema (Req 6.2) */}
              <table className={styles.table} aria-label="Skema dataset">
                <thead>
                  <tr>
                    <th>Kolom</th>
                    <th>Tipe</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.schema.map((c) => (
                    <tr key={c.name}>
                      <td>{c.name}</td>
                      <td>{typeLabel(c.type)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Profil kolom (Req 6.2) */}
              <table className={styles.table} aria-label="Profil kolom">
                <thead>
                  <tr>
                    <th>Kolom</th>
                    <th>Peran</th>
                    <th>Null</th>
                    <th>Unik</th>
                    <th>Nilai teratas</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.column_profiles.map((p) => (
                    <tr key={p.name}>
                      <td>{p.name}</td>
                      <td>{p.role}</td>
                      <td>{formatPct(p.null_pct)}</td>
                      <td>{p.distinct_count}</td>
                      <td>
                        {p.top_values
                          .map(([v, n]) => `${String(v)} (${n})`)
                          .join(", ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Kualitas data (Req 6.2) */}
              <div className={styles.quality}>
                <h4 className={styles.detailTitle}>Kualitas data</h4>
                <p className={styles.muted}>
                  Baris duplikat: {detail.quality.duplicate_rows.toLocaleString("id-ID")}
                </p>
                {detail.quality.mixed_type_columns.length > 0 && (
                  <p className={styles.warn}>
                    Kolom tipe campuran: {detail.quality.mixed_type_columns.join(", ")}
                  </p>
                )}
                {renamedColumns(detail.column_mapping).length > 0 && (
                  <p className={styles.muted}>
                    Nama kolom dinormalisasi:{" "}
                    {renamedColumns(detail.column_mapping)
                      .map((m) => `${m.original} → ${m.normalized}`)
                      .join(", ")}
                  </p>
                )}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
