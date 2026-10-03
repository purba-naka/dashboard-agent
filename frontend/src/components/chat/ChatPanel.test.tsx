import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatStreamHandlers, ChatStreamResult } from "@/lib/sse";
import type { ChatRequest, ChatSseEvent, Relation } from "@/lib/types";
import { ChatPanel, type ChatPanelHandle } from "./ChatPanel";
import { mapHistory, type ChatClient } from "./client";

afterEach(cleanup);

const relation = (over: Partial<Relation> = {}): Relation => ({
  id: "rel_1",
  workspace_id: "ws",
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

/** Klien in-memory: test mendorong event ke run aktif lalu menutupnya. */
function fakeClient() {
  const requests: ChatRequest[] = [];
  let handlers: ChatStreamHandlers = {};
  let finish: (r: ChatStreamResult) => void = () => {};
  const client: ChatClient = {
    send: vi.fn((_ws, req, h) => {
      requests.push(req);
      handlers = h;
      return new Promise<ChatStreamResult>((resolve) => (finish = resolve));
    }),
    stop: vi.fn(async () => {}),
    history: vi.fn(async () => []),
    confirmRelation: vi.fn(async (_ws, id) => relation({ id, status: "confirmed" })),
    rejectRelation: vi.fn(async (_ws, id) => relation({ id, status: "rejected" })),
  };
  const emit = (...events: ChatSseEvent[]) =>
    act(() => events.forEach((e) => handlers.onEvent?.(e)));
  const end = (outcome: ChatStreamResult["outcome"] = "done") =>
    act(async () => finish({ runId: "run_1", sessionId: "s_1", outcome }));
  return { client, requests, emit, end };
}

const started: ChatSseEvent = { event: "run.started", data: { run_id: "run_1", session_id: "s_1" } };

describe("ChatPanel", () => {
  it("pesan pertama tetap tampil saat parent meneruskan sesi baru dari run.started", async () => {
    const user = userEvent.setup();
    const { client, emit } = fakeClient();
    // Riwayat backend belum memuat pesan yang sedang diproses.
    client.history = vi.fn(async () => []);
    function Host() {
      const [sid, setSid] = useState<string | null>(null);
      return <ChatPanel workspaceId="ws" client={client} sessionId={sid} onSessionChange={setSid} />;
    }
    render(<Host />);

    await user.type(screen.getByLabelText("Pesan"), "Total penjualan?{Enter}");
    await emit(started, { event: "text.delta", data: { agent: "root", text: "Sebentar" } });
    await act(async () => {});

    expect(client.history).not.toHaveBeenCalled();
    expect(screen.getByText("Total penjualan?")).toBeTruthy();
    expect(screen.getByText("Sebentar")).toBeTruthy();
    expect(screen.getByRole("status")).toBeTruthy();

    // Enter saat run berjalan tidak membuang draft.
    await user.type(screen.getByLabelText("Pesan"), "lanjut{Enter}");
    expect((screen.getByLabelText("Pesan") as HTMLTextAreaElement).value).toBe("lanjut");
  });

  it("menampilkan agent aktif, tool berjalan, dan teks streaming (Req 16.4)", async () => {
    const user = userEvent.setup();
    const { client, emit, end } = fakeClient();
    render(<ChatPanel workspaceId="ws" client={client} />);

    await user.type(screen.getByLabelText("Pesan"), "Total penjualan?{Enter}");
    await emit(
      started,
      { event: "agent.active", data: { agent: "query" } },
      { event: "tool.call", data: { agent: "query", tool: "run_sql", args_summary: "SUM(amount)" } },
    );
    const status = screen.getByRole("status");
    expect(status.textContent).toMatch(/Agent Query aktif/);
    expect(status.textContent).toMatch(/menjalankan run_sql/);

    await emit(
      { event: "tool.result", data: { agent: "query", tool: "run_sql", ok: true, summary: "1 baris" } },
      { event: "text.delta", data: { agent: "root", text: "Total " } },
      { event: "text.delta", data: { agent: "root", text: "Rp 10 jt" } },
    );
    expect(screen.getByRole("status").textContent).not.toMatch(/run_sql/);
    expect(screen.getByText("Total Rp 10 jt")).toBeTruthy();

    await emit({ event: "run.done", data: { run_id: "run_1" } });
    await end();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("thinking tampil di dropdown terpisah dari jawaban", async () => {
    const user = userEvent.setup();
    const { client, emit } = fakeClient();
    render(<ChatPanel workspaceId="ws" client={client} />);

    await user.type(screen.getByLabelText("Pesan"), "halo{Enter}");
    await emit(
      started,
      { event: "thought.delta", data: { agent: "root", text: "User menyapa. " } },
      { event: "thought.delta", data: { agent: "root", text: "Balas singkat." } },
    );
    const details = screen.getByText("Sedang berpikir").closest("details")!;
    expect(details.open).toBe(true);

    await emit({ event: "text.delta", data: { agent: "root", text: "Halo!" } });
    const done = screen.getByText("Proses berpikir").closest("details")!;
    expect(done.textContent).toMatch(/User menyapa\. Balas singkat\./);
    expect(screen.getByText("Halo!").closest("details")).toBeNull();
    // Jawaban mulai → thinking otomatis tertutup, tetap bisa dibuka/ditutup.
    expect(done.open).toBe(false);
    await user.click(screen.getByText("Proses berpikir"));
    expect(done.open).toBe(true);
    await user.click(screen.getByText("Proses berpikir"));
    expect(done.open).toBe(false);
  });

  it("jawaban agent dirender sebagai Markdown, HTML mentah tidak dieksekusi", async () => {
    const user = userEvent.setup();
    const { client, emit } = fakeClient();
    const { container } = render(<ChatPanel workspaceId="ws" client={client} />);

    await user.type(screen.getByLabelText("Pesan"), "ringkas{Enter}");
    await emit(started, {
      event: "text.delta",
      data: {
        agent: "root",
        text: "**Total** naik\n\n- Jawa\n- Bali\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<img src=x onerror=alert(1)>",
      },
    });
    expect(screen.getByText("Total").tagName).toBe("STRONG");
    const md = screen.getByText("Total").closest("div")!;
    expect(md.querySelectorAll("li").length).toBe(2);
    expect(md.querySelector("table td")?.textContent).toBe("1");
    expect(container.querySelector("img")).toBeNull();
  });

  it("kartu kandidat relasi: Konfirmasi memanggil client dan onRelationsChanged (Req 7.3)", async () => {
    const user = userEvent.setup();
    const { client, emit } = fakeClient();
    const onRelationsChanged = vi.fn();
    const handle = createRef<ChatPanelHandle>();
    render(<ChatPanel workspaceId="ws" client={client} onRelationsChanged={onRelationsChanged} ref={handle} />);

    act(() => void handle.current!.send("Dataset baru selesai diunggah"));
    await emit(
      started,
      { event: "profile.summary", data: { dataset_id: "ds_o", summary: "1.000 baris, 5 kolom" } },
      { event: "relation.candidates", data: [relation(), relation({ id: "rel_2", to_column: "buyer_id" })] },
    );
    expect(screen.getByLabelText("Ringkasan profil dataset").textContent).toMatch(/1.000 baris/);

    const card = screen.getByRole("region", { name: "Kandidat relasi" });
    await user.click(within(card).getByRole("button", { name: "Konfirmasi customers.id → orders.customer_id" }));
    expect(client.confirmRelation).toHaveBeenCalledWith("ws", "rel_1");
    expect(onRelationsChanged).toHaveBeenCalledTimes(1);
    expect(within(card).getByText("Dikonfirmasi")).toBeTruthy();

    await user.click(within(card).getByRole("button", { name: "Tolak customers.id → orders.buyer_id" }));
    expect(client.rejectRelation).toHaveBeenCalledWith("ws", "rel_2");
    expect(within(card).getByText("Ditolak")).toBeTruthy();
  });

  it("Setujui mengirim approval.proposal_id dengan session_id (Req 21.2)", async () => {
    const user = userEvent.setup();
    const { client, requests, emit, end } = fakeClient();
    render(<ChatPanel workspaceId="ws" client={client} />);

    await user.type(screen.getByLabelText("Pesan"), "Buatkan draft{Enter}");
    await emit(started, {
      event: "approval.request",
      data: { proposal_id: "prop_9", summary: "Tambah 3 chart", themes: ["Tren bulanan"] },
    });
    await emit({ event: "run.done", data: { run_id: "run_1" } });
    await end();

    await user.click(screen.getByRole("button", { name: "Setujui" }));
    expect(requests[1]).toMatchObject({ session_id: "s_1", approval: { proposal_id: "prop_9" } });
    expect(screen.queryByRole("button", { name: "Setujui" })).toBeNull();
  });

  it("Stop memanggil stop(run_id) dan menampilkan penghentian (Req 16.6)", async () => {
    const user = userEvent.setup();
    const { client, emit, end } = fakeClient();
    render(<ChatPanel workspaceId="ws" client={client} />);

    await user.type(screen.getByLabelText("Pesan"), "Analisis{Enter}");
    await emit(started);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(client.stop).toHaveBeenCalledWith("ws", "run_1");

    await emit({ event: "run.stopped", data: { run_id: "run_1" } });
    await end("stopped");
    expect(screen.getByText("Proses agent dihentikan.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Kirim" })).toBeTruthy();
  });

  it("meneruskan patch.applied ke onPatch dan menampilkan error agent (Req 8.5)", async () => {
    const user = userEvent.setup();
    const { client, emit } = fakeClient();
    const onPatch = vi.fn();
    render(<ChatPanel workspaceId="ws" client={client} onPatch={onPatch} />);

    await user.type(screen.getByLabelText("Pesan"), "x{Enter}");
    const patch = { dashboard_id: "d", version: 2, ops: [] } as never;
    await emit(started, { event: "patch.applied", data: patch }, {
      event: "error",
      data: { code: "SQL_FAILED", message: "Query gagal setelah 3 percobaan", agent: "query" },
    });
    expect(onPatch).toHaveBeenCalledWith(patch);
    expect(screen.getByRole("alert").textContent).toBe("Query: Query gagal setelah 3 percobaan");
  });

  it("memuat riwayat sesi", async () => {
    const { client } = fakeClient();
    client.history = vi.fn(async () => mapHistory({ messages: [
      { role: "user", content: "Halo" },
      { role: "model", parts: [{ text: "Hai" }] },
    ] }));
    render(<ChatPanel workspaceId="ws" sessionId="s_1" client={client} />);
    expect(await screen.findByText("Halo")).toBeTruthy();
    expect(screen.getByText("Hai")).toBeTruthy();
  });
});
