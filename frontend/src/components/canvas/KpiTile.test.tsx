// Feature: dashboard-studio-agent — KPI_Card, verifikasi item, dan reset filter (Req 22.8, 34.2, 38.7).

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DashboardSnapshot, KpiItem, PatchEvent, RenderedItem } from "@/lib/types";
import { Canvas } from "./Canvas";
import type { CanvasClient } from "./client";
import { KpiTile, kpiAriaLabel } from "./KpiTile";

vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ containerRef: { current: null }, width: 800 }),
  GridLayout: (props: { children?: React.ReactNode }) => <div data-testid="grid">{props.children}</div>,
}));
vi.mock("echarts-for-react", () => ({ default: () => <div data-testid="echarts" /> }));

afterEach(cleanup);

const kpi = (over: Partial<KpiItem["spec"]> = {}): KpiItem => ({
  id: "k1",
  kind: "kpi",
  title: "Revenue",
  spec: {
    spec_version: 1,
    query_id: "q1",
    value_column: "total",
    comparison_column: "prev",
    comparison_label: "vs bulan lalu",
    format: { style: "currency", currency: "IDR", decimals: 1, compact: true },
    good_direction: "up",
    metric_name: "revenue",
    ...over,
  },
});

const rendered = (sentiment: "positive" | "negative" | "neutral", delta = "+Rp 1,2 juta"): RenderedItem => ({
  status: "ok",
  filter_unaffected: false,
  kpi: {
    value: 17_600_000,
    comparison: 16_400_000,
    delta: 1_200_000,
    delta_pct: 7.3,
    sentiment,
    formatted: { value: "Rp 17,6 juta", comparison: "Rp 16,4 juta", delta, delta_pct: "+7,3%" },
  },
});

describe("KpiTile", () => {
  it("menampilkan nilai, delta berwarna sesuai sentimen, dan aria-label deskriptif", () => {
    render(<KpiTile item={kpi()} rendered={rendered("positive")} />);
    const group = screen.getByRole("group");
    expect(group.getAttribute("aria-label")).toBe(
      "Revenue: Rp 17,6 juta, +Rp 1,2 juta (+7,3%) vs bulan lalu, membaik",
    );
    expect(screen.getByText("Rp 17,6 juta")).toBeTruthy();
    const delta = screen.getByText(/\+Rp 1,2 juta/);
    expect(delta.getAttribute("data-sentiment")).toBe("positive");
  });

  it("sentimen negatif memakai kelas negatif", () => {
    render(<KpiTile item={kpi({ good_direction: "down" })} rendered={rendered("negative")} />);
    expect(screen.getByText(/\+Rp 1,2 juta/).getAttribute("data-sentiment")).toBe("negative");
    expect(kpiAriaLabel(kpi(), rendered("negative"))).toContain("memburuk");
  });

  it("menampilkan error KPI_SHAPE", () => {
    render(
      <KpiTile
        item={kpi()}
        rendered={{ status: "error", filter_unaffected: false, error: { code: "KPI_SHAPE", message: "x", details: {} } }}
      />,
    );
    expect(screen.getByText(/KPI_SHAPE/)).toBeTruthy();
  });
});

function snapshot(globalFilters = true): DashboardSnapshot {
  return {
    id: "db_1",
    title: "D",
    version: 5,
    content: {
      title: "D",
      items: { k1: kpi() },
      layout: { k1: { x: 0, y: 0, w: 3, h: 2 } },
      global_filters: globalFilters
        ? [{ kind: "in", table: "sales", column: "region", values: ["Jawa"] }]
        : [],
    },
    can_undo: false,
    can_redo: false,
    item_status: { k1: { invalid: false, stale: false } },
  };
}

describe("Canvas ekstensi", () => {
  const client = (): CanvasClient => ({
    render: vi.fn(async () => ({ version: 5, items: { k1: rendered("neutral") } })),
    command: vi.fn(async () => ({}) as PatchEvent),
    verify: vi.fn(async () => undefined),
  });

  it("merender KPI_Card di grid", async () => {
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={client()} />);
    expect(await screen.findByText("Rp 17,6 juta")).toBeTruthy();
  });

  it("Reset semua filter mengosongkan Global_Filter dan Cross_Filter (Req 22.8)", async () => {
    const c = client();
    const onCross = vi.fn();
    render(
      <Canvas
        snapshot={snapshot()}
        crossFilters={[{ kind: "in", table: "sales", column: "region", values: ["Bali"] }]}
        client={c}
        onCrossFiltersChange={onCross}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Reset semua filter" }));
    expect(onCross).toHaveBeenCalledWith([]);
    expect(c.command).toHaveBeenCalledWith("db_1", 5, { type: "set_global_filters", filters: [] });
  });

  it("tanpa filter aktif tombol reset tidak tampil", async () => {
    render(<Canvas snapshot={snapshot(false)} crossFilters={[]} client={client()} />);
    await screen.findByText("Rp 17,6 juta");
    expect(screen.queryByRole("button", { name: "Reset semua filter" })).toBeNull();
  });

  it("Tandai terverifikasi memanggil verify (Req 34.2)", async () => {
    const c = client();
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={c} />);
    await userEvent.click(await screen.findByRole("button", { name: "Aksi untuk Revenue" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Tandai benar/ }));
    expect(c.verify).toHaveBeenCalledWith("db_1", "k1");
    expect(await screen.findByText("Hasil benar")).toBeTruthy();
  });
});
