// Feature: dashboard-studio-agent — test panel Global_Filter (Req 22.1, 22.3, 24.3, 24.4).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  Command,
  DashboardSnapshot,
  Dataset,
  DatasetDetail,
  FilterSet,
  PatchEvent,
  Predicate,
} from "@/lib/types";
import { GlobalFilterPanel } from "./GlobalFilterPanel";
import type { FilterClient } from "./client";

afterEach(cleanup);

const dataset = (over: Partial<Dataset> = {}): Dataset => ({
  id: "ds_1",
  workspace_id: "ws_1",
  owner_id: "local",
  upload_id: null,
  table_name: "sales",
  source_name: "sales.csv",
  sheet_name: null,
  schema: [
    { name: "order_date", type: "date" },
    { name: "region", type: "string" },
    { name: "amount", type: "float" },
  ],
  column_mapping: [],
  row_count: 100,
  data_version: 1,
  data_updated_at: "2026-01-01T00:00:00Z",
  privacy_no_samples: false,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const detail: DatasetDetail = {
  dataset: dataset(),
  schema: [
    { name: "order_date", type: "date" },
    { name: "region", type: "string" },
    { name: "amount", type: "float" },
  ],
  column_profiles: [
    {
      name: "region",
      type: "string",
      role: "dimension",
      null_count: 0,
      null_pct: 0,
      distinct_count: 2,
      min: null,
      max: null,
      mean: null,
      top_values: [
        ["Barat", 60],
        ["Timur", 40],
      ],
    },
  ],
  quality: {
    duplicate_rows: 0,
    mixed_type_columns: [],
    null_pct: {},
  },
  column_mapping: [],
};

function snapshot(globalFilters: FilterSet = []): DashboardSnapshot {
  return {
    id: "db_1",
    title: "Dashboard",
    version: 5,
    content: {
      title: "Dashboard",
      items: {},
      layout: {},
      global_filters: globalFilters,
    },
    can_undo: false,
    can_redo: false,
    item_status: {},
  };
}

function memoryClient(over: Partial<FilterClient> = {}): FilterClient {
  return {
    command: vi.fn(async () => ({}) as PatchEvent),
    getDataset: vi.fn(async () => detail),
    columnValues: vi.fn(async () => ({ values: ["Barat", "Timur"], truncated: false })),
    ...over,
  };
}

function renderPanel(
  over: {
    globalFilters?: FilterSet;
    crossFilters?: Predicate[];
    client?: FilterClient;
    datasets?: Dataset[];
    onCrossFiltersChange?: (fs: FilterSet) => void;
  } = {},
) {
  const onCrossFiltersChange = over.onCrossFiltersChange ?? vi.fn();
  const client = over.client ?? memoryClient();
  const utils = render(
    <GlobalFilterPanel
      workspaceId="ws_1"
      snapshot={snapshot(over.globalFilters)}
      datasets={over.datasets ?? [dataset()]}
      crossFilters={over.crossFilters ?? []}
      onCrossFiltersChange={onCrossFiltersChange}
      client={client}
    />,
  );
  return { ...utils, client, onCrossFiltersChange };
}

describe("GlobalFilterPanel", () => {
  it("menampilkan chip Global_Filter aktif dengan tombol hapus (Req 22.3)", () => {
    renderPanel({
      globalFilters: [
        { kind: "in", table: "sales", column: "region", values: ["Barat"] },
      ],
    });
    expect(screen.getByText(/sales\.region = Barat/)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Hapus filter sales.region = Barat" }),
    ).toBeTruthy();
  });

  it("menghapus Global_Filter lewat command set_global_filters (Req 22.3)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel({
      globalFilters: [
        { kind: "in", table: "sales", column: "region", values: ["Barat"] },
      ],
    });
    await user.click(
      screen.getByRole("button", { name: "Hapus filter sales.region = Barat" }),
    );
    expect(client.command).toHaveBeenCalledWith("db_1", 5, {
      type: "set_global_filters",
      filters: [],
    });
  });

  it("membuat filter rentang tanggal untuk kolom waktu (Req 22.1)", async () => {
    const { client } = renderPanel();
    const from = await screen.findByLabelText("Order date dari");
    fireEvent.change(from, { target: { value: "2026-01-01" } });
    // Langsung berlaku tanpa tombol terapkan.
    expect(client.command).toHaveBeenLastCalledWith("db_1", 5, {
      type: "set_global_filters",
      filters: [
        {
          kind: "date_range",
          table: "sales",
          column: "order_date",
          start: "2026-01-01",
          end: null,
        },
      ],
    } satisfies Command);
  });

  it("memfilter dimensi lewat dropdown dengan nilai dari API (Req 22.1)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    await user.click(await screen.findByText("Region"));
    const barat = await screen.findByLabelText("Barat");
    await user.click(barat);
    expect(client.command).toHaveBeenCalledWith("db_1", 5, {
      type: "set_global_filters",
      filters: [
        { kind: "in", table: "sales", column: "region", values: ["Barat"] },
      ],
    });
  });

  it("menampilkan chip Cross_Filter aktif dan hapus memanggil callback (Req 24.3, 24.4)", async () => {
    const user = userEvent.setup();
    const cross: Predicate[] = [
      { kind: "in", table: "sales", column: "region", values: ["Timur"] },
    ];
    const { onCrossFiltersChange } = renderPanel({ crossFilters: cross });
    expect(screen.getByText(/Dipilih di chart · sales\.region = Timur/)).toBeTruthy();
    await user.click(
      screen.getByRole("button", { name: "Hapus Cross_Filter sales.region = Timur" }),
    );
    expect(onCrossFiltersChange).toHaveBeenCalledWith([]);
  });

  it("menampilkan pesan error tanpa mengubah state saat command gagal", async () => {
    const user = userEvent.setup();
    renderPanel({
      globalFilters: [
        { kind: "in", table: "sales", column: "region", values: ["Barat"] },
      ],
      client: memoryClient({
        command: vi.fn(async () => {
          throw new Error("konflik versi");
        }),
      }),
    });
    await user.click(
      screen.getByRole("button", { name: "Hapus filter sales.region = Barat" }),
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("alert").textContent).toContain("konflik versi");
  });

  it("menampilkan pesan kosong bila belum ada dataset", () => {
    renderPanel({ datasets: [] });
    expect(
      screen.getByText("Unggah dataset untuk mulai menyaring data."),
    ).toBeTruthy();
  });
});
