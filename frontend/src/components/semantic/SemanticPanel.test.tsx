// Feature: dashboard-studio-agent — panel Model Semantik dan Design Brief (Req 31.7, 31.11, 32.10, 36.3).

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { DashboardSnapshot, PatchEvent, SemanticModelResponse } from "@/lib/types";
import { BriefPanel, briefFromForm } from "@/components/brief";
import { SemanticPanel } from "./SemanticPanel";
import type { SemanticClient } from "./client";

afterEach(cleanup);

const model = (): SemanticModelResponse => ({
  domain: "retail_sales",
  domain_confidence: 0.9,
  assumptions: ["amount dalam Rupiah"],
  semantic_version: 3,
  entries: [
    {
      id: "e1",
      kind: "metric",
      entry_key: "metric:revenue",
      status: "candidate",
      source: "auto",
      dataset_id: null,
      body: { name: "revenue", label: "Revenue", expr: "SUM(amount)", base_table: "sales" },
      valid: true,
      updated_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "e2",
      kind: "column",
      entry_key: "col:sales.amount",
      status: "confirmed",
      source: "user",
      dataset_id: "ds",
      body: { table: "sales", column: "amount", label: "Nilai", description: "Nilai transaksi" },
      valid: true,
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  draft_run: {
    id: "run",
    status: "done",
    discarded: [{ entry_key: "metric:bad", reason: "kolom tidak ada" }],
    started_at: "2026-01-01T00:00:00Z",
    finished_at: null,
  },
});

function client(over: Partial<SemanticClient> = {}): SemanticClient {
  return {
    load: vi.fn(async () => model()),
    update: vi.fn(async () => ({}) as never),
    confirm: vi.fn(async () => ({}) as never),
    reject: vi.fn(async () => ({}) as never),
    confirmAll: vi.fn(async () => ({ confirmed: 1 })),
    redraft: vi.fn(async () => ({ scheduled: true })),
    exportYaml: vi.fn(async () => "metrics: []\n"),
    importYaml: vi.fn(async () => model()),
    ...over,
  };
}

describe("SemanticPanel", () => {
  it("menampilkan entri per jenis, domain, dan entri yang dibuang", async () => {
    render(<SemanticPanel workspaceId="ws" client={client()} />);
    expect(await screen.findByText("Revenue")).toBeTruthy();
    expect(screen.getByText("SUM(amount)")).toBeTruthy();
    expect(screen.getByText("retail_sales")).toBeTruthy();
    expect(screen.getByText("Diedit pengguna")).toBeTruthy();
    expect(screen.getByText("1 usulan dibuang drafter")).toBeTruthy();
  });

  it("filter status, konfirmasi, dan konfirmasi semua", async () => {
    const c = client();
    render(<SemanticPanel workspaceId="ws" client={c} />);
    await screen.findByText("Revenue");
    await userEvent.click(screen.getByRole("radio", { name: "Dikonfirmasi" }));
    expect(screen.queryByText("SUM(amount)")).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Semua" }));
    await userEvent.click(screen.getByRole("button", { name: "Konfirmasi Revenue" }));
    expect(c.confirm).toHaveBeenCalledWith("ws", "e1");
    await userEvent.click(screen.getByRole("button", { name: "Konfirmasi semua (1)" }));
    expect(c.confirmAll).toHaveBeenCalledWith("ws");
  });

  it("edit inline mengirim body baru (Req 31.7)", async () => {
    const c = client();
    render(<SemanticPanel workspaceId="ws" client={c} />);
    await userEvent.click(await screen.findByRole("button", { name: "Edit Revenue" }));
    const label = screen.getByLabelText("Label");
    await userEvent.clear(label);
    await userEvent.type(label, "Omzet");
    await userEvent.click(screen.getByRole("button", { name: "Simpan" }));
    expect(c.update).toHaveBeenCalledWith("ws", "e1", expect.objectContaining({ label: "Omzet", expr: "SUM(amount)" }));
  });

  it("impor YAML yang ditolak menampilkan setiap masalah (Req 31.11)", async () => {
    const err = new ApiError(422, "SEMANTIC_IMPORT_INVALID", "invalid", {
      issues: [{ path: "metrics[0]", entry_key: "metric:broken", reason: "kolom tidak ada" }],
    });
    const c = client({ importYaml: vi.fn(async () => Promise.reject(err)) });
    render(<SemanticPanel workspaceId="ws" client={c} />);
    await screen.findByText("Revenue");
    const file = new File(["metrics: []"], "s.yaml", { type: "text/yaml" });
    await userEvent.upload(screen.getByLabelText("Berkas YAML model semantik"), file);
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("metric:broken")).toBeTruthy();
    expect(alert.textContent).toContain("tidak ada yang diubah");
  });
});

const snapshot = (): DashboardSnapshot => ({
  id: "db",
  title: "D",
  version: 4,
  content: {
    title: "D",
    items: {},
    layout: {},
    global_filters: [],
    brief: {
      purpose: "Lama",
      audience: "",
      key_questions: [],
      kpis: [{ metric: "revenue", compare: "previous_period" }],
      sections: ["kpi_row"],
      time_grain: null,
      assumptions: [],
    },
  },
  can_undo: false,
  can_redo: false,
  item_status: {},
});

describe("BriefPanel", () => {
  it("menyimpan brief lewat set_brief dengan KPI dipertahankan (Req 36.3)", async () => {
    const command = vi.fn(async () => ({}) as PatchEvent);
    render(<BriefPanel snapshot={snapshot()} client={{ command }} />);
    await userEvent.click(screen.getByText(/Design Brief: Lama/));
    const purpose = screen.getByLabelText("Tujuan");
    await userEvent.clear(purpose);
    await userEvent.type(purpose, "Pantau margin");
    await userEvent.type(screen.getByLabelText(/Pertanyaan bisnis kunci/), "Apakah margin naik?");
    await userEvent.click(screen.getByRole("button", { name: "Simpan brief" }));
    expect(command).toHaveBeenCalledWith("db", 4, {
      type: "set_brief",
      brief: expect.objectContaining({
        purpose: "Pantau margin",
        key_questions: ["Apakah margin naik?"],
        kpis: [{ metric: "revenue", compare: "previous_period" }],
        sections: ["kpi_row"],
      }),
    });
    expect(await screen.findByText("Tersimpan")).toBeTruthy();
  });

  it("briefFromForm membuang baris kosong", () => {
    const b = briefFromForm(null, { purpose: " x ", audience: "", questions: "a\n\n b ", assumptions: "", grain: "month" });
    expect(b).toMatchObject({ purpose: "x", key_questions: ["a", "b"], time_grain: "month", kpis: [] });
  });
});
