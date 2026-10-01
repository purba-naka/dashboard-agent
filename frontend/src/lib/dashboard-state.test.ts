import { describe, expect, it, vi } from "vitest";

import fixtureJson from "./__fixtures__/patches.json";
import {
  CONFLICT_MESSAGE,
  InvalidOpError,
  applyOps,
  applyPatch,
  dashboardReducer,
  initialDashboardState,
  jsonEqual,
  replayPatches,
  resyncDashboard,
  type DashboardState,
  type ResyncFetchers,
} from "./dashboard-state";
import type {
  ChartItem,
  DashboardContent,
  DashboardSnapshot,
  LayoutRect,
  Op,
  PatchEvent,
  PatchesSinceResponse,
} from "./types";

// ---------------------------------------------------------------------------
// Fixture dari backend (`backend/tests/fixtures/gen_patch_fixtures.py`)
// ---------------------------------------------------------------------------

interface Scenario {
  name: string;
  description: string;
  initial: DashboardContent;
  patches: PatchEvent[];
  expected: DashboardContent;
}

const fixture = fixtureJson as unknown as { dashboard_id: string; scenarios: Scenario[] };

const DASH_ID = "dash_test";

function chart(id: string, title = `Chart ${id}`): ChartItem {
  return {
    id,
    kind: "chart",
    title,
    spec: {
      spec_version: 1,
      query_id: "q_1",
      chart_type: "bar",
      option: { xAxis: { type: "category" } },
      cross_filter_column: null,
    },
  };
}

const rect = (x = 0, y = 0, w = 6, h = 4): LayoutRect => ({ x, y, w, h });

function content(overrides: Partial<DashboardContent> = {}): DashboardContent {
  return { title: "Dash", items: {}, layout: {}, global_filters: [], ...overrides };
}

function snapshot(c: DashboardContent, version = 0, id = DASH_ID): DashboardSnapshot {
  return {
    id,
    title: c.title,
    version,
    content: c,
    can_undo: false,
    can_redo: false,
    item_status: {},
  };
}

function patch(
  ops: Op[],
  version: number,
  overrides: Partial<PatchEvent> = {},
): PatchEvent {
  return {
    id: `patch_${version}`,
    dashboard_id: DASH_ID,
    version,
    base_version: version - 1,
    source: "user",
    kind: "normal",
    target_patch_id: null,
    ops,
    inverse_ops: [],
    created_at: "2024-01-01T00:00:00Z",
    ...overrides,
  };
}

function loaded(c: DashboardContent = content(), version = 0): DashboardState {
  return dashboardReducer(initialDashboardState, {
    type: "snapshotLoaded",
    snapshot: snapshot(c, version),
  });
}

const titleOp = (before: string, after: string): Op => ({ op: "set_title", before, after });

// ---------------------------------------------------------------------------

describe("fixture patches.json (paritas dengan core/patches.py)", () => {
  it("memuat skenario yang mencakup semua jenis op", () => {
    const kinds = new Set(
      fixture.scenarios.flatMap((s) => s.patches.flatMap((p) => p.ops.map((o) => o.op))),
    );
    expect([...kinds].sort()).toEqual(
      [
        "add_item",
        "remove_item",
        "set_brief",
        "set_filters",
        "set_item",
        "set_layout",
        "set_title",
      ].sort(),
    );
  });

  describe.each(fixture.scenarios.map((s) => [s.name, s] as const))("skenario %s", (_n, s) => {
    it("replayPatches menghasilkan expected", () => {
      const result = replayPatches(s.initial, s.patches);
      expect(jsonEqual(result, s.expected)).toBe(true);
      expect(result).toEqual(s.expected);
    });

    it("applyOps per patch menghasilkan expected tanpa memutasi initial", () => {
      const initialCopy = structuredClone(s.initial);
      const result = s.patches.reduce((c, p) => applyOps(c, p.ops), s.initial);
      expect(result).toEqual(s.expected);
      expect(s.initial).toEqual(initialCopy);
    });

    it("reducer patchReceived menghasilkan expected dan versi terakhir", () => {
      const start = s.patches[0].base_version;
      let state = dashboardReducer(initialDashboardState, {
        type: "snapshotLoaded",
        snapshot: {
          ...snapshot(s.initial, start, s.patches[0].dashboard_id),
        },
      });
      for (const p of s.patches) state = dashboardReducer(state, { type: "patchReceived", patch: p });
      const last = s.patches[s.patches.length - 1];
      expect(state.needsResync).toBe(false);
      expect(state.snapshot?.version).toBe(last.version);
      expect(state.snapshot?.title).toBe(s.expected.title);
      expect(state.snapshot?.content).toEqual(s.expected);
      expect(state.lastPatchId).toBe(last.id);
    });
  });
});

describe("applyOps: prasyarat setiap jenis op", () => {
  const base = content({ items: { a: chart("a") }, layout: { a: rect() } });

  it.each<[string, Op]>([
    ["add_item id sudah ada", { op: "add_item", item: chart("a"), layout: rect() }],
    ["remove_item tidak ada", { op: "remove_item", item: chart("zz"), layout: rect() }],
    ["remove_item snapshot beda", { op: "remove_item", item: chart("a", "lain"), layout: rect() }],
    ["set_item before beda", { op: "set_item", id: "a", before: chart("a", "x"), after: chart("a") }],
    ["set_item id before/after beda", { op: "set_item", id: "a", before: chart("a"), after: chart("b") }],
    [
      "set_layout before beda",
      { op: "set_layout", changes: [{ id: "a", before: rect(1), after: rect(2) }] },
    ],
    [
      "set_layout keluar grid",
      { op: "set_layout", changes: [{ id: "a", before: rect(), after: rect(8, 0, 6, 4) }] },
    ],
    [
      "set_filters before beda",
      {
        op: "set_filters",
        before: [{ kind: "in", table: "t", column: "c", values: [1] }],
        after: [],
      },
    ],
    ["set_title before beda", titleOp("Bukan", "Baru")],
  ])("%s → InvalidOpError, state tidak berubah", (_label, op) => {
    const before = structuredClone(base);
    expect(() => applyOps(base, [op])).toThrow(InvalidOpError);
    expect(base).toEqual(before);
  });

  it("all-or-nothing: op kedua gagal → tidak ada hasil parsial", () => {
    const ops: Op[] = [titleOp("Dash", "Baru"), titleOp("Dash", "Lagi")];
    const err = (() => {
      try {
        applyOps(base, ops);
      } catch (e) {
        return e;
      }
    })();
    expect(err).toBeInstanceOf(InvalidOpError);
    expect((err as InvalidOpError).details.op_index).toBe(1);
    expect(base.title).toBe("Dash");
  });

  it("jsonEqual mengabaikan urutan key", () => {
    expect(jsonEqual({ a: 1, b: [1, { c: 2 }] }, { b: [1, { c: 2 }], a: 1 })).toBe(true);
    expect(jsonEqual([1, 2], [2, 1])).toBe(false);
  });
});

describe("dashboardReducer: versi", () => {
  it("menerapkan patch berurutan dan memperkirakan can_undo/can_redo", () => {
    const s1 = applyPatch(loaded(), patch([titleOp("Dash", "Baru")], 1));
    expect(s1.snapshot?.version).toBe(1);
    expect(s1.snapshot?.title).toBe("Baru");
    expect(s1.snapshot?.can_undo).toBe(true);
    expect(s1.snapshot?.can_redo).toBe(false);

    const s2 = applyPatch(s1, patch([titleOp("Baru", "Dash")], 2, { kind: "undo" }));
    expect(s2.snapshot?.can_redo).toBe(true);
    expect(s2.historyApproximate).toBe(true);
  });

  it("patch duplikat (version <= state.version) diabaikan", () => {
    const p1 = patch([titleOp("Dash", "Baru")], 1);
    const s1 = dashboardReducer(loaded(), { type: "patchReceived", patch: p1 });
    const s2 = dashboardReducer(s1, { type: "patchReceived", patch: p1 });
    expect(s2).toBe(s1);
    expect(s2.needsResync).toBe(false);
  });

  it("patch dashboard lain atau sebelum snapshot dimuat diabaikan", () => {
    const p = patch([titleOp("Dash", "Baru")], 1, { dashboard_id: "other" });
    const s = loaded();
    expect(applyPatch(s, p)).toBe(s);
    expect(applyPatch(initialDashboardState, patch([], 1))).toBe(initialDashboardState);
  });

  it("celah versi → tidak diterapkan, needsResync (version_gap)", () => {
    const s = dashboardReducer(loaded(), {
      type: "patchReceived",
      patch: patch([titleOp("Dash", "Baru")], 3),
    });
    expect(s.snapshot?.version).toBe(0);
    expect(s.snapshot?.title).toBe("Dash");
    expect(s.needsResync).toBe(true);
    expect(s.resyncReason).toBe("version_gap");
    expect(s.knownServerVersion).toBe(3);
  });

  it("version !== base_version + 1 ditolak meskipun base_version cocok", () => {
    const s = applyPatch(loaded(), patch([titleOp("Dash", "Baru")], 2, { base_version: 0 }));
    expect(s.snapshot?.version).toBe(0);
    expect(s.needsResync).toBe(true);
  });

  it("op tidak valid → content tidak berubah, needsResync (invalid_op)", () => {
    const s = applyPatch(loaded(), patch([titleOp("Salah", "Baru")], 1));
    expect(s.snapshot?.version).toBe(0);
    expect(s.snapshot?.title).toBe("Dash");
    expect(s.needsResync).toBe(true);
    expect(s.resyncReason).toBe("invalid_op");
  });
});

describe("dashboardReducer: konflik 409 & resync", () => {
  it("conflict → notifikasi + needsResync; noticeDismissed menghapus notifikasi", () => {
    const s = dashboardReducer(loaded(), { type: "conflict", currentVersion: 5 });
    expect(s.notice).toEqual({ kind: "conflict", message: CONFLICT_MESSAGE });
    expect(CONFLICT_MESSAGE.toLowerCase()).toContain("perubahan ditolak karena state sudah berubah");
    expect(s.needsResync).toBe(true);
    expect(s.resyncReason).toBe("conflict");
    expect(s.knownServerVersion).toBe(5);

    const d = dashboardReducer(s, { type: "noticeDismissed" });
    expect(d.notice).toBeNull();
    expect(d.needsResync).toBe(true);
  });

  it("snapshotLoaded setelah konflik membersihkan flag resync", () => {
    const s = dashboardReducer(loaded(), { type: "conflict", currentVersion: 5 });
    const fresh = snapshot(content({ title: "Server" }), 5);
    const r = dashboardReducer(s, { type: "snapshotLoaded", snapshot: fresh });
    expect(r.snapshot).toBe(fresh);
    expect(r.needsResync).toBe(false);
    expect(r.resyncReason).toBeNull();
    expect(r.knownServerVersion).toBeNull();
  });

  it("snapshotLoaded lebih lama dari state lokal diabaikan", () => {
    const s = applyPatch(loaded(), patch([titleOp("Dash", "Baru")], 1));
    expect(dashboardReducer(s, { type: "snapshotLoaded", snapshot: snapshot(content(), 0) })).toBe(s);
  });

  it("resyncRequested menandai needsResync", () => {
    const s = dashboardReducer(loaded(), { type: "resyncRequested" });
    expect(s.needsResync).toBe(true);
    expect(s.resyncReason).toBe("requested");
  });

  it("patchesReceived dengan snapshot → memuat snapshot", () => {
    const s = dashboardReducer(loaded(), { type: "resyncRequested" });
    const fresh = snapshot(content({ title: "Server" }), 7);
    const r = dashboardReducer(s, {
      type: "patchesReceived",
      response: { snapshot: fresh },
    });
    expect(r.snapshot).toBe(fresh);
    expect(r.needsResync).toBe(false);
  });

  it("patchesReceived menambal celah (urutan acak) dan menyelesaikan resync", () => {
    const p1 = patch([titleOp("Dash", "A")], 1);
    const p2 = patch([titleOp("A", "B")], 2);
    const p3 = patch([titleOp("B", "C")], 3);
    const gap = dashboardReducer(loaded(), { type: "patchReceived", patch: p3 });
    expect(gap.needsResync).toBe(true);

    const r = dashboardReducer(gap, {
      type: "patchesReceived",
      response: { patches: [p2, p3, p1], version: 3 },
    });
    expect(r.snapshot?.version).toBe(3);
    expect(r.snapshot?.title).toBe("C");
    expect(r.needsResync).toBe(false);
    expect(r.resyncReason).toBeNull();
  });

  it("patchesReceived yang masih tertinggal dari versi server tetap needsResync", () => {
    const p1 = patch([titleOp("Dash", "A")], 1);
    const r = dashboardReducer(loaded(), {
      type: "patchesReceived",
      response: { patches: [p1], version: 4 },
    });
    expect(r.snapshot?.version).toBe(1);
    expect(r.needsResync).toBe(true);
  });
});

describe("resyncDashboard", () => {
  function fetchers(response: PatchesSinceResponse, server = snapshot(content(), 9)) {
    return {
      getDashboard: vi.fn<ResyncFetchers["getDashboard"]>(async () => server),
      getPatchesSince: vi.fn<ResyncFetchers["getPatchesSince"]>(async () => response),
    };
  }

  it("mengembalikan null bila snapshot belum dimuat", async () => {
    const f = fetchers({ patches: [] });
    expect(await resyncDashboard(initialDashboardState, f)).toBeNull();
    expect(f.getDashboard).not.toHaveBeenCalled();
    expect(f.getPatchesSince).not.toHaveBeenCalled();
  });

  it("konflik → refetch snapshot (bukan patches_since)", async () => {
    const f = fetchers({ patches: [] });
    const s = dashboardReducer(loaded(), { type: "conflict", currentVersion: 9 });
    const action = await resyncDashboard(s, f);
    expect(f.getDashboard).toHaveBeenCalledWith(DASH_ID);
    expect(f.getPatchesSince).not.toHaveBeenCalled();
    expect(action).toEqual({ type: "snapshotLoaded", snapshot: snapshot(content(), 9) });
  });

  it("invalid_op → refetch snapshot", async () => {
    const f = fetchers({ patches: [] });
    const s = applyPatch(loaded(), patch([titleOp("Salah", "X")], 1));
    await resyncDashboard(s, f);
    expect(f.getDashboard).toHaveBeenCalledTimes(1);
    expect(f.getPatchesSince).not.toHaveBeenCalled();
  });

  it("celah versi → patches_since dari versi lokal, hasil di-dispatch sebagai patchesReceived", async () => {
    const p1 = patch([titleOp("Dash", "A")], 1);
    const p2 = patch([titleOp("A", "B")], 2);
    const response: PatchesSinceResponse = { patches: [p1, p2], version: 2 };
    const f = fetchers(response);
    const s = applyPatch(loaded(), p2);
    const action = await resyncDashboard(s, f);
    expect(f.getPatchesSince).toHaveBeenCalledWith(DASH_ID, 0);
    expect(f.getDashboard).not.toHaveBeenCalled();
    expect(action).toEqual({ type: "patchesReceived", response });

    const r = dashboardReducer(s, action!);
    expect(r.snapshot?.title).toBe("B");
    expect(r.needsResync).toBe(false);
  });

  it("patches_since mengirim snapshot (riwayat tidak tersedia) → snapshotLoaded", async () => {
    const fresh = snapshot(content({ title: "Server" }), 12);
    const f = fetchers({ snapshot: fresh });
    const s = dashboardReducer(loaded(), { type: "resyncRequested" });
    expect(await resyncDashboard(s, f)).toEqual({ type: "snapshotLoaded", snapshot: fresh });
  });
});
