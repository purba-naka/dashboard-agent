// Feature: dashboard-studio-agent — test perangkai halaman Workspace (task 23.1).

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  ChatStreamHandlers,
  ChatStreamResult,
  EventSourceLike,
} from "@/lib/sse";
import type {
  ChatRequest,
  DashboardSnapshot,
  Dataset,
  PatchEvent,
  Relation,
  WorkspaceDetail,
} from "@/lib/types";
import type { StudioDeps } from "./deps";
import { WorkspaceStudio } from "./WorkspaceStudio";

afterEach(cleanup);

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

const dataset: Dataset = {
  id: "ds_1",
  workspace_id: "ws_1",
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
  row_count: 100,
  data_version: 1,
  data_updated_at: "2026-01-01T00:00:00Z",
  privacy_no_samples: false,
  created_at: "2026-01-01T00:00:00Z",
};

const snapshot = (over: Partial<DashboardSnapshot> = {}): DashboardSnapshot => ({
  id: "db_1",
  title: "Dashboard Penjualan",
  version: 2,
  content: {
    title: "Dashboard Penjualan",
    items: {},
    layout: {},
    global_filters: [],
  },
  can_undo: false,
  can_redo: false,
  item_status: {},
  ...over,
});

const detail = (over: Partial<WorkspaceDetail> = {}): WorkspaceDetail => ({
  workspace: {
    id: "ws_1",
    owner_id: "local",
    name: "Studio",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  datasets: [dataset],
  dashboards: [
    {
      id: "db_1",
      workspace_id: "ws_1",
      title: "Dashboard Penjualan",
      version: 2,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
  chat_sessions: [],
  ...over,
});

const relation: Relation = {
  id: "rel_1",
  workspace_id: "ws_1",
  candidate_key: "a.x|b.y",
  from_dataset_id: "ds_1",
  from_table: "sales",
  from_column: "region",
  to_dataset_id: "ds_2",
  to_table: "customers",
  to_column: "region",
  cardinality: "one_to_many",
  overlap_pct: 90,
  status: "candidate",
  created_at: "2026-01-01T00:00:00Z",
  decided_at: null,
};

function patchEvent(over: Partial<PatchEvent> = {}): PatchEvent {
  return {
    id: "p_3",
    dashboard_id: "db_1",
    version: 3,
    base_version: 2,
    source: "agent",
    kind: "normal",
    target_patch_id: null,
    ops: [
      { op: "set_title", before: "Dashboard Penjualan", after: "Judul Baru" },
    ],
    inverse_ops: [
      { op: "set_title", before: "Judul Baru", after: "Dashboard Penjualan" },
    ],
    created_at: "2026-01-01T00:00:01Z",
    ...over,
  };
}

// ---------------------------------------------------------------------------
// Fake SSE + deps
// ---------------------------------------------------------------------------

class FakeEventSource implements EventSourceLike {
  readonly readyState = 0;
  onopen: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  private listeners = new Map<string, Array<(ev: MessageEvent) => void>>();
  static instances: FakeEventSource[] = [];
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }
  close(): void {
    /* no-op */
  }
  open(): void {
    this.onopen?.({} as Event);
  }
  emit(type: string, data: unknown, lastEventId = "1"): void {
    const ev = { type, data: JSON.stringify(data), lastEventId } as MessageEvent;
    for (const listener of this.listeners.get(type) ?? []) listener(ev);
  }
}

function makeDeps(over: Partial<StudioDeps> = {}): StudioDeps {
  return {
    workspace: {
      list: vi.fn(async () => []),
      create: vi.fn(async () => ({}) as never),
      get: vi.fn(async () => detail()),
      rename: vi.fn(async () => ({}) as never),
      remove: vi.fn(async () => undefined),
    },
    canvas: {
      render: vi.fn(async () => ({ version: 2, items: {} })),
      command: vi.fn(async () => ({}) as never),
    },
    chat: {
      send: vi.fn<(ws: string, req: ChatRequest, handlers: ChatStreamHandlers) => Promise<ChatStreamResult>>(
        async () => ({ runId: null, sessionId: null, outcome: "done" }),
      ),
      stop: vi.fn(async () => undefined),
      history: vi.fn(async () => []),
      confirmRelation: vi.fn(async () => relation),
      rejectRelation: vi.fn(async () => relation),
    },
    datasets: {
      upload: vi.fn(async () => ({}) as never),
      selectSheets: vi.fn(async () => ({}) as never),
      getJob: vi.fn(async () => ({}) as never),
      getDataset: vi.fn(async () => ({}) as never),
      updateDataset: vi.fn(async () => ({}) as never),
      reupload: vi.fn(async () => ({}) as never),
    },
    export: {
      toPng: vi.fn(async () => "data:image/png;base64,X"),
      createPdfDoc: vi.fn(() => ({}) as never),
      slice: vi.fn(async () => []),
      savePdf: vi.fn(),
      downloadDataUrl: vi.fn(),
    },
    filters: {
      command: vi.fn(async () => ({}) as never),
      getDataset: vi.fn(async () => ({}) as never),
      columnValues: vi.fn(async () => ({ values: [], truncated: false })),
    },
    relations: {
      list: vi.fn(async () => [relation]),
      detect: vi.fn(async () => []),
      confirm: vi.fn(async () => relation),
      reject: vi.fn(async () => relation),
      remove: vi.fn(async () => undefined),
    },
    semantic: {
      load: vi.fn(async () => ({
        domain: null,
        domain_confidence: null,
        assumptions: [],
        semantic_version: 0,
        entries: [],
        draft_run: null,
      })),
      update: vi.fn(async () => ({}) as never),
      confirm: vi.fn(async () => ({}) as never),
      reject: vi.fn(async () => ({}) as never),
      confirmAll: vi.fn(async () => ({ confirmed: 0 })),
      redraft: vi.fn(async () => ({ scheduled: true })),
      exportYaml: vi.fn(async () => ""),
      importYaml: vi.fn(async () => ({}) as never),
    },
    brief: { command: vi.fn(async () => ({}) as never) },
    getDashboard: vi.fn(async () => snapshot()),
    getPatchesSince: vi.fn(async () => ({ patches: [], version: 2 })),
    createDashboard: vi.fn(async () => snapshot()),
    deleteDashboard: vi.fn(async () => undefined),
    eventSourceFactory: (url: string) => new FakeEventSource(url),
    ...over,
  };
}

afterEach(() => {
  FakeEventSource.instances = [];
});

function currentSource(): FakeEventSource {
  const src = FakeEventSource.instances.at(-1);
  if (!src) throw new Error("EventSource belum dibuat");
  return src;
}

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

describe("WorkspaceStudio", () => {
  it("memuat Workspace, Dashboard, dan memasang semua panel (Req 1.2)", async () => {
    render(<WorkspaceStudio workspaceId="ws_1" deps={makeDeps()} />);
    await screen.findByText("Studio");
    // Slot terisi: panel dataset, relasi, filter, canvas, chat.
    expect((await screen.findAllByText(/sales\.csv/)).length).toBeGreaterThan(0);
    expect(await screen.findByText(/sales\.region → customers\.region/)).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Ekspor PNG" })).toBeTruthy();
    expect(await screen.findByLabelText(/File dataset/)).toBeTruthy();
    // Canvas memuat judul dashboard (panel ringkasan + judul canvas).
    expect((await screen.findAllByText("Dashboard Penjualan")).length).toBeGreaterThanOrEqual(2);
    // Chat panel terpasang (textarea pesan dari Chat_Panel).
    expect(await screen.findByLabelText("Pesan")).toBeTruthy();
    // Panel model semantik & Design Brief terpasang (ekstensi v2).
    expect(await screen.findByRole("region", { name: "Model semantik" })).toBeTruthy();
    expect(await screen.findByText(/Design Brief/)).toBeTruthy();
  });

  it("event SSE patch.applied memperbarui versi canvas (Req 16.5, 18.3)", async () => {
    render(<WorkspaceStudio workspaceId="ws_1" deps={makeDeps()} />);
    // Tunggu snapshot termuat (judul dashboard tampil).
    await screen.findByText("Dashboard Penjualan");
    const src = currentSource();
    src.open();
    src.emit("patch.applied", patchEvent());
    await waitFor(() => expect(screen.getByText("Judul Baru")).toBeTruthy());
    await waitFor(() => expect(screen.getByText("v3")).toBeTruthy());
  });

  it("job.done memuat ulang detail dan mengirim giliran chat pemicu (Req 21.1, 26.2)", async () => {
    const deps = makeDeps();
    render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
    await screen.findByText("Dashboard Penjualan");
    const src = currentSource();
    src.open();
    src.emit("job.done", { job_id: "j_9", dataset_id: "ds_1" });
    await waitFor(() => expect(deps.workspace.get).toHaveBeenCalled());
    await waitFor(() =>
      expect(deps.chat.send).toHaveBeenCalledWith(
        "ws_1",
        expect.objectContaining({
          message: expect.stringContaining("sales.csv"),
        }),
        expect.anything(),
        expect.anything(),
      ),
    );
  });

  it("event relation.updated menyegarkan panel relasi", async () => {
    const deps = makeDeps();
    render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
    await screen.findByText(/sales\.region → customers\.region/);
    const src = currentSource();
    src.open();
    const callsBefore = (deps.relations.list as ReturnType<typeof vi.fn>).mock.calls.length;
    src.emit("relation.updated", { relation_ids: ["rel_1"] });
    await waitFor(() =>
      expect((deps.relations.list as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(
        callsBefore,
      ),
    );
  });

  it("Workspace tanpa Dashboard menawarkan pembuatan (Req 22.3)", async () => {
    const deps = makeDeps();
    deps.workspace.get = vi.fn(async () => detail({ dashboards: [] }));
    render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
    const create = await screen.findByRole("button", { name: "Buat Dashboard" });
    expect(create).toBeTruthy();
    expect(screen.getByText("Buat Dashboard untuk mulai memfilter.")).toBeTruthy();
  });

  it("tombol Buat Dashboard membuat Dashboard lalu memuatnya", async () => {
    const user = userEvent.setup();
    const deps = makeDeps();
    deps.workspace.get = vi.fn(async () => detail({ dashboards: [] }));
    render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
    await screen.findByRole("button", { name: "Buat Dashboard" });
    await user.click(screen.getByRole("button", { name: "Buat Dashboard" }));
    await waitFor(() => expect(deps.createDashboard).toHaveBeenCalledWith("ws_1", "Dashboard"));
    // Setelah dibuat, canvas memuat judul dashboard baru.
    await screen.findByText("Dashboard Penjualan");
  });

  it("resync memuat ulang snapshot saat reconnect (Req 16.5)", async () => {
    const deps = makeDeps();
    render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
    await screen.findByText("Dashboard Penjualan");
    const src = currentSource();
    src.open();
    // Paksa celah versi: patch versi 5 (versi lokal 2 → gap → resync).
    src.emit("patch.applied", patchEvent({ version: 5, base_version: 4 }));
    await waitFor(() => expect(deps.getPatchesSince).toHaveBeenCalled());
  });

  describe("multi-halaman", () => {
    const twoPages = () =>
      detail({
        dashboards: [
          { id: "db_1", workspace_id: "ws_1", title: "Overview", version: 2, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
          { id: "db_2", workspace_id: "ws_1", title: "Revenue", version: 1, created_at: "2026-01-02T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" },
        ],
      });
    const pagesDeps = () =>
      makeDeps({
        workspace: { ...makeDeps().workspace, get: vi.fn(async () => twoPages()) },
        getDashboard: vi.fn(async (id: string) =>
          id === "db_2"
            ? snapshot({ id: "db_2", title: "Revenue", version: 1, content: { title: "Revenue", items: {}, layout: {}, global_filters: [] } })
            : snapshot({ id: "db_1", title: "Overview" }),
        ),
      });

    afterEach(() => window.history.replaceState(null, "", "/"));

    it("pindah halaman memuat snapshot yang benar dan menulis ?page=", async () => {
      const deps = pagesDeps();
      const user = userEvent.setup();
      render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
      const nav = await screen.findByRole("navigation", { name: "Halaman dashboard" });
      expect(nav.querySelector('[aria-current="page"]')?.textContent).toBe("Overview");
      await user.click(screen.getByRole("button", { name: "Revenue" }));
      await waitFor(() => expect(deps.getDashboard).toHaveBeenCalledWith("db_2"));
      expect(window.location.search).toBe("?page=db_2");
      expect(nav.querySelector('[aria-current="page"]')?.textContent).toBe("Revenue");
    });

    it("?page= dipulihkan saat dimuat", async () => {
      window.history.replaceState(null, "", "/?page=db_2");
      const deps = pagesDeps();
      render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
      await waitFor(() => expect(deps.getDashboard).toHaveBeenCalledWith("db_2"));
      expect(deps.getDashboard).not.toHaveBeenCalledWith("db_1");
    });

    it("patch halaman lain tidak mengubah canvas", async () => {
      const deps = pagesDeps();
      render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
      await screen.findByRole("navigation", { name: "Halaman dashboard" });
      await waitFor(() => expect(deps.getDashboard).toHaveBeenCalledWith("db_1"));
      currentSource().emit("patch.applied", patchEvent({ dashboard_id: "db_2", version: 2, base_version: 1 }));
      await new Promise((r) => setTimeout(r, 50));
      expect(screen.queryByText("Judul Baru")).toBeNull();
    });

    it("chat mengirim dashboard_id halaman aktif", async () => {
      const deps = pagesDeps();
      const user = userEvent.setup();
      render(<WorkspaceStudio workspaceId="ws_1" deps={deps} />);
      await user.click(await screen.findByRole("button", { name: "Revenue" }));
      await waitFor(() => expect(deps.getDashboard).toHaveBeenCalledWith("db_2"));
      await user.type(await screen.findByLabelText("Pesan"), "halo{enter}");
      await waitFor(() => expect(deps.chat.send).toHaveBeenCalled());
      expect(vi.mocked(deps.chat.send).mock.calls[0][1]).toMatchObject({ dashboard_id: "db_2" });
    });
  });
});
