// @vitest-environment node
/**
 * Test parser SSE, stream chat, dan langganan event workspace (Req 16.3, 16.5).
 *
 * - Parser: event yang terpotong di sembarang posisi antar-chunk (termasuk di
 *   antara `\r` dan `\n`, dan di tengah karakter UTF-8 multi-byte pada stream
 *   chat) menghasilkan event yang sama dengan stream utuh.
 * - Reconnect: setelah koneksi workspace pulih, Dashboard diselaraskan lewat
 *   `GET /dashboards/{id}/patches?since_version=` dan patch diterapkan
 *   berurutan; replay event duplikat diabaikan.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  dashboardReducer,
  initialDashboardState,
  resyncDashboard,
  type DashboardAction,
  type DashboardState,
  type ResyncFetchers,
} from "./dashboard-state";
import {
  connectWorkspaceEvents,
  createSseParser,
  streamChat,
  type EventSourceLike,
  type SseMessage,
  type WorkspaceEventHandlers,
} from "./sse";
import type {
  ChatSseEvent,
  DashboardContent,
  DashboardSnapshot,
  PatchEvent,
  PatchesSinceResponse,
  SseEnvelope,
  WorkspaceSseEvent,
} from "./types";

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function parseAll(chunks: readonly string[]): SseMessage[] {
  const out: SseMessage[] = [];
  const parser = createSseParser((m) => out.push(m));
  for (const c of chunks) parser.feed(c);
  return out;
}

/** Semua cara memotong `text` menjadi dua chunk. */
function allTwoWaySplits(text: string): string[][] {
  const splits: string[][] = [];
  for (let i = 0; i <= text.length; i++) splits.push([text.slice(0, i), text.slice(i)]);
  return splits;
}

const encoder = new TextEncoder();

/** Potong bytes UTF-8 `text` menjadi chunk berukuran `size` (boleh memotong karakter). */
function byteChunks(text: string, size: number): Uint8Array[] {
  const bytes = encoder.encode(text);
  const chunks: Uint8Array[] = [];
  for (let i = 0; i < bytes.length; i += size) chunks.push(bytes.slice(i, i + size));
  return chunks;
}

function streamResponse(chunks: readonly Uint8Array[], init: ResponseInit = {}): Response {
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(chunks[i++]);
      else controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
    ...init,
  });
}

function sse(event: string, data: unknown, id?: string): string {
  return `${id !== undefined ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

// ---------------------------------------------------------------------------
// createSseParser
// ---------------------------------------------------------------------------

describe("createSseParser", () => {
  const STREAM =
    ": connected\r\n" +
    "id: 1\r\n" +
    "event: patch.applied\r\n" +
    'data: {"version":2}\r\n' +
    "\r\n" +
    ": ping\r\n" +
    "event: job.progress\r\n" +
    'data: {"job_id":"j1",\r\n' +
    'data: "progress":50}\r\n' +
    "\r\n" +
    "id: 2\r\n" +
    "event: text.delta\r\n" +
    'data: {"text":"halo é 👋"}\r\n' +
    "\r\n";

  const EXPECTED: SseMessage[] = [
    { event: "patch.applied", data: '{"version":2}', lastEventId: "1", hasId: true },
    {
      event: "job.progress",
      data: '{"job_id":"j1",\n"progress":50}',
      lastEventId: "1",
      hasId: false,
    },
    { event: "text.delta", data: '{"text":"halo é 👋"}', lastEventId: "2", hasId: true },
  ];

  it("mem-parse stream utuh dalam satu chunk", () => {
    expect(parseAll([STREAM])).toEqual(EXPECTED);
  });

  it("hasil sama untuk setiap posisi potong dua-chunk (termasuk di antara \\r dan \\n)", () => {
    for (const chunks of allTwoWaySplits(STREAM)) {
      expect(parseAll(chunks), JSON.stringify(chunks)).toEqual(EXPECTED);
    }
  });

  it("hasil sama bila stream dikirim satu karakter per chunk", () => {
    expect(parseAll([...STREAM])).toEqual(EXPECTED);
  });

  it("`\\r` di akhir chunk lalu `\\n` di awal chunk berikutnya dihitung satu akhir baris", () => {
    // Bila `\n` dianggap baris kosong, "a" akan di-dispatch sebagai event terpisah.
    expect(parseAll(["data: a\r", "\ndata: b\r", "\n\r", "\n"])).toEqual([
      { event: "message", data: "a\nb", lastEventId: "", hasId: false },
    ]);
  });

  it("akhir baris LF, CR, dan CRLF setara", () => {
    const lines = ["event: run.done", 'data: {"run_id":"r1"}', "", "data: x", ""];
    const lf = parseAll([lines.join("\n") + "\n"]);
    const cr = parseAll([lines.join("\r") + "\r"]);
    const crlf = parseAll([lines.join("\r\n") + "\r\n"]);
    expect(lf).toHaveLength(2);
    expect(cr).toEqual(lf);
    expect(crlf).toEqual(lf);
  });

  it("event tidak di-dispatch sebelum baris kosong penutup tiba", () => {
    const out: SseMessage[] = [];
    const parser = createSseParser((m) => out.push(m));
    parser.feed("event: tool.call\ndata: {");
    parser.feed('"tool":"run_sql"}\n');
    expect(out).toEqual([]);
    parser.feed("\n");
    expect(out).toEqual([
      { event: "tool.call", data: '{"tool":"run_sql"}', lastEventId: "", hasId: false },
    ]);
  });

  it("komentar, field tak dikenal, dan blok tanpa data tidak menghasilkan event", () => {
    const comments: string[] = [];
    const out = [] as SseMessage[];
    const parser = createSseParser((m) => out.push(m), { onComment: (t) => comments.push(t) });
    parser.feed(": ping\n:raw\nfoo: bar\nevent: job.done\n\n");
    expect(out).toEqual([]);
    expect(comments).toEqual(["ping", "raw"]);
  });

  it("mem-parse `retry`, mengabaikan `id` ber-NUL, dan hanya membuang satu spasi setelah colon", () => {
    const retries: number[] = [];
    const out: SseMessage[] = [];
    const parser = createSseParser((m) => out.push(m), { onRetry: (ms) => retries.push(ms) });
    parser.feed("retry: 1500\nretry: abc\nid: 5\n\nid: 6\u00007\ndata:  x\ndata\n\n");
    expect(retries).toEqual([1500]);
    expect(parser.retry).toBe(1500);
    expect(out).toEqual([{ event: "message", data: " x\n", lastEventId: "5", hasId: false }]);
    expect(parser.lastEventId).toBe("5");
  });

  it("BOM hanya dibuang di awal stream", () => {
    expect(parseAll(["\uFEFFdata: a\n\n"])).toEqual([
      { event: "message", data: "a", lastEventId: "", hasId: false },
    ]);
    // Di tengah stream BOM menjadi bagian nama field (`\uFEFFdata` tak dikenal → diabaikan).
    expect(parseAll(["data: a\n\n", "\uFEFFdata: b\n\n"]).map((m) => m.data)).toEqual(["a"]);
  });

  it("end() membuang event yang belum lengkap tetapi mempertahankan lastEventId", () => {
    const out: SseMessage[] = [];
    const parser = createSseParser((m) => out.push(m));
    parser.feed("id: 9\ndata: a\n\nid: 10\ndata: potong");
    parser.end();
    expect(out.map((m) => m.data)).toEqual(["a"]);
    // `id: 10` sudah diproses sebagai baris lengkap sebelum stream berakhir.
    expect(parser.lastEventId).toBe("10");
    parser.feed("data: b\n\n");
    expect(out[1]).toEqual({ event: "message", data: "b", lastEventId: "10", hasId: false });
  });
});

// ---------------------------------------------------------------------------
// streamChat
// ---------------------------------------------------------------------------

describe("streamChat", () => {
  const CHAT_STREAM =
    ": connected\n\n" +
    sse("run.started", { run_id: "run_1", session_id: "sess_1" }) +
    sse("agent.active", { agent: "root" }) +
    sse("text.delta", { agent: "root", text: "Pendapatan naik 12% — café ☕ 👍" }) +
    sse("tool.call", { agent: "query", tool: "run_sql", args_summary: "SELECT …" }) +
    sse("tool.result", { agent: "query", tool: "run_sql", ok: true, summary: "12 baris" }) +
    "event: mystery\ndata: {}\n\n" +
    "event: error\ndata: {bukan json\n\n" +
    sse("error", { agent: "chart", code: "X", message: "gagal" }) +
    sse("run.done", { run_id: "run_1" });

  const EXPECTED_TYPES = [
    "run.started",
    "agent.active",
    "text.delta",
    "tool.call",
    "tool.result",
    "error",
    "run.done",
  ];

  async function run(chunks: Uint8Array[]) {
    const events: SseEnvelope<ChatSseEvent>[] = [];
    const unhandled: string[] = [];
    const fetchMock = vi.fn(async () => streamResponse(chunks));
    const result = await streamChat(
      "ws_1",
      { message: "halo", session_id: "sess_1" },
      {
        onEvent: (e) => events.push(e),
        onUnhandled: (f) => unhandled.push(`${f.reason}:${f.message.event}`),
      },
      { fetch: fetchMock as unknown as typeof fetch },
    );
    return { result, events, unhandled, fetchMock };
  }

  it.each([1, 2, 3, 7, 64, 100_000])(
    "event terpisah per jenis dengan chunk %i byte (memotong karakter multi-byte)",
    async (size) => {
      const { result, events, unhandled } = await run(byteChunks(CHAT_STREAM, size));
      expect(events.map((e) => e.event)).toEqual(EXPECTED_TYPES);
      expect(events[2].data).toEqual({ agent: "root", text: "Pendapatan naik 12% — café ☕ 👍" });
      expect(unhandled).toEqual(["unknown_event:mystery", "invalid_json:error"]);
      expect(result).toEqual({ runId: "run_1", sessionId: "sess_1", outcome: "done" });
    },
  );

  it("mengirim POST JSON ke URL chat dengan Accept text/event-stream", async () => {
    const { fetchMock } = await run(byteChunks(CHAT_STREAM, 1024));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/workspaces\/ws_1\/chat$/);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ message: "halo", session_id: "sess_1" });
    expect((init.headers as Record<string, string>).Accept).toBe("text/event-stream");
  });

  it("outcome `stopped` untuk run.stopped dan `ended` bila stream berakhir tanpa penutup", async () => {
    const stopped = await run(
      byteChunks(sse("run.started", { run_id: "r", session_id: "s" }) + sse("run.stopped", { run_id: "r" }), 5),
    );
    expect(stopped.result.outcome).toBe("stopped");

    const ended = await run(
      byteChunks(sse("text.delta", { agent: "root", text: "a" }) + "event: text.delta\ndata: {", 4),
    );
    expect(ended.events).toHaveLength(1); // event terakhir terpotong dibuang
    expect(ended.result).toEqual({ runId: null, sessionId: null, outcome: "ended" });
  });

  it("HTTP non-2xx menjadi ApiError dari envelope", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "Tidak ada", details: {} } }), {
          status: 404,
        }),
    );
    const err = await streamChat("ws_1", { message: "x" }, {}, {
      fetch: fetchMock as unknown as typeof fetch,
    }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "NOT_FOUND" });
  });

  it("stream putus di tengah menjadi ApiError NETWORK_ERROR setelah event yang sudah lengkap", async () => {
    const first = encoder.encode(sse("agent.active", { agent: "root" }) + "event: text.delta\n");
    let sent = false;
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (!sent) {
          sent = true;
          controller.enqueue(first);
        } else {
          controller.error(new TypeError("network reset"));
        }
      },
    });
    const events: string[] = [];
    const err = await streamChat("ws_1", { message: "x" }, { onEvent: (e) => events.push(e.event) }, {
      fetch: (async () => new Response(body, { status: 200 })) as unknown as typeof fetch,
    }).catch((e: unknown) => e);
    expect(events).toEqual(["agent.active"]);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 0, code: "NETWORK_ERROR" });
  });
});

// ---------------------------------------------------------------------------
// connectWorkspaceEvents + resync Dashboard
// ---------------------------------------------------------------------------

const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 2;

class FakeEventSource implements EventSourceLike {
  readyState = CONNECTING;
  onopen: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  closed = false;
  private listeners = new Map<string, Set<(ev: MessageEvent) => void>>();

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  close(): void {
    this.readyState = CLOSED;
    this.closed = true;
  }

  // --- kontrol test ---

  open(): void {
    this.readyState = OPEN;
    this.onopen?.(new Event("open"));
  }

  /** Kirim event bernama; `lastEventId` meniru buffer id `EventSource`. */
  emit(type: string, data: unknown, lastEventId = ""): void {
    const ev = {
      type,
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId,
    } as MessageEvent;
    for (const l of this.listeners.get(type) ?? []) l(ev);
  }

  /** Koneksi putus; `CONNECTING` = browser reconnect sendiri, `CLOSED` = menyerah. */
  drop(state: typeof CONNECTING | typeof CLOSED): void {
    this.readyState = state;
    this.onerror?.(new Event("error"));
  }
}

const DASH_ID = "dash_1";

function content(title: string): DashboardContent {
  return { title, items: {}, layout: {}, global_filters: [] };
}

function snapshot(title: string, version: number): DashboardSnapshot {
  return {
    id: DASH_ID,
    title,
    version,
    content: content(title),
    can_undo: false,
    can_redo: false,
    item_status: {},
  };
}

/** Patch ganti judul `T{v-1}` → `T{v}` pada versi `v`. */
function titlePatch(version: number): PatchEvent {
  return {
    id: `patch_${version}`,
    dashboard_id: DASH_ID,
    version,
    base_version: version - 1,
    source: "agent",
    kind: "normal",
    target_patch_id: null,
    ops: [{ op: "set_title", before: `T${version - 1}`, after: `T${version}` }],
    inverse_ops: [{ op: "set_title", before: `T${version}`, after: `T${version - 1}` }],
    created_at: "2024-01-01T00:00:00Z",
  };
}

/**
 * Store minimal yang merangkai SSE workspace ke reducer Dashboard seperti
 * halaman Workspace: `patch.applied` → `patchReceived`, reconnect/`resync`/
 * celah versi → `resyncDashboard` via REST (Req 16.5).
 */
function createHarness(fetchers: ResyncFetchers, initial: DashboardSnapshot) {
  let state: DashboardState = dashboardReducer(initialDashboardState, {
    type: "snapshotLoaded",
    snapshot: initial,
  });
  const actions: DashboardAction[] = [];
  const dispatch = (action: DashboardAction) => {
    actions.push(action);
    state = dashboardReducer(state, action);
  };
  let pending: Promise<void> = Promise.resolve();
  const resync = () => {
    pending = pending.then(async () => {
      if (!state.needsResync) return;
      const action = await resyncDashboard(state, fetchers);
      if (action) dispatch(action);
    });
    return pending;
  };

  const events: SseEnvelope<WorkspaceSseEvent>[] = [];
  const handlers: WorkspaceEventHandlers = {
    onEvent: (event) => {
      events.push(event);
      if (event.event === "patch.applied") {
        dispatch({ type: "patchReceived", patch: event.data });
        if (state.needsResync) void resync();
      }
    },
    onReconnect: () => {
      dispatch({ type: "resyncRequested" });
      void resync();
    },
    onResync: () => {
      dispatch({ type: "resyncRequested" });
      void resync();
    },
  };
  return {
    handlers,
    events,
    actions,
    get state() {
      return state;
    },
    settle: () => pending,
  };
}

describe("connectWorkspaceEvents", () => {
  let sources: FakeEventSource[];
  const factory = (url: string) => {
    const es = new FakeEventSource(url);
    sources.push(es);
    return es;
  };
  const latest = () => sources[sources.length - 1];

  beforeEach(() => {
    sources = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("mendekode event yang dikenal, melacak Last-Event-ID, dan melaporkan event tak dikenal", () => {
    const events: string[] = [];
    const unhandled: string[] = [];
    const statuses: string[] = [];
    const sub = connectWorkspaceEvents(
      "ws 1",
      {
        onEvent: (e) => events.push(`${e.event}#${e.id ?? "-"}`),
        onUnhandled: (f) => unhandled.push(`${f.reason}:${f.message.event}`),
        onStatusChange: (s) => statuses.push(s),
      },
      { eventSourceFactory: factory },
    );
    expect(sources).toHaveLength(1);
    expect(latest().url).toMatch(/\/api\/workspaces\/ws%201\/events$/);

    latest().open();
    latest().emit("job.progress", { job_id: "j1", progress: 40 }, "3");
    latest().emit("message", { x: 1 }, "4");
    latest().emit("job.done", "{rusak", "5");
    expect(events).toEqual(["job.progress#3"]);
    expect(unhandled).toEqual(["unknown_event:message", "invalid_json:job.done"]);
    expect(sub.lastEventId).toBe("5");
    expect(statuses).toEqual(["open"]);

    sub.close();
    expect(latest().closed).toBe(true);
    expect(sub.status).toBe("closed");
  });

  it("reconnect otomatis browser memanggil onReconnect dengan id terakhir", () => {
    const reconnects: Array<string | null> = [];
    const errors: Array<number | null> = [];
    const sub = connectWorkspaceEvents(
      "ws_1",
      {
        onReconnect: (i) => reconnects.push(i.lastEventId),
        onError: (i) => errors.push(i.retryInMs),
      },
      { eventSourceFactory: factory },
    );
    latest().open();
    latest().emit("job.progress", { job_id: "j", progress: 1 }, "11");
    expect(reconnects).toEqual([]); // koneksi pertama bukan reconnect

    latest().drop(CONNECTING);
    expect(sub.status).toBe("reconnecting");
    expect(errors).toEqual([null]);
    expect(sources).toHaveLength(1); // browser yang menyambung ulang

    latest().open();
    expect(sub.status).toBe("open");
    expect(reconnects).toEqual(["11"]);
    sub.close();
  });

  it("koneksi CLOSED dibuat ulang dengan backoff eksponensial dan ?last_event_id=", () => {
    const errors: Array<number | null> = [];
    const reconnects: Array<string | null> = [];
    const sub = connectWorkspaceEvents(
      "ws_1",
      { onError: (i) => errors.push(i.retryInMs), onReconnect: (i) => reconnects.push(i.lastEventId) },
      { eventSourceFactory: factory, initialBackoffMs: 1000, maxBackoffMs: 3000 },
    );
    latest().open();
    latest().emit("job.progress", { job_id: "j", progress: 1 }, "7");

    latest().drop(CLOSED);
    expect(sources[0].closed).toBe(true);
    expect(errors).toEqual([1000]);
    vi.advanceTimersByTime(999);
    expect(sources).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sources).toHaveLength(2);
    expect(latest().url).toMatch(/\/api\/workspaces\/ws_1\/events\?last_event_id=7$/);

    // Gagal lagi sebelum terbuka → backoff berlipat, dibatasi maxBackoffMs.
    latest().drop(CLOSED);
    latest().drop(CLOSED); // error dari source lama diabaikan
    vi.advanceTimersByTime(2000);
    expect(sources).toHaveLength(3);
    latest().drop(CLOSED);
    expect(errors).toEqual([1000, 2000, 3000]);
    vi.advanceTimersByTime(3000);
    expect(sources).toHaveLength(4);

    latest().open();
    expect(reconnects).toEqual(["7"]);
    // Backoff di-reset setelah terbuka.
    latest().drop(CLOSED);
    expect(errors.at(-1)).toBe(1000);

    sub.close();
    vi.advanceTimersByTime(10_000);
    expect(sources).toHaveLength(4); // tidak ada reconnect setelah close
  });

  it("opsi lastEventId dipakai sebagai kursor replay koneksi pertama", () => {
    const sub = connectWorkspaceEvents("ws_1", {}, { eventSourceFactory: factory, lastEventId: "42" });
    expect(latest().url).toMatch(/\?last_event_id=42$/);
    expect(sub.lastEventId).toBe("42");
    sub();
    expect(sub.status).toBe("closed");
  });

  describe("resync patch saat reconnect (Req 16.5)", () => {
    it("menarik patch sejak versi terakhir, menerapkan berurutan, dan mengabaikan replay duplikat", async () => {
      const getPatchesSince = vi.fn(
        async (): Promise<PatchesSinceResponse> => ({
          // Urutan acak: reducer wajib menerapkan berurutan versi.
          patches: [titlePatch(5), titlePatch(3), titlePatch(4)],
          version: 5,
        }),
      );
      const getDashboard = vi.fn(async () => snapshot("tidak dipakai", 0));
      const h = createHarness({ getPatchesSince, getDashboard }, snapshot("T1", 1));
      const sub = connectWorkspaceEvents("ws_1", h.handlers, { eventSourceFactory: factory });

      latest().open();
      latest().emit("patch.applied", titlePatch(2), "20");
      expect(h.state.snapshot?.version).toBe(2);

      // Putus; server menerapkan v3..v5 selama terputus. Browser menyambung ulang.
      latest().drop(CONNECTING);
      latest().open();
      await h.settle();

      expect(getPatchesSince).toHaveBeenCalledTimes(1);
      expect(getPatchesSince).toHaveBeenCalledWith(DASH_ID, 2);
      expect(getDashboard).not.toHaveBeenCalled();
      expect(h.state.snapshot?.version).toBe(5);
      expect(h.state.snapshot?.content.title).toBe("T5");
      expect(h.state.needsResync).toBe(false);
      expect(h.state.lastPatchId).toBe("patch_5");

      // Server me-replay event sejak Last-Event-ID: sudah diterapkan → diabaikan.
      const before = h.state;
      latest().emit("patch.applied", titlePatch(3), "21");
      latest().emit("patch.applied", titlePatch(4), "22");
      latest().emit("patch.applied", titlePatch(5), "23");
      expect(h.state).toBe(before);
      expect(sub.lastEventId).toBe("23");

      // Patch berikutnya menyambung normal.
      latest().emit("patch.applied", titlePatch(6), "24");
      expect(h.state.snapshot?.content.title).toBe("T6");
      sub.close();
    });

    it("patch bercelah dari SSE memicu resync sejak versi lokal", async () => {
      const getPatchesSince = vi.fn(
        async (): Promise<PatchesSinceResponse> => ({ patches: [titlePatch(3), titlePatch(4)], version: 4 }),
      );
      const h = createHarness(
        { getPatchesSince, getDashboard: vi.fn(async () => snapshot("x", 0)) },
        snapshot("T2", 2),
      );
      const sub = connectWorkspaceEvents("ws_1", h.handlers, { eventSourceFactory: factory });
      latest().open();

      // Event v3 terlewat; v4 tiba lebih dulu.
      latest().emit("patch.applied", titlePatch(4), "31");
      expect(h.state.snapshot?.version).toBe(2);
      expect(h.state.needsResync).toBe(true);
      expect(h.state.resyncReason).toBe("version_gap");

      await h.settle();
      expect(getPatchesSince).toHaveBeenCalledWith(DASH_ID, 2);
      expect(h.state.snapshot?.content.title).toBe("T4");
      expect(h.state.needsResync).toBe(false);
      sub.close();
    });

    it("event `resync` (replay tidak lengkap) memuat snapshot bila riwayat tidak menyambung", async () => {
      const serverSnap = snapshot("T9", 9);
      const getPatchesSince = vi.fn(async (): Promise<PatchesSinceResponse> => ({ snapshot: serverSnap }));
      const resyncs: unknown[] = [];
      const h = createHarness(
        { getPatchesSince, getDashboard: vi.fn(async () => serverSnap) },
        snapshot("T1", 1),
      );
      const onResync = h.handlers.onResync!;
      h.handlers.onResync = (data) => {
        resyncs.push(data);
        onResync(data);
      };

      const sub = connectWorkspaceEvents("ws_1", h.handlers, {
        eventSourceFactory: factory,
        lastEventId: "100",
      });
      latest().open();
      latest().emit("resync", { reason: "replay_incomplete", last_event_id: "100" });
      await h.settle();

      expect(resyncs).toEqual([{ reason: "replay_incomplete", last_event_id: "100" }]);
      expect(h.events.map((e) => e.event)).toEqual(["resync"]);
      expect(getPatchesSince).toHaveBeenCalledWith(DASH_ID, 1);
      expect(h.state.snapshot).toEqual(serverSnap);
      expect(h.state.needsResync).toBe(false);
      sub.close();
    });

    it("reconnect setelah koneksi CLOSED juga menyelaraskan Dashboard", async () => {
      const getPatchesSince = vi.fn(
        async (): Promise<PatchesSinceResponse> => ({ patches: [titlePatch(2)], version: 2 }),
      );
      const h = createHarness(
        { getPatchesSince, getDashboard: vi.fn(async () => snapshot("x", 0)) },
        snapshot("T1", 1),
      );
      const sub = connectWorkspaceEvents("ws_1", h.handlers, {
        eventSourceFactory: factory,
        initialBackoffMs: 500,
      });
      latest().open();
      latest().emit("job.progress", { job_id: "j", progress: 5 }, "50");
      latest().drop(CLOSED);
      vi.advanceTimersByTime(500);
      expect(latest().url).toMatch(/last_event_id=50$/);
      latest().open();
      await h.settle();

      expect(getPatchesSince).toHaveBeenCalledWith(DASH_ID, 1);
      expect(h.state.snapshot?.content.title).toBe("T2");
      sub.close();
    });
  });
});
