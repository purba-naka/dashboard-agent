/**
 * Port data & helper Export_Service (Req 25.1–25.3).
 *
 * PNG memakai `html-to-image.toPng(canvasNode)`; PDF memakai jsPDF A4
 * landscape berisi judul Dashboard, filter aktif, waktu ekspor, lalu gambar
 * canvas dipecah per halaman. Kegagalan ditangani komponen (toast, tanpa
 * mengubah state Dashboard). Komponen menerima `client` lewat props sehingga
 * dapat dirender dengan implementasi in-memory di test.
 */

import { toPng } from "html-to-image";
import { jsPDF } from "jspdf";
import { describeFilters } from "@/lib/filters";
import type { Predicate } from "@/lib/types";

// ---------------------------------------------------------------------------
// Kontrak
// ---------------------------------------------------------------------------

/** Sepotong gambar vertikal untuk satu halaman PDF. */
export interface ImageStrip {
  dataUrl: string;
  width: number;
  height: number;
}

/** Bagian jsPDF yang dipakai Export_Service (mudah di-mock). */
export interface PdfDocLike {
  text(text: string, x: number, y: number): void;
  setFontSize(size: number): void;
  addImage(dataUrl: string, format: string, x: number, y: number, w: number, h: number): void;
  addPage(): void;
  getImageProperties(dataUrl: string): { width: number; height: number };
  save(filename: string): void;
}

export interface ExportClient {
  /** Tangkap node DOM sebagai data URL PNG (setelah chart selesai render). */
  toPng(node: HTMLElement): Promise<string>;
  /** Dokumen jsPDF A4 landscape baru. */
  createPdfDoc(): PdfDocLike;
  /** Potong gambar menjadi strip vertikal selebar `widthPx`. */
  slice(dataUrl: string, widthPx: number, stripHeightPx: number): Promise<ImageStrip[]>;
  /** Simpan dokumen PDF (memicu unduhan). */
  savePdf(doc: PdfDocLike, filename: string): void;
  /** Unduh data URL PNG. */
  downloadDataUrl(dataUrl: string, filename: string): void;
}

export const defaultExportClient: ExportClient = {
  toPng: (node) => toPng(node, { pixelRatio: 2 }),
  createPdfDoc: () =>
    new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" }) as unknown as PdfDocLike,
  slice: sliceImageStrips,
  savePdf: (doc, filename) => doc.save(filename),
  downloadDataUrl: downloadDataUrl,
};

// ---------------------------------------------------------------------------
// Meta PDF (Req 25.2)
// ---------------------------------------------------------------------------

export interface PdfMeta {
  title: string;
  filterLabels: string[];
  exportedAtLabel: string;
}

const MONTHS_ID = [
  "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
  "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
];

/** Waktu ekspor deterministik (UTC) agar mudah diuji: "1 Jan 2026, 14.05 UTC". */
export function formatExportTime(date: Date): string {
  const hh = String(date.getUTCHours()).padStart(2, "0");
  const mm = String(date.getUTCMinutes()).padStart(2, "0");
  return `${date.getUTCDate()} ${MONTHS_ID[date.getUTCMonth()]} ${date.getUTCFullYear()}, ${hh}.${mm} UTC`;
}

/** Label filter aktif untuk PDF/chip (Req 25.2). */
export function activeFilterLabels(filters: readonly Predicate[]): string[] {
  return describeFilters(filters);
}

/** Nama file aman dari judul Dashboard. */
export function fileSlug(title: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug === "" ? "dashboard" : slug;
}

// ---------------------------------------------------------------------------
// Layout PDF & orkestrasi
// ---------------------------------------------------------------------------

const PAGE_W_MM = 297; // A4 landscape
const PAGE_H_MM = 210;
const MARGIN_MM = 15;
const CONTENT_W_MM = PAGE_W_MM - 2 * MARGIN_MM;

/**
 * Susun PDF: halaman 1 berisi judul, daftar filter aktif, dan waktu ekspor,
 * lalu gambar dashboard dipecah per halaman (Req 25.2).
 */
export async function exportPdfWith(
  client: Pick<ExportClient, "toPng" | "createPdfDoc" | "slice" | "savePdf">,
  node: HTMLElement,
  meta: PdfMeta,
): Promise<void> {
  const dataUrl = await client.toPng(node);
  const doc = client.createPdfDoc();
  const { width, height } = doc.getImageProperties(dataUrl);

  // Header halaman pertama.
  let y = MARGIN_MM + 4;
  doc.setFontSize(16);
  doc.text(meta.title, MARGIN_MM, y);
  y += 7;
  doc.setFontSize(9);
  if (meta.filterLabels.length === 0) {
    doc.text("Filter aktif: (tidak ada)", MARGIN_MM, y);
    y += 5;
  } else {
    for (const label of meta.filterLabels) {
      doc.text(`Filter aktif: ${label}`, MARGIN_MM, y);
      y += 5;
    }
  }
  doc.text(`Diekspor: ${meta.exportedAtLabel}`, MARGIN_MM, y);
  y += 4;

  // Tinggi strip: muat sisa halaman pertama (halaman lanjutan setinggi penuh).
  const pxPerMm = width / CONTENT_W_MM;
  const firstAvailMm = Math.max(10, PAGE_H_MM - y - MARGIN_MM);
  const stripPx = Math.max(1, Math.round(firstAvailMm * pxPerMm));
  const strips = await client.slice(dataUrl, width, stripPx);

  let cursor = y;
  for (let i = 0; i < strips.length; i++) {
    const strip = strips[i];
    if (i > 0) {
      doc.addPage();
      cursor = MARGIN_MM;
    }
    const hMm = Math.min(
      PAGE_H_MM - MARGIN_MM - cursor,
      (strip.height / Math.max(1, strip.width)) * CONTENT_W_MM,
    );
    doc.addImage(strip.dataUrl, "PNG", MARGIN_MM, cursor, CONTENT_W_MM, hMm);
    cursor += hMm;
    if (height <= 0) break; // gambar tanpa dimensi → jangan halaman tak hingga
  }
  client.savePdf(doc, `${fileSlug(meta.title)}.pdf`);
}

// ---------------------------------------------------------------------------
// Implementasi default (browser)
// ---------------------------------------------------------------------------

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Gambar dashboard tidak dapat dibaca."));
    img.src = dataUrl;
  });
}

async function sliceImageStrips(
  dataUrl: string,
  widthPx: number,
  stripHeightPx: number,
): Promise<ImageStrip[]> {
  const img = await loadImage(dataUrl);
  const scale = widthPx / Math.max(1, img.width);
  const targetH = img.height * scale;
  const strips: ImageStrip[] = [];
  for (let y = 0; y < targetH; y += stripHeightPx) {
    const h = Math.min(stripHeightPx, targetH - y);
    const canvas = document.createElement("canvas");
    canvas.width = widthPx;
    canvas.height = Math.round(h);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D tidak tersedia di lingkungan ini.");
    ctx.drawImage(img, 0, y / scale, img.width, h / scale, 0, 0, widthPx, Math.round(h));
    strips.push({ dataUrl: canvas.toDataURL("image/png"), width: widthPx, height: Math.round(h) });
  }
  return strips;
}

function downloadDataUrl(dataUrl: string, filename: string): void {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Pesan yang aman ditampilkan ke pengguna untuk error apa pun. */
export function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "Ekspor gagal.";
}
