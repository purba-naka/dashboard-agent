"use client";

import { useState, type RefObject } from "react";
import type { Predicate } from "@/lib/types";
import {
  activeFilterLabels,
  defaultExportClient,
  errorMessage,
  exportPdfWith,
  fileSlug,
  formatExportTime,
  type ExportClient,
} from "./client";
import styles from "./export.module.css";

export interface ExportBarProps {
  /** Node canvas yang ditangkap (chart + insight sesuai layout). */
  canvasRef: RefObject<HTMLElement | null>;
  /** Judul Dashboard (Req 25.2). */
  title: string;
  /** Global_Filter aktif untuk daftar filter di PDF (Req 25.2). */
  filters: readonly Predicate[];
  /** Jam injeksi untuk test deterministik. */
  clock?: () => Date;
  client?: ExportClient;
}

/**
 * Export_Service: tombol Ekspor PNG & PDF. PNG menangkap node canvas;
 * PDF A4 landscape berisi judul, filter aktif, waktu ekspor, dan gambar
 * dipecah per halaman. Kegagalan → pesan error tanpa mengubah state
 * Dashboard (Req 25.1–25.3).
 */
export function ExportBar({
  canvasRef,
  title,
  filters,
  clock = () => new Date(),
  client = defaultExportClient,
}: ExportBarProps) {
  const [busy, setBusy] = useState<"png" | "pdf" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function exportPng() {
    const node = canvasRef.current;
    if (!node) {
      setError("Canvas belum tersedia untuk diekspor.");
      return;
    }
    setBusy("png");
    setError(null);
    try {
      const dataUrl = await client.toPng(node);
      client.downloadDataUrl(dataUrl, `${fileSlug(title)}.png`);
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function exportPdf() {
    const node = canvasRef.current;
    if (!node) {
      setError("Canvas belum tersedia untuk diekspor.");
      return;
    }
    setBusy("pdf");
    setError(null);
    try {
      await exportPdfWith(client, node, {
        title,
        filterLabels: activeFilterLabels(filters),
        exportedAtLabel: formatExportTime(clock()),
      });
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.bar} role="toolbar" aria-label="Ekspor">
      <button
        type="button"
        className={styles.button}
        disabled={busy !== null}
        onClick={() => void exportPng()}
      >
        {busy === "png" ? "Mengekspor…" : "Ekspor PNG"}
      </button>
      <button
        type="button"
        className={styles.button}
        disabled={busy !== null}
        onClick={() => void exportPdf()}
      >
        {busy === "pdf" ? "Mengekspor…" : "Ekspor PDF"}
      </button>
      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}
