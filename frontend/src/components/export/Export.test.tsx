// Feature: dashboard-studio-agent — test Export_Service (Req 25.1–25.3).
//
// `html-to-image` dan `jspdf` di-mock; instansi jsPDF mock direkam agar isi
// PDF (judul, filter, waktu) dapat diperiksa.

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Predicate } from "@/lib/types";
import { ExportBar } from "./ExportBar";
import {
  activeFilterLabels,
  defaultExportClient,
  exportPdfWith,
  fileSlug,
  formatExportTime,
  type ExportClient,
  type PdfDocLike,
} from "./client";

const toPngMock = vi.hoisted(() => vi.fn(async () => "data:image/png;base64,MOCK"));

vi.mock("html-to-image", () => ({
  toPng: toPngMock,
}));

/** Rekam instansi jsPDF mock + panggilan metodenya. */
const pdfState = vi.hoisted(() => ({
  instances: [] as Array<Record<string, ReturnType<typeof vi.fn>>>,
}));

vi.mock("jspdf", () => ({
  jsPDF: class MockJsPDF {
    text = vi.fn();
    setFontSize = vi.fn();
    addImage = vi.fn();
    addPage = vi.fn();
    save = vi.fn();
    getImageProperties = vi.fn(() => ({ width: 1000, height: 2000 }));
    constructor() {
      pdfState.instances.push(this as unknown as Record<string, ReturnType<typeof vi.fn>>);
    }
  },
}));

afterEach(() => {
  cleanup();
  toPngMock.mockClear();
  pdfState.instances.length = 0;
});

const filters: Predicate[] = [
  { kind: "in", table: "sales", column: "region", values: ["Barat"] },
];

function fakeDoc(over: Partial<PdfDocLike> = {}): PdfDocLike {
  return {
    text: vi.fn(),
    setFontSize: vi.fn(),
    addImage: vi.fn(),
    addPage: vi.fn(),
    getImageProperties: vi.fn(() => ({ width: 1000, height: 2000 })),
    save: vi.fn(),
    ...over,
  };
}

describe("helper ekspor", () => {
  it("formatExportTime deterministik UTC (Req 25.2)", () => {
    expect(formatExportTime(new Date("2026-01-01T14:05:00Z"))).toBe(
      "1 Jan 2026, 14.05 UTC",
    );
  });

  it("activeFilterLabels memakai label filter kanonik (Req 25.2)", () => {
    expect(activeFilterLabels(filters)).toEqual(["sales.region = Barat"]);
  });

  it("fileSlug menghasilkan nama file aman", () => {
    expect(fileSlug("Dashboard Penjualan 2026!")).toBe("dashboard-penjualan-2026");
    expect(fileSlug("...")).toBe("dashboard");
  });

  it("exportPdfWith menulis judul, filter, waktu, lalu gambar per halaman (Req 25.2)", async () => {
    const doc = fakeDoc();
    const client = {
      toPng: vi.fn(async () => "data:image/png;base64,IMG"),
      createPdfDoc: () => doc,
      slice: vi.fn(async () => [
        { dataUrl: "data:image/png;base64,S1", width: 1000, height: 500 },
        { dataUrl: "data:image/png;base64,S2", width: 1000, height: 500 },
      ]),
      savePdf: vi.fn(),
    };
    await exportPdfWith(client, document.body, {
      title: "Dashboard Penjualan",
      filterLabels: ["sales.region = Barat"],
      exportedAtLabel: "1 Jan 2026, 14.05 UTC",
    });
    const texts = (doc.text as ReturnType<typeof vi.fn>).mock.calls.map(
      (c: unknown[]) => String(c[0]),
    );
    expect(texts).toContain("Dashboard Penjualan");
    expect(texts).toContain("Filter aktif: sales.region = Barat");
    expect(texts).toContain("Diekspor: 1 Jan 2026, 14.05 UTC");
    // Dua strip → addImage dua kali, addPage sekali.
    expect((doc.addImage as ReturnType<typeof vi.fn>).mock.calls.length).toBe(2);
    expect(doc.addPage).toHaveBeenCalledTimes(1);
    expect(client.savePdf).toHaveBeenCalledWith(doc, "dashboard-penjualan.pdf");
  });
});

describe("ExportBar", () => {
  function setup(
    over: { client?: ExportClient; filters?: Predicate[] } = {},
  ) {
    const canvasRef = createRef<HTMLDivElement>();
    const clock = () => new Date("2026-01-01T14:05:00Z");
    const utils = render(
      <div>
        <div ref={canvasRef} data-testid="canvas-node" />
        <ExportBar
          canvasRef={canvasRef}
          title="Dashboard Penjualan"
          filters={over.filters ?? filters}
          clock={clock}
          client={over.client ?? defaultExportClient}
        />
      </div>,
    );
    return utils;
  }

  it("ekspor PNG menangkap node canvas (Req 25.1)", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Ekspor PNG" }));
    await waitFor(() => expect(toPngMock).toHaveBeenCalled());
    expect(toPngMock).toHaveBeenCalledWith(
      screen.getByTestId("canvas-node"),
      expect.objectContaining({ pixelRatio: 2, filter: expect.any(Function) }),
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ekspor PDF memuat judul, filter, dan waktu dengan jsPDF (Req 25.2)", async () => {
    const user = userEvent.setup();
    const client: ExportClient = {
      ...defaultExportClient,
      // slice diganti karena jsdom tidak punya Canvas 2D sungguhan.
      slice: vi.fn(async () => [
        { dataUrl: "data:image/png;base64,S1", width: 1000, height: 2000 },
      ]),
    };
    setup({ client });
    await user.click(screen.getByRole("button", { name: "Ekspor PDF" }));
    await waitFor(() => expect(pdfState.instances.length).toBeGreaterThan(0));
    const doc = pdfState.instances[0]!;
    const texts = (doc.text as ReturnType<typeof vi.fn>).mock.calls.map(
      (c: unknown[]) => String(c[0]),
    );
    expect(texts).toContain("Dashboard Penjualan");
    expect(texts).toContain("Filter aktif: sales.region = Barat");
    expect(texts).toContain("Diekspor: 1 Jan 2026, 14.05 UTC");
    expect(doc.addImage).toHaveBeenCalledTimes(1);
    expect(doc.save).toHaveBeenCalledWith("dashboard-penjualan.pdf");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("PDF tanpa filter menuliskan '(tidak ada)' (Req 25.2)", async () => {
    const user = userEvent.setup();
    const client: ExportClient = {
      ...defaultExportClient,
      slice: vi.fn(async () => [
        { dataUrl: "data:image/png;base64,S1", width: 1000, height: 2000 },
      ]),
    };
    setup({ client, filters: [] });
    await user.click(screen.getByRole("button", { name: "Ekspor PDF" }));
    await waitFor(() => expect(pdfState.instances.length).toBeGreaterThan(0));
    const doc = pdfState.instances[0]!;
    const texts = (doc.text as ReturnType<typeof vi.fn>).mock.calls.map(
      (c: unknown[]) => String(c[0]),
    );
    expect(texts).toContain("Filter aktif: (tidak ada)");
  });

  it("kegagalan menampilkan error dan tombol kembali aktif (Req 25.3)", async () => {
    const user = userEvent.setup();
    const client: ExportClient = {
      ...defaultExportClient,
      toPng: vi.fn(async () => {
        throw new Error("gagal menangkap gambar");
      }),
    };
    setup({ client });
    const png = screen.getByRole("button", { name: "Ekspor PNG" });
    await user.click(png);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("gagal menangkap gambar");
    // State tetap: tombol aktif kembali (tidak ada perubahan state Dashboard).
    expect(screen.getByRole("button", { name: "Ekspor PNG" }).hasAttribute("disabled")).toBe(
      false,
    );
  });

  it("canvas belum tersedia → pesan error tanpa menangkap (Req 25.3)", async () => {
    const user = userEvent.setup();
    const canvasRef = createRef<HTMLDivElement>();
    render(
      <ExportBar
        canvasRef={canvasRef}
        title="Dashboard"
        filters={[]}
        clock={() => new Date("2026-01-01T14:05:00Z")}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Ekspor PNG" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Canvas belum tersedia");
    expect(toPngMock).not.toHaveBeenCalled();
  });
});
