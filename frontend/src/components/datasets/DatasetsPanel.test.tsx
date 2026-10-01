// Feature: dashboard-studio-agent — test panel Dataset (Req 2.3, 2.4, 3.2, 3.3,
// 5.3, 6.2, 26.3, 27.4).

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { Dataset, DatasetDetail, JobStatus, UploadResponse } from "@/lib/types";
import { DatasetsPanel } from "./DatasetsPanel";
import {
  combinedProgress,
  describeSchemaDiff,
  progressPercent,
  type DatasetClient,
} from "./client";

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
  column_mapping: [{ original: "order date", normalized: "order_date" }],
  row_count: 100,
  data_version: 1,
  data_updated_at: "2026-01-01T00:00:00Z",
  privacy_no_samples: false,
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const detail = (over: Partial<DatasetDetail> = {}): DatasetDetail => ({
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
    duplicate_rows: 2,
    mixed_type_columns: ["amount"],
    null_pct: { region: 0.05 },
  },
  column_mapping: [{ original: "order date", normalized: "order_date" }],
  ...over,
});

function job(over: Partial<JobStatus> = {}): JobStatus {
  return { status: "running", progress: 0.5, ...over };
}

function memoryClient(over: Partial<DatasetClient> = {}): DatasetClient {
  return {
    upload: vi.fn(async () => ({ upload_id: "u_1", job_id: "j_1" }) as UploadResponse),
    selectSheets: vi.fn(async () => ({
      jobs: [{ sheet: "Penjualan", job_id: "j_s1" }],
    })),
    getJob: vi.fn(async () => job({ status: "done", progress: 1, dataset_id: "ds_1" })),
    getDataset: vi.fn(async () => detail()),
    updateDataset: vi.fn(async () => dataset()),
    reupload: vi.fn(async () => ({ job_id: "j_2" })),
    ...over,
  };
}

function makeFile(name: string): File {
  return new File(["a,b\n1,2\n"], name, { type: "text/csv" });
}

function renderPanel(
  over: {
    datasets?: Dataset[];
    client?: DatasetClient;
    onDatasetsChanged?: () => void;
  } = {},
) {
  const onDatasetsChanged = over.onDatasetsChanged ?? vi.fn();
  const client = over.client ?? memoryClient();
  const utils = render(
    <DatasetsPanel
      workspaceId="ws_1"
      datasets={over.datasets ?? [dataset()]}
      onDatasetsChanged={onDatasetsChanged}
      client={client}
    />,
  );
  return { ...utils, client, onDatasetsChanged };
}

describe("client helpers", () => {
  it("combinedProgress menggabungkan XHR (0–50%) dan job (50–100%) (Req 5.3)", () => {
    expect(combinedProgress(0, 0)).toBe(0);
    expect(combinedProgress(1, 0)).toBeCloseTo(0.5);
    expect(combinedProgress(1, 1)).toBe(1);
    // Upload total tak diketahui dianggap setengah jalan → 25%.
    expect(progressPercent(combinedProgress(null, null))).toBe(25);
  });

  it("describeSchemaDiff menyebut missing/added/changed (Req 26.3)", () => {
    const lines = describeSchemaDiff({
      missing: ["amount"],
      added: ["qty"],
      changed: [{ name: "region", old_type: "string", new_type: "integer" }],
    });
    expect(lines.join(" | ")).toContain("Kolom hilang: amount");
    expect(lines.join(" | ")).toContain("Kolom baru: qty");
    expect(lines.join(" | ")).toContain("region (string → integer)");
  });
});

describe("DatasetsPanel", () => {
  it("mengunggah CSV: progres gabungan lalu refresh daftar (Req 5.3)", async () => {
    const user = userEvent.setup();
    const { client, onDatasetsChanged } = renderPanel();
    const input = screen.getByLabelText("File dataset");
    await user.upload(input, makeFile("baru.csv"));
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    await waitFor(() => expect(onDatasetsChanged).toHaveBeenCalled());
    expect(client.upload).toHaveBeenCalledWith(
      "ws_1",
      expect.any(File),
      expect.objectContaining({
        onProgress: expect.any(Function),
      }),
    );
    // Progressbar hilang setelah job selesai.
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("menampilkan progressbar dengan persen gabungan saat job berjalan (Req 5.3)", async () => {
    const user = userEvent.setup();
    const resolvers: Array<(j: JobStatus) => void> = [];
    const client = memoryClient({
      upload: vi.fn(async (_ws, _file, opts) => {
        opts?.onProgress?.({ loaded: 100, total: 100, fraction: 1 });
        return { upload_id: "u_1", job_id: "j_1" };
      }),
      getJob: vi.fn(
        () =>
          new Promise<JobStatus>((resolve) => {
            resolvers.push(resolve);
          }),
      ),
    });
    renderPanel({ client });
    await user.upload(screen.getByLabelText("File dataset"), makeFile("besar.csv"));
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    await waitFor(() => expect(screen.getByRole("progressbar")).toBeTruthy());
    // Upload selesai (100% → 50%) + job 50% → 75%.
    resolvers[0]?.(job({ status: "running", progress: 0.5 }));
    await waitFor(() =>
      expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("75"),
    );
    resolvers[1]?.(job({ status: "done", progress: 1, dataset_id: "ds_2" }));
  });

  it("upload XLSX menampilkan pemilih sheet lalu konversi (Req 3.2)", async () => {
    const user = userEvent.setup();
    const { client, onDatasetsChanged } = renderPanel({
      client: memoryClient({
        upload: vi.fn(async () => ({
          upload_id: "u_9",
          sheets: [
            { name: "Penjualan", rows_hint: 120 },
            { name: "Kosong", rows_hint: 0 },
          ],
        })),
      }),
    });
    await user.upload(screen.getByLabelText("File dataset"), makeFile("data.xlsx"));
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    expect(await screen.findByLabelText("Pemilih sheet")).toBeTruthy();
    expect(screen.getByText("Penjualan")).toBeTruthy();
    expect(screen.getByText(/Kosong/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Konversi sheet terpilih" }));
    await waitFor(() => expect(onDatasetsChanged).toHaveBeenCalled());
    expect(client.selectSheets).toHaveBeenCalledWith("ws_1", "u_9", ["Penjualan", "Kosong"]);
  });

  it("error parse menampilkan pesan dengan nomor baris (Req 2.3)", async () => {
    const user = userEvent.setup();
    renderPanel({
      client: memoryClient({
        getJob: vi.fn(async () =>
          job({
            status: "failed",
            error: {
              code: "PARSE_ERROR",
              message: "File CSV tidak dapat di-parse: kolom tidak konsisten pada baris 42.",
              details: {},
            },
          }),
        ),
      }),
    });
    await user.upload(screen.getByLabelText("File dataset"), makeFile("rusak.csv"));
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("baris 42");
  });

  it("sheet kosong gagal dengan pesan yang menyebut nama sheet (Req 3.3)", async () => {
    const user = userEvent.setup();
    renderPanel({
      client: memoryClient({
        upload: vi.fn(async () => ({
          upload_id: "u_9",
          sheets: [{ name: "Kosong", rows_hint: 0 }],
        })),
        selectSheets: vi.fn(async () => ({ jobs: [{ sheet: "Kosong", job_id: "j_k" }] })),
        getJob: vi.fn(async () =>
          job({
            status: "failed",
            error: {
              code: "EMPTY_SHEET",
              message: "Sheet 'Kosong' tidak berisi baris data.",
              details: {},
            },
          }),
        ),
      }),
    });
    await user.upload(screen.getByLabelText("File dataset"), makeFile("data.xlsx"));
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    await user.click(await screen.findByRole("button", { name: "Konversi sheet terpilih" }));
    const msg = await screen.findByText(/Kosong: Sheet 'Kosong' tidak berisi baris data\./);
    expect(msg).toBeTruthy();
  });

  it("error format menolak ekstensi selain csv/xlsx (Req 2.4)", async () => {
    const user = userEvent.setup();
    renderPanel({
      client: memoryClient({
        upload: vi.fn(async () => {
          throw new ApiError(
            415,
            "UNSUPPORTED_FORMAT",
            "Format file tidak didukung. Gunakan .csv atau .xlsx.",
          );
        }),
      }),
    });
    // `fireEvent` dipakai karena `user.upload` memvalidasi atribut `accept`.
    fireEvent.change(screen.getByLabelText("File dataset"), {
      target: { files: [makeFile("data.txt")] },
    });
    await user.click(screen.getByRole("button", { name: "Unggah" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(".csv atau .xlsx");
  });

  it("menampilkan detail dataset: skema, profil, kualitas, pemetaan (Req 6.2, 2.5)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    await user.click(screen.getByRole("button", { name: /sales\.csv/ }));
    const detailSection = await screen.findByLabelText("Detail dataset sales.csv");
    expect(client.getDataset).toHaveBeenCalledWith("ws_1", "ds_1");
    expect(screen.getByLabelText("Skema dataset")).toBeTruthy();
    expect(screen.getByText("tanggal")).toBeTruthy(); // typeLabel date
    const profileTable = screen.getByLabelText("Profil kolom");
    expect(profileTable.textContent).toContain("Barat (60)");
    expect(detailSection.textContent).toContain("Baris duplikat: 2");
    expect(detailSection.textContent).toContain("Kolom tipe campuran: amount");
    expect(detailSection.textContent).toContain("order date → order_date");
  });

  it("toggle privasi memanggil updateDataset (Req 27.4)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    await user.click(screen.getByRole("button", { name: /sales\.csv/ }));
    const toggle = await screen.findByLabelText(/Jangan kirim sample rows/);
    await user.click(toggle);
    await waitFor(() =>
      expect(client.updateDataset).toHaveBeenCalledWith("ws_1", "ds_1", {
        privacy_no_samples: true,
      }),
    );
  });

  it("re-upload dengan skema beda menampilkan diff tanpa mengubah state (Req 26.3)", async () => {
    const user = userEvent.setup();
    renderPanel({
      client: memoryClient({
        reupload: vi.fn(async () => {
          throw new ApiError(409, "SCHEMA_MISMATCH", "Skema berbeda.", {
            missing: ["amount"],
            added: ["qty"],
            changed: [{ name: "region", old_type: "string", new_type: "integer" }],
          });
        }),
      }),
    });
    await user.click(screen.getByRole("button", { name: /sales\.csv/ }));
    await screen.findByLabelText("Detail dataset sales.csv");
    await user.upload(
      screen.getByLabelText("File re-upload sales.csv"),
      makeFile("sales_v2.csv"),
    );
    const diff = await screen.findByLabelText("Perbedaan skema");
    expect(diff.textContent).toContain("Kolom hilang: amount");
    expect(diff.textContent).toContain("Kolom baru: qty");
    expect(diff.textContent).toContain("region (string → integer)");
  });

  it("re-upload sukses me-refresh detail dan daftar (Req 26.2)", async () => {
    const user = userEvent.setup();
    const { client, onDatasetsChanged } = renderPanel();
    await user.click(screen.getByRole("button", { name: /sales\.csv/ }));
    await screen.findByLabelText("Detail dataset sales.csv");
    await user.upload(
      screen.getByLabelText("File re-upload sales.csv"),
      makeFile("sales_v2.csv"),
    );
    await waitFor(() => expect(onDatasetsChanged).toHaveBeenCalled());
    expect(client.getDataset).toHaveBeenCalledWith("ws_1", "ds_1");
  });

  it("menampilkan pesan kosong bila belum ada dataset", () => {
    renderPanel({ datasets: [] });
    expect(screen.getByText("Belum ada dataset — unggah CSV atau XLSX.")).toBeTruthy();
  });
});
