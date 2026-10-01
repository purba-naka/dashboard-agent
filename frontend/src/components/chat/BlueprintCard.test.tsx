// Feature: dashboard-studio-agent — kartu Blueprint, kartu semantik, review (Req 32.10, 37.5, 37.6, 37.8, 39.5).

import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatStreamHandlers, ChatStreamResult } from "@/lib/sse";
import type { ChatRequest, ChatSseEvent, DashboardBlueprint } from "@/lib/types";
import { BlueprintCard, revisionMessage } from "./BlueprintCard";
import { ChatPanel } from "./ChatPanel";
import type { ChatClient } from "./client";

afterEach(cleanup);

const blueprint: DashboardBlueprint = {
  brief: {
    purpose: "Pantau penjualan",
    audience: "Manajer",
    key_questions: [],
    kpis: [],
    sections: [],
    time_grain: null,
    assumptions: ["revenue dalam Rupiah"],
  },
  slots: [
    { slot_id: "kpi_rev", section: "kpi_row", purpose: "Total revenue", visual: "kpi", metrics: ["revenue"], dimension: null, layout: { x: 0, y: 0, w: 12, h: 2 }, cross_filter_column: null },
    { slot_id: "trend", section: "trend", purpose: "Tren bulanan", visual: "line", metrics: ["revenue"], dimension: "month", layout: { x: 0, y: 2, w: 8, h: 6 }, cross_filter_column: null },
    { slot_id: "region", section: "breakdown", purpose: "Per region", visual: "bar", metrics: ["revenue"], dimension: "region", layout: { x: 8, y: 2, w: 4, h: 6 }, cross_filter_column: "region" },
  ],
  default_filters: [],
};

describe("BlueprintCard", () => {
  it("menampilkan wireframe dan menyetujui slot terpilih", async () => {
    const onApprove = vi.fn();
    render(
      <BlueprintCard summary="Rancangan" blueprint={blueprint} approved={false} progress={{}} disabled={false} onApprove={onApprove} onRevise={vi.fn()} />,
    );
    expect(screen.getByRole("img", { name: "Wireframe 3 slot" })).toBeTruthy();
    expect(screen.getByText(/revenue dalam Rupiah/)).toBeTruthy();
    await userEvent.click(screen.getByRole("checkbox", { name: "Pilih slot region" }));
    await userEvent.click(screen.getByRole("button", { name: "Setujui terpilih (2)" }));
    expect(onApprove).toHaveBeenCalledWith(["kpi_rev", "trend"]);
  });

  it("edit slot mengaktifkan Revisi dan menonaktifkan Setujui", async () => {
    const onRevise = vi.fn();
    render(
      <BlueprintCard summary="R" blueprint={blueprint} approved={false} progress={{}} disabled={false} onApprove={vi.fn()} onRevise={onRevise} />,
    );
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Visual slot region" }), "pie");
    expect((screen.getByRole("button", { name: "Setujui semua" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Revisi" }));
    expect(onRevise.mock.calls[0][0]).toContain("Slot region: visual menjadi pie");
  });

  it("revisionMessage mencantumkan slot yang dihapus", () => {
    const msg = revisionMessage(blueprint.slots, {}, new Set(["kpi_rev"]));
    expect(msg).toContain("Hapus slot trend");
    expect(msg).toContain("Hapus slot region");
  });
});

function chatHarness() {
  let handlers: ChatStreamHandlers = {};
  let resolve: (r: ChatStreamResult) => void = () => {};
  const requests: ChatRequest[] = [];
  const client: ChatClient = {
    send: vi.fn((_ws: string, req: ChatRequest, h: ChatStreamHandlers) => {
      requests.push(req);
      handlers = h;
      return new Promise<ChatStreamResult>((r) => (resolve = r));
    }),
    stop: vi.fn(async () => undefined),
    history: vi.fn(async () => []),
    confirmRelation: vi.fn(),
    rejectRelation: vi.fn(),
    confirmSemantic: vi.fn(async () => ({})),
    rejectSemantic: vi.fn(async () => ({})),
    confirmAllSemantic: vi.fn(async () => ({ confirmed: 2 })),
  } as unknown as ChatClient;
  const emit = (...events: ChatSseEvent[]) => act(() => events.forEach((e) => handlers.onEvent?.(e)));
  const end = () => act(() => resolve({ runId: "r", sessionId: "s", outcome: "done" }));
  return { client, emit, end, requests };
}

const started: ChatSseEvent = { event: "run.started", data: { run_id: "r1", session_id: "s1" } };

describe("ChatPanel ekstensi v2", () => {
  it("persetujuan Blueprint mengirim selected_slot_ids lalu menampilkan progres", async () => {
    const h = chatHarness();
    render(<ChatPanel workspaceId="ws" client={h.client} />);
    await userEvent.type(screen.getByLabelText("Pesan"), "Buatkan dashboard{Enter}");
    await h.emit(started, {
      event: "approval.request",
      data: { proposal_id: "p1", summary: "Rancangan", themes: [], kind: "blueprint", blueprint },
    });
    await h.end();

    await userEvent.click(screen.getByRole("checkbox", { name: "Pilih slot trend" }));
    await userEvent.click(screen.getByRole("button", { name: "Setujui terpilih (2)" }));
    expect(h.requests[1].approval).toEqual({ proposal_id: "p1", selected_slot_ids: ["kpi_rev", "region"] });

    await h.emit(
      started,
      { event: "blueprint.progress", data: { blueprint_id: "b", slot_id: "kpi_rev", status: "building" } },
      { event: "blueprint.progress", data: { blueprint_id: "b", slot_id: "kpi_rev", status: "done", item_id: "i1" } },
      { event: "blueprint.progress", data: { blueprint_id: "b", slot_id: "region", status: "failed" } },
    );
    expect(screen.getByText("Selesai")).toBeTruthy();
    expect(screen.getByText("Gagal")).toBeTruthy();
    expect(screen.getByText("Disetujui (2 slot)")).toBeTruthy();
  });

  it("kartu pemahaman data: konfirmasi per entri dan semua", async () => {
    const h = chatHarness();
    render(<ChatPanel workspaceId="ws" client={h.client} />);
    await userEvent.type(screen.getByLabelText("Pesan"), "halo{Enter}");
    await h.emit(started, {
      event: "semantic.draft",
      data: {
        run_id: "run",
        domain: "retail_sales",
        summary: "",
        metrics: [
          { id: "m1", name: "revenue", label: "Revenue", expr: "SUM(amount)", status: "candidate" },
          { id: "m2", name: "orders", label: "Orders", expr: "COUNT(*)", status: "candidate" },
        ],
        columns_highlight: [],
        assumptions: ["amount dalam Rupiah"],
      },
    });
    await h.end();
    expect(screen.getByText("retail_sales")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Tolak metrik Revenue" }));
    expect(h.client.rejectSemantic).toHaveBeenCalledWith("ws", "m1");
    expect(await screen.findByText("Ditolak")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Konfirmasi semua" }));
    expect(h.client.confirmAllSemantic).toHaveBeenCalledWith("ws");
    expect(await screen.findByText("Dikonfirmasi")).toBeTruthy();
  });

  it("kartu review: Terapkan saran mengirim pesan chat", async () => {
    const h = chatHarness();
    render(<ChatPanel workspaceId="ws" client={h.client} />);
    await userEvent.type(screen.getByLabelText("Pesan"), "review{Enter}");
    await h.emit(started, {
      event: "review.findings",
      data: {
        findings: [
          { code: "KPI_NO_COMPARISON", severity: "info", item_ids: ["k1"], message: "KPI tanpa pembanding", suggestion: "Tambahkan pembanding" },
        ],
      },
    });
    await h.end();
    await userEvent.click(screen.getByRole("button", { name: "Terapkan saran KPI_NO_COMPARISON" }));
    expect(h.requests[1].message).toContain("Tambahkan pembanding");
  });
});
