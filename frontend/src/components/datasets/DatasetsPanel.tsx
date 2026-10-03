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
import { UploadSimpleIcon } from "@phosphor-icons/react/ssr";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

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
  const [dragging, setDragging] = useState(false);
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
    <div className="flex flex-col gap-3 rounded-lg border bg-card px-4 py-3.5">
      <h2 className="text-base font-semibold">Dataset</h2>

      {/* Upload baru */}
      <div className="flex flex-col gap-2">
        <label
          data-dragging={dragging || undefined}
          className="flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed border-input px-3 py-4 text-center text-sm transition-colors hover:bg-muted has-focus-visible:ring-3 has-focus-visible:ring-ring/50 has-disabled:cursor-not-allowed has-disabled:opacity-50 data-dragging:border-primary data-dragging:bg-muted"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!uploading) setFile(e.dataTransfer.files[0] ?? null);
          }}
        >
          <UploadSimpleIcon className="size-5 text-muted-foreground" aria-hidden />
          <span className="truncate font-medium">{file ? file.name : "Pilih atau seret file"}</span>
          <span className="text-xs text-muted-foreground">CSV atau XLSX</span>
          <input
            data-slot="file-input"
            ref={uploadFileRef}
            type="file"
            aria-label="File dataset"
            accept=".csv,.xlsx"
            className="sr-only"
            disabled={uploading}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </label>
        <Button variant="outline" disabled={!file || uploading} onClick={handleUpload}>
          {uploading ? "Mengunggah" : "Unggah"}
        </Button>
        {uploading && (
          <div
            role="progressbar"
            aria-valuenow={progressPercent(uploadProgress)}
            aria-valuemin={0}
            aria-valuemax={100}
            className="relative h-5 overflow-hidden rounded-md border border-primary bg-card"
          >
            <div className="h-full bg-primary/15 transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${progressPercent(uploadProgress)}%` }} />
            <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold tabular-nums">{progressPercent(uploadProgress)}%</span>
          </div>
        )}
        {uploadError && (
          <Alert variant="destructive">
            <AlertDescription>{uploadError}</AlertDescription>
          </Alert>
        )}
      </div>

      {/* Pemilih sheet XLSX */}
      {picker && (
        <fieldset className="flex flex-col gap-1.5 rounded-md border px-3 py-2 [&>legend]:px-1 [&>legend]:text-[0.8125rem] [&>legend]:text-muted-foreground" aria-label="Pemilih sheet">
          <legend>Pilih sheet XLSX</legend>
          {picker.sheets.map((s) => (
            <label key={s.name} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                data-slot="checkbox" className="size-4 accent-foreground"
                checked={chosenSheets.includes(s.name)}
                onChange={(e) =>
                  setChosenSheets((prev) =>
                    e.target.checked ? [...prev, s.name] : prev.filter((x) => x !== s.name),
                  )
                }
              />
              {s.name}
              {s.rows_hint !== null && <span className="text-[0.8125rem] text-muted-foreground"> · {s.rows_hint} baris</span>}
            </label>
          ))}
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            disabled={chosenSheets.length === 0}
            onClick={handleSelectSheets}
          >
            Konversi sheet terpilih
          </Button>
          {sheetJobs.map((j) => (
            <p key={j.sheet} className={j.state === "failed" ? "text-[0.8125rem] text-destructive" : "text-[0.8125rem] text-muted-foreground"}>
              {j.sheet}: {j.state === "failed" ? j.error : `${progressPercent(j.progress)}%`}
            </p>
          ))}
        </fieldset>
      )}

      {/* Daftar dataset */}
      {datasets.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground">Belum ada dataset. Unggah CSV atau XLSX.</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {datasets.map((d) => (
            <li key={d.id}>
              <button
                type="button"
                data-slot="dataset-row"
                className="flex w-full min-w-0 flex-col items-start gap-0.5 rounded-md border bg-card px-2.5 py-2 text-left text-sm transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 aria-pressed:border-primary aria-pressed:bg-muted [&>span]:max-w-full"
                aria-pressed={d.id === selectedId}
                onClick={() => setSelectedId(d.id === selectedId ? null : d.id)}
              >
                <span className="block truncate font-semibold">
                  {d.source_name}
                  {d.sheet_name ? ` · ${d.sheet_name}` : ""}
                </span>
                <span className="text-[0.8125rem] text-muted-foreground">
                  {d.row_count.toLocaleString("id-ID")} baris · v{d.data_version}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {/* Detail dataset */}
      {selected && (
        <section aria-label={`Detail dataset ${selected.source_name}`} className="flex flex-col gap-2 border-t pt-3 [&_code]:[overflow-wrap:anywhere]">
          <h3 className="text-[0.9375rem] font-semibold">
            {selected.source_name}
            {selected.sheet_name ? ` · ${selected.sheet_name}` : ""}
          </h3>
          <p className="text-[0.8125rem] text-muted-foreground">
            Tabel <code>{selected.table_name}</code> · {selected.row_count.toLocaleString("id-ID")}{" "}
            baris · data v{selected.data_version}
          </p>

          <label className="flex items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              data-slot="checkbox" className="size-4 accent-foreground"
              checked={selected.privacy_no_samples}
              disabled={privacyBusy}
              onChange={(e) => handlePrivacy(selected, e.target.checked)}
            />
            Jangan kirim sample rows ke LLM
          </label>

          {detailError && (
            <Alert variant="destructive">
            <AlertDescription>{detailError}</AlertDescription>
          </Alert>
          )}

          {/* Re-upload */}
          <div className="flex flex-col gap-2">
            <Input
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
                className="relative h-5 overflow-hidden rounded-md border border-primary bg-card"
              >
                <div
                  className="h-full bg-primary/15 transition-[width] duration-300 motion-reduce:transition-none"
                  style={{ width: `${progressPercent(reuploadProgress)}%` }}
                />
                <span className="absolute inset-0 flex items-center justify-center text-xs font-semibold tabular-nums">{progressPercent(reuploadProgress)}%</span>
              </div>
            )}
            {mismatch && (
              <ul className="list-disc rounded-md bg-warn-soft py-1.5 pr-3 pl-6 text-[0.8125rem] text-warn" aria-label="Perbedaan skema">
                {mismatch.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </div>

          {detail && (
            <>
              {/* Skema (Req 6.2) */}
              <Table className="text-[0.8125rem] tabular-nums" aria-label="Skema dataset">
                <TableHeader>
                  <TableRow>
                    <TableHead>Kolom</TableHead>
                    <TableHead>Tipe</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.schema.map((c) => (
                    <TableRow key={c.name}>
                      <TableCell>{c.name}</TableCell>
                      <TableCell>{typeLabel(c.type)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Profil kolom (Req 6.2) */}
              <Table className="text-[0.8125rem] tabular-nums" aria-label="Profil kolom">
                <TableHeader>
                  <TableRow>
                    <TableHead>Kolom</TableHead>
                    <TableHead>Peran</TableHead>
                    <TableHead>Null</TableHead>
                    <TableHead>Unik</TableHead>
                    <TableHead>Nilai teratas</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.column_profiles.map((p) => (
                    <TableRow key={p.name}>
                      <TableCell>{p.name}</TableCell>
                      <TableCell>{p.role}</TableCell>
                      <TableCell>{formatPct(p.null_pct)}</TableCell>
                      <TableCell>{p.distinct_count}</TableCell>
                      <TableCell>
                        {p.top_values
                          .map(([v, n]) => `${String(v)} (${n})`)
                          .join(", ") || "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Kualitas data (Req 6.2) */}
              <div className="flex flex-col gap-1">
                <h4 className="text-[0.9375rem] font-semibold">Kualitas data</h4>
                <p className="text-[0.8125rem] text-muted-foreground">
                  Baris duplikat: {detail.quality.duplicate_rows.toLocaleString("id-ID")}
                </p>
                {detail.quality.mixed_type_columns.length > 0 && (
                  <p className="text-[0.8125rem] text-warn">
                    Kolom tipe campuran: {detail.quality.mixed_type_columns.join(", ")}
                  </p>
                )}
                {renamedColumns(detail.column_mapping).length > 0 && (
                  <p className="text-[0.8125rem] text-muted-foreground">
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
