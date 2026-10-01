// Feature: dashboard-studio-agent — test komponen Canvas_Editor (Req 7.8, 23.3, 24.3, 26.2).
//
// `react-grid-layout` dan `echarts-for-react` di-mock karena jsdom tidak
// memiliki layout sungguhan (clientWidth selalu 0) dan canvas ECharts berat;
// prop grid ditangkap agar `onLayoutChange` dapat dipicu manual.

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ChartItem,
  DashboardSnapshot,
  Dataset,
  InsightItem,
  PatchEvent,
  Predicate,
  RenderResponse,
} from "@/lib/types";
import { Canvas } from "./Canvas";
import type { CanvasClient } from "./client";

// Prop GridLayout mock terakhir, dipakai untuk memicu `onLayoutChange`.
const gridState = vi.hoisted(() => ({
  props: undefined as { onLayoutChange?: (layout: unknown[]) => void } | undefined,
}));

vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ containerRef: { current: null }, width: 800 }),
  GridLayout: (props: { children?: React.ReactNode } & Record<string, unknown>) => {
    gridState.props = props as { onLayoutChange?: (layout: unknown[]) => void };
    return <div data-testid="grid">{props.children}</div>;
  },
}));

// Prop ECharts mock terakhir, dipakai untuk memicu klik elemen chart.
const echartsState = vi.hoisted(() => ({
  props: undefined as { onEvents?: Record<string, (p: unknown) => void> } | undefined,
}));

vi.mock("echarts-for-react", () => ({
  default: (
    props: {
      option: Record<string, unknown>;
      onEvents?: Record<string, (p: unknown) => void>;
    },
  ) => {
    echartsState.props = props;
    return <div data-testid="echarts" data-option={JSON.stringify(props.option)} />;
  },
}));

afterEach(cleanup);

const chart = (over: Partial<ChartItem> = {}): ChartItem => ({
  id: "ch_1",
  kind: "chart",
  title: "Penjualan per bulan",
  spec: {
    spec_version: 1,
    query_id: "q_1",
    chart_type: "bar",
    option: { xAxis: { type: "category" } },
    cross_filter_column: null,
  },
  ...over,
});

const insight = (over: Partial<InsightItem> = {}): InsightItem => ({
  id: "ins_1",
  kind: "insight",
  insight_type: "top_bottom_contributors",
  title: "Kontributor teratas",
  text: "Produk A menyumbang 1.200 penjualan.",
  query_id: "q_2",
  sql: "SELECT product, SUM(amount) FROM t GROUP BY product",
  evidence: { columns: [], rows: [], row_count: 0 },
  matched_numbers: [],
  filters_snapshot: [],
  dataset_ids: ["ds_1"],
  computed_at: "2026-01-01T00:00:00Z",
  dataset_versions: { ds_1: 1 },
  ...over,
});

function snapshot(over: Partial<DashboardSnapshot> = {}): DashboardSnapshot {
  return {
    id: "db_1",
    title: "Dashboard Penjualan",
    version: 3,
    content: {
      title: "Dashboard Penjualan",
      items: {
        ch_1: chart(),
        ins_1: insight(),
      },
      layout: {
        ch_1: { x: 0, y: 1, w: 6, h: 4 },
        ins_1: { x: 6, y: 0, w: 6, h: 2 },
      },
      global_filters: [],
    },
    can_undo: true,
    can_redo: false,
    item_status: {
      ch_1: { invalid: false, stale: false },
      ins_1: { invalid: false, stale: false },
    },
    ...over,
  };
}

const renderResponse: RenderResponse = {
  version: 3,
  items: {
    ch_1: {
      option: { series: [{ type: "bar" }] },
      status: "ok",
      filter_unaffected: false,
    },
    ins_1: { status: "ok", filter_unaffected: false },
  },
};

function memoryClient(over: Partial<CanvasClient> = {}): CanvasClient {
  return {
    render: vi.fn(async () => renderResponse),
    command: vi.fn(async () => ({}) as PatchEvent),
    ...over,
  };
}

/** Ambil prop `onLayoutChange` terakhir yang diterima GridLayout mock. */
function lastGridProps() {
  return gridState.props;
}

describe("Canvas", () => {
  it("mengurutkan item berdasarkan layout y lalu x (Req 23.3)", async () => {
    const client = memoryClient();
    render(
      <Canvas snapshot={snapshot()} crossFilters={[]} client={client} />,
    );
    // ins_1 (y=0) tampil sebelum ch_1 (y=1).
    const buttons = await screen.findAllByRole("button", { name: /^Hapus item/ });
    const labels = buttons.map((b) => b.getAttribute("aria-label"));
    expect(labels[0]).toContain("Kontributor teratas");
    expect(labels[1]).toContain("Penjualan per bulan");
    expect(client.render).toHaveBeenCalled();
  });

  it("menampilkan badge invalid dan stale dari item_status (Req 26.2)", () => {
    render(
      <Canvas
        snapshot={snapshot({
          item_status: {
            ch_1: { invalid: true, stale: false },
            ins_1: { invalid: false, stale: true },
          },
        })}
        crossFilters={[]}
        client={memoryClient()}
      />,
    );
    expect(screen.getByText("Tidak valid")).toBeTruthy();
    expect(screen.getByText("Data berubah")).toBeTruthy();
  });

  it("menampilkan penanda Cross_Filter hanya untuk chart dengan kolom aktif (Req 24.3)", () => {
    const withColumn = snapshot({
      content: {
        ...snapshot().content,
        items: {
          ch_1: chart({
            spec: { ...chart().spec, cross_filter_column: "region" },
          }),
          ins_1: insight(),
        },
      },
    });
    const crossFilter: Predicate = {
      kind: "in",
      table: "t",
      column: "region",
      values: ["Barat"],
    };
    const { rerender } = render(
      <Canvas snapshot={withColumn} crossFilters={[]} client={memoryClient()} />,
    );
    // Tanpa Cross_Filter aktif tidak ada penanda.
    expect(screen.queryByText("Cross_Filter")).toBeNull();

    rerender(
      <Canvas snapshot={withColumn} crossFilters={[crossFilter]} client={memoryClient()} />,
    );
    expect(screen.getByText("Cross_Filter")).toBeTruthy();
  });

  it("tombol hapus mengirim command remove_item dengan versi dasar (Req 23.3)", async () => {
    const user = userEvent.setup();
    const client = memoryClient();
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={client} />);
    await user.click(screen.getByRole("button", { name: "Hapus item Penjualan per bulan" }));
    expect(client.command).toHaveBeenCalledWith("db_1", 3, {
      type: "remove_item",
      id: "ch_1",
    });
  });

  it("perubahan tata letak mengirim command set_layout (Req 17.4)", () => {
    const client = memoryClient();
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={client} />);
    const props = lastGridProps();
    if (!props?.onLayoutChange) throw new Error("onLayoutChange tidak terpasang");
    props.onLayoutChange([
      { i: "ch_1", x: 3, y: 2, w: 6, h: 4 },
      { i: "ins_1", x: 6, y: 0, w: 6, h: 2 },
    ]);
    expect(client.command).toHaveBeenCalledWith("db_1", 3, {
      type: "set_layout",
      changes: { ch_1: { x: 3, y: 2, w: 6, h: 4 } },
    });
  });

  it("merender chart dengan option hasil /render dan Cross_Filter (Req 22.3)", async () => {
    const client = memoryClient();
    const crossFilter: Predicate = {
      kind: "in",
      table: "t",
      column: "region",
      values: ["Barat"],
    };
    render(
      <Canvas snapshot={snapshot()} crossFilters={[crossFilter]} client={client} />,
    );
    expect(client.render).toHaveBeenCalledWith(
      "db_1",
      ["ins_1", "ch_1"],
      [crossFilter],
      expect.objectContaining({ signal: expect.anything() }),
    );
    const echarts = await screen.findByTestId("echarts");
    expect(JSON.parse(echarts.getAttribute("data-option") ?? "{}")).toEqual({
      series: [{ type: "bar" }],
    });
  });

  it("kegagalan /render menampilkan pesan error tanpa merusak grid (Req 22.3)", async () => {
    const client = memoryClient({
      render: vi.fn(async () => {
        throw new Error("gagal render");
      }),
    });
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={client} />);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("gagal render");
    // Grid tetap ada.
    expect(screen.getByTestId("grid")).toBeTruthy();
  });

  it("merender insight di dalam canvas dengan InsightCard (Req 26.2)", async () => {
    render(<Canvas snapshot={snapshot()} crossFilters={[]} client={memoryClient()} />);
    expect(
      await screen.findByText("Produk A menyumbang 1.200 penjualan."),
    ).toBeTruthy();
  });

  it("memuat ulang render saat versi snapshot berubah (Req 18.3)", async () => {
    const client = memoryClient();
    const { rerender } = render(
      <Canvas snapshot={snapshot()} crossFilters={[]} client={client} />,
    );
    rerender(
      <Canvas
        snapshot={snapshot({ version: 4 })}
        crossFilters={[]}
        client={client}
      />,
    );
    expect(client.render).toHaveBeenCalledTimes(2);
  });

  it("klik elemen chart men-toggle Cross_Filter pada kolom sumber (Req 24.1)", async () => {
    const client = memoryClient();
    const datasets: Dataset[] = [
      {
        id: "ds_1",
        workspace_id: "ws",
        owner_id: "local",
        upload_id: null,
        table_name: "sales",
        source_name: "sales.csv",
        sheet_name: null,
        schema: [
          { name: "region", type: "string" },
          { name: "amount", type: "float" },
        ],
        column_mapping: [],
        row_count: 10,
        data_version: 1,
        data_updated_at: "2026-01-01T00:00:00Z",
        privacy_no_samples: false,
        created_at: "2026-01-01T00:00:00Z",
      },
    ];
    const withColumn = snapshot({
      content: {
        ...snapshot().content,
        items: {
          ch_1: chart({
            spec: { ...chart().spec, cross_filter_column: "region" },
          }),
          ins_1: insight(),
        },
      },
    });
    const onCrossFiltersChange = vi.fn();
    const { rerender } = render(
      <Canvas
        snapshot={withColumn}
        crossFilters={[]}
        client={client}
        datasets={datasets}
        onCrossFiltersChange={onCrossFiltersChange}
      />,
    );
    // Tunggu chart dirender (option tersedia setelah /render selesai).
    await screen.findByTestId("echarts");
    const props = echartsState.props;
    if (!props?.onEvents?.click) throw new Error("onEvents.click tidak terpasang");
    props.onEvents.click({ name: "Barat", value: 120 });
    expect(onCrossFiltersChange).toHaveBeenCalledWith([
      { kind: "in", table: "sales", column: "region", values: ["Barat"] },
    ]);
    // Klik elemen yang sama lagi setelah parent memperbarui Cross_Filter →
    // toggle mati (Property 33).
    const active: Predicate[] = [
      { kind: "in", table: "sales", column: "region", values: ["Barat"] },
    ];
    rerender(
      <Canvas
        snapshot={withColumn}
        crossFilters={active}
        client={client}
        datasets={datasets}
        onCrossFiltersChange={onCrossFiltersChange}
      />,
    );
    await screen.findByTestId("echarts");
    const propsAfter = echartsState.props;
    if (!propsAfter?.onEvents?.click) throw new Error("onEvents.click tidak terpasang");
    propsAfter.onEvents.click({ name: "Barat", value: 120 });
    expect(onCrossFiltersChange).toHaveBeenLastCalledWith([]);
  });
});
