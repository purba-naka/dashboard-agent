// Feature: dashboard-studio-agent — test panel relasi (Req 7.2–7.5, 7.8).

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Dataset, Relation } from "@/lib/types";
import { RelationsPanel } from "./RelationsPanel";
import type { RelationClient } from "./client";

afterEach(cleanup);

const relation = (over: Partial<Relation> = {}): Relation => ({
  id: "rel_1",
  workspace_id: "ws_1",
  candidate_key: "customers.id|orders.customer_id",
  from_dataset_id: "ds_c",
  from_table: "customers",
  from_column: "id",
  to_dataset_id: "ds_o",
  to_table: "orders",
  to_column: "customer_id",
  cardinality: "one_to_many",
  overlap_pct: 98,
  status: "candidate",
  created_at: "2026-01-01T00:00:00Z",
  decided_at: null,
  ...over,
});

const datasets: Dataset[] = [
  {
    id: "ds_c",
    workspace_id: "ws_1",
    owner_id: "local",
    upload_id: null,
    table_name: "customers",
    source_name: "customers.csv",
    sheet_name: null,
    schema: [],
    column_mapping: [],
    row_count: 10,
    data_version: 1,
    data_updated_at: "2026-01-01T00:00:00Z",
    privacy_no_samples: false,
    created_at: "2026-01-01T00:00:00Z",
  },
];

function memoryClient(over: Partial<RelationClient> = {}): RelationClient {
  return {
    list: vi.fn(async () => []),
    detect: vi.fn(async () => [relation()]),
    confirm: vi.fn(async (_ws, id) => relation({ id, status: "confirmed" })),
    reject: vi.fn(async (_ws, id) => relation({ id, status: "rejected" })),
    remove: vi.fn(async () => undefined),
    ...over,
  };
}

function renderPanel(
  over: {
    relations?: Relation[];
    client?: RelationClient;
    onRelationsChange?: (r: Relation[]) => void;
    datasets?: Dataset[];
  } = {},
) {
  const onRelationsChange = over.onRelationsChange ?? vi.fn();
  const client = over.client ?? memoryClient();
  const utils = render(
    <RelationsPanel
      workspaceId="ws_1"
      relations={over.relations ?? [relation()]}
      datasets={over.datasets ?? datasets}
      onRelationsChange={onRelationsChange}
      client={client}
    />,
  );
  return { ...utils, client, onRelationsChange };
}

describe("RelationsPanel", () => {
  it("mengelompokkan relasi per status dengan kardinalitas dan overlap (Req 7.2)", () => {
    renderPanel({
      relations: [
        relation(),
        relation({ id: "rel_2", status: "confirmed", cardinality: "one_to_one", overlap_pct: 100 }),
        relation({ id: "rel_3", status: "rejected" }),
      ],
    });
    const candidate = screen.getByLabelText("Relasi Kandidat");
    const confirmed = screen.getByLabelText("Relasi Terkonfirmasi");
    const rejected = screen.getByLabelText("Relasi Ditolak");
    expect(candidate.textContent).toContain("customers.id → orders.customer_id");
    expect(candidate.textContent).toContain("one-to-many (1:N)");
    expect(candidate.textContent).toContain("98%");
    expect(confirmed.textContent).toContain("one-to-one (1:1)");
    expect(confirmed.textContent).toContain("100%");
    expect(rejected.textContent).toContain("customers.id → orders.customer_id");
  });

  it("konfirmasi memanggil client dan memperbarui daftar (Req 7.4)", async () => {
    const user = userEvent.setup();
    const { client, onRelationsChange } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Konfirmasi" }));
    await waitFor(() => expect(onRelationsChange).toHaveBeenCalled());
    expect(client.confirm).toHaveBeenCalledWith("ws_1", "rel_1");
    const confirmed = await screen.findByLabelText("Relasi Terkonfirmasi");
    expect(confirmed.textContent).toContain("customers.id → orders.customer_id");
    // Kandidat kosong → grup hilang.
    expect(screen.queryByLabelText("Relasi Kandidat")).toBeNull();
  });

  it("tolak memanggil client.reject (Req 7.5)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Tolak" }));
    await waitFor(() => expect(client.reject).toHaveBeenCalledWith("ws_1", "rel_1"));
    expect(screen.getByLabelText("Relasi Ditolak")).toBeTruthy();
  });

  it("hapus relasi terkonfirmasi menghilangkannya dari daftar (Req 7.8)", async () => {
    const user = userEvent.setup();
    const { client } = renderPanel({
      relations: [relation({ status: "confirmed" })],
    });
    await user.click(screen.getByRole("button", { name: /Hapus relasi/ }));
    await waitFor(() => expect(client.remove).toHaveBeenCalledWith("ws_1", "rel_1"));
    await waitFor(() => expect(screen.queryByLabelText("Relasi Terkonfirmasi")).toBeNull());
  });

  it("deteksi ulang mengganti kandidat dengan hasil terbaru (Req 7.2)", async () => {
    const user = userEvent.setup();
    const fresh = relation({ id: "rel_9", from_column: "kode", overlap_pct: 80 });
    const { client } = renderPanel({
      client: memoryClient({ detect: vi.fn(async () => [fresh]) }),
    });
    await user.click(screen.getByRole("button", { name: "Deteksi ulang" }));
    await waitFor(() => expect(client.detect).toHaveBeenCalledWith("ws_1"));
    const candidate = await screen.findByLabelText("Relasi Kandidat");
    expect(candidate.textContent).toContain("customers.kode");
    expect(candidate.textContent).not.toContain("orders.customer_id →");
    expect(candidate.textContent).toContain("80%");
  });

  it("error aksi ditampilkan tanpa merusak daftar", async () => {
    const user = userEvent.setup();
    renderPanel({
      client: memoryClient({
        confirm: vi.fn(async () => {
          throw new Error("gagal konfirmasi");
        }),
      }),
    });
    await user.click(screen.getByRole("button", { name: "Konfirmasi" }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("gagal konfirmasi");
    expect(screen.getByLabelText("Relasi Kandidat")).toBeTruthy();
  });

  it("menampilkan pesan kosong bila belum ada relasi", () => {
    renderPanel({ relations: [] });
    expect(
      screen.getByText(/Belum ada relasi. Coba deteksi ulang/),
    ).toBeTruthy();
  });
});
