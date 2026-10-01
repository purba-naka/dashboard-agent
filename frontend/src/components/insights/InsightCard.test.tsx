import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FilterSet, InsightItem, QueryDetail } from "@/lib/types";
import { InsightCard } from "./InsightCard";
import type { InsightClient } from "./client";

afterEach(cleanup);

const insight = (over: Partial<InsightItem> = {}): InsightItem => ({
  id: "ins_1",
  kind: "insight",
  insight_type: "top_bottom_contributors",
  title: "Kontributor teratas",
  text: "Produk A menyumbang 1.200 penjualan.",
  query_id: "q_1",
  sql: "SELECT product, SUM(amount) FROM t GROUP BY product",
  evidence: {
    columns: [
      { name: "product", type: "string" },
      { name: "total", type: "float" },
    ],
    rows: [
      ["A", 1200],
      ["B", 800],
    ],
    row_count: 2,
  },
  matched_numbers: [],
  filters_snapshot: [],
  dataset_ids: ["ds_1"],
  computed_at: "2026-01-01T00:00:00Z",
  dataset_versions: { ds_1: 1 },
  ...over,
});

const queryDetail: QueryDetail = {
  sql: "SELECT product, SUM(amount) AS total FROM t GROUP BY product",
  columns: [
    { name: "product", type: "string" },
    { name: "total", type: "float" },
  ],
  rows: [
    ["A", 1200],
    ["B", 800],
  ],
  row_count: 2,
  executed_at: "2026-01-01T00:00:00Z",
  filters: [],
};

function memoryClient(over: Partial<InsightClient> = {}): InsightClient {
  return {
    query: vi.fn(async () => queryDetail),
    refresh: vi.fn(async () => {
      throw new Error("not used");
    }),
    ...over,
  };
}

describe("InsightCard", () => {
  it("menampilkan teks dan tipe insight", () => {
    render(
      <InsightCard
        insight={insight()}
        dashboardId="db_1"
        baseVersion={1}
        activeFilters={[]}
        client={memoryClient()}
      />,
    );
    expect(screen.getByText("Produk A menyumbang 1.200 penjualan.")).toBeTruthy();
    expect(screen.getByText("Kontributor teratas/terbawah")).toBeTruthy();
  });

  it("menampilkan badge filter berbeda ketika snapshot filter tidak sama (Req 15.3)", () => {
    const activeFilters: FilterSet = [
      { kind: "in", table: "t", column: "region", values: ["Barat"] },
    ];
    render(
      <InsightCard
        insight={insight()}
        dashboardId="db_1"
        baseVersion={1}
        activeFilters={activeFilters}
        client={memoryClient()}
      />,
    );
    expect(screen.getByText("Dihitung dengan filter berbeda")).toBeTruthy();
  });

  it("menampilkan badge stale ketika data_version lebih baru (Req 26.2)", () => {
    render(
      <InsightCard
        insight={insight()}
        dashboardId="db_1"
        baseVersion={1}
        activeFilters={[]}
        datasetVersions={{ ds_1: 2 }}
        client={memoryClient()}
      />,
    );
    expect(screen.getByText("Data sumber sudah berubah")).toBeTruthy();
  });

  it("membuka detail dengan SQL sumber dan tabel bukti (Req 15.1)", async () => {
    const user = userEvent.setup();
    const client = memoryClient();
    render(
      <InsightCard
        insight={insight()}
        dashboardId="db_1"
        baseVersion={1}
        activeFilters={[]}
        client={client}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Detail" }));
    const dialog = await screen.findByRole("dialog");
    expect(client.query).toHaveBeenCalledWith("q_1");
    expect(within(dialog).getByText(/GROUP BY product/)).toBeTruthy();
    expect(within(dialog).getByText("product")).toBeTruthy();
    expect(within(dialog).getAllByText("A").length).toBeGreaterThan(0);
  });

  it("memanggil refresh dengan base_version (Req 15.1)", async () => {
    const user = userEvent.setup();
    const refresh = vi.fn(async () => ({}) as never);
    render(
      <InsightCard
        insight={insight()}
        dashboardId="db_1"
        baseVersion={4}
        activeFilters={[]}
        client={memoryClient({ refresh })}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Segarkan" }));
    expect(refresh).toHaveBeenCalledWith("db_1", "ins_1", 4);
  });
});
