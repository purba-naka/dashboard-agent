"use client";

import { useEffect, useState, type RefObject } from "react";
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
import { CaretDownIcon, FileImageIcon, FilePdfIcon } from "@phosphor-icons/react/ssr";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

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
  /** Format yang baru selesai diekspor; tampil sebentar sebagai konfirmasi. */
  const [done, setDone] = useState<"png" | "pdf" | null>(null);
  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => setDone(null), 2500);
    return () => clearTimeout(t);
  }, [done]);

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
      setDone("png");
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
      setDone("pdf");
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <TooltipProvider delayDuration={300}>
    <div className="relative flex items-center gap-2" role="toolbar" aria-label="Ekspor">
      {/* Split button: PDF aksi utama, PNG di menu. */}
      <div className="flex items-center">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              className="rounded-r-none"
              aria-label="Ekspor PDF"
              disabled={busy !== null}
              onClick={() => void exportPdf()}
            >
              <FilePdfIcon data-icon="inline-start" />
              <span className="hidden md:inline">{busy ? "Mengekspor" : "Ekspor"}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Ekspor PDF</TooltipContent>
        </Tooltip>
        <DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="outline"
                  size="icon"
                  className="-ml-px rounded-l-none"
                  aria-label="Opsi ekspor"
                  disabled={busy !== null}
                >
                  <CaretDownIcon />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>Opsi ekspor</TooltipContent>
          </Tooltip>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => void exportPdf()}>
              <FilePdfIcon /> Ekspor PDF
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportPng()}>
              <FileImageIcon /> Ekspor PNG
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <span role="status" className="text-xs whitespace-nowrap text-muted-foreground empty:hidden">
        {busy ? <span className="sr-only">Mengekspor {busy.toUpperCase()}</span> : done ? `${done.toUpperCase()} diunduh` : ""}
      </span>
      {/* Melayang di bawah header agar pesan panjang tidak mendorong tata letak. */}
      {error && (
        <p
          role="alert"
          className="absolute top-full right-0 z-50 mt-2 w-max max-w-72 rounded-md border border-destructive/40 bg-popover px-3 py-2 text-[0.8125rem] text-destructive shadow-(--shadow-lg)"
        >
          {error}
          <button
            type="button"
            className="ml-2 underline underline-offset-2"
            onClick={() => setError(null)}
          >
            Tutup
          </button>
        </p>
      )}
    </div>
    </TooltipProvider>
  );
}
