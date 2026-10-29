import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { Dataset, DatasetRowsPage } from "@/lib/types";
import { DatasetPreview } from "./DatasetPreview";
import type { DatasetClient } from "./client";

afterEach(cleanup);

const dataset = {
  id: "ds_1",
  source_name: "sales.csv",
  schema: [
    { name: "region", type: "string" },
    { name: "amount", type: "float" },
  ],
  row_count: 150,
} as Dataset;

const page = (offset: number, rows: DatasetRowsPage["rows"]): DatasetRowsPage => ({
  columns: dataset.schema,
  rows,
  total: 150,
  offset,
  limit: 100,
});

function setup(rows: DatasetClient["rows"]) {
  render(
    <DatasetPreview
      workspaceId="ws_1"
      dataset={dataset}
      open
      onOpenChange={() => {}}
      client={{ rows }}
    />,
  );
}

describe("DatasetPreview", () => {
  it("merender baris, label tipe, dan angka id-ID", async () => {
    setup(vi.fn().mockResolvedValue(page(0, [["Barat", 1234.5], ["Timur", null]])));
    expect(await screen.findByText("Barat")).toBeTruthy();
    expect(screen.getByText("1.234,5")).toBeTruthy();
    expect(screen.getByText("float")).toBeTruthy();
    expect(screen.getByText("1–2 dari 150")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sebelumnya" })).toHaveProperty("disabled", true);
  });

  it("Berikutnya meminta halaman dengan offset 100", async () => {
    const rows = vi
      .fn()
      .mockResolvedValueOnce(page(0, [["Barat", 1]]))
      .mockResolvedValueOnce(page(100, [["Utara", 2]]));
    setup(rows);
    await screen.findByText("Barat");
    await userEvent.setup().click(screen.getByRole("button", { name: "Berikutnya" }));
    expect(await screen.findByText("Utara")).toBeTruthy();
    expect(rows).toHaveBeenLastCalledWith(
      "ws_1",
      "ds_1",
      { offset: 100, limit: 100 },
      expect.anything(),
    );
    expect(screen.getByRole("button", { name: "Berikutnya" })).toHaveProperty("disabled", true);
  });

  it("menampilkan state privasi tanpa tabel", async () => {
    setup(vi.fn().mockRejectedValue(new ApiError(403, "PRIVACY_NO_SAMPLES", "x")));
    expect(
      await screen.findByText("Pratinjau dinonaktifkan karena mode privasi."),
    ).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  });

  it("menampilkan error umum", async () => {
    setup(vi.fn().mockRejectedValue(new ApiError(500, "INTERNAL", "Server rusak")));
    expect((await screen.findByRole("alert")).textContent).toContain("Server rusak");
  });
});
