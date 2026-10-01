/**
 * State Dashboard di Frontend: penerapan ops dan reducer Patch_Event.
 *
 * `applyOps` adalah cermin `backend/studio/core/patches.py::apply_ops`
 * (prasyarat identik, murni, all-or-nothing). `dashboardReducer` mengelola
 * versi Dashboard (Req 18.3, 18.6, 30.3):
 *
 * - `patch.version <= state.version`          → duplikat, diabaikan.
 * - `patch.base_version === state.version`    → ops diterapkan, versi = `patch.version`.
 * - selain itu (celah versi)                  → tidak diterapkan, `needsResync = true`.
 *
 * Modul ini tidak melakukan I/O; `resyncDashboard` menerima fungsi fetch
 * (mis. `getDashboard`/`getPatchesSince` dari `api.ts`) lewat parameter.
 *
 * Catatan `can_undo`/`can_redo`/`item_status`: nilai otoritatif berasal dari
 * snapshot server (`snapshotLoaded`). Setelah patch, nilainya diperkirakan:
 * - `normal` → `can_undo = true`, `can_redo = false` (patch baru mengosongkan redo).
 * - `undo`   → `can_redo = true`; `can_undo` tidak diketahui (dibiarkan).
 * - `redo`   → `can_undo = true`; `can_redo` tidak diketahui (dibiarkan).
 * Untuk `undo`/`redo`, `historyApproximate` di-set `true` agar UI dapat
 * me-refetch snapshot bila butuh nilai pasti. `item_status` untuk item yang
 * ditambah/dihapus/diubah dibuang (dianggap ok) sampai snapshot/render berikutnya.
 */

import type {
  DashboardContent,
  DashboardItem,
  DashboardSnapshot,
  DesignBrief,
  ItemStatus,
  LayoutRect,
  Op,
  PatchEvent,
  PatchesSinceResponse,
} from "./types";

/** Sama dengan `GRID_COLUMNS` di `core/models.py`. */
export const GRID_COLUMNS = 12;

/** Notifikasi saat Command ditolak 409 `VERSION_CONFLICT` (Req 18.6). */
export const CONFLICT_MESSAGE = "Perubahan ditolak karena state sudah berubah";

// ---------------------------------------------------------------------------
// Kesetaraan struktural (semantik JSON / `==` model Pydantic)
// ---------------------------------------------------------------------------

/**
 * Kesetaraan struktural nilai JSON: objek dibandingkan per key (urutan key
 * tidak berpengaruh, key bernilai `undefined` dianggap tidak ada), array
 * dibandingkan berurutan, primitif dengan `===`.
 */
export function jsonEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (typeof a !== "object") return false; // primitif berbeda (NaN ≠ NaN seperti JSON)

  const aIsArray = Array.isArray(a);
  if (aIsArray !== Array.isArray(b)) return false;
  if (aIsArray) {
    const x = a as unknown[];
    const y = b as unknown[];
    if (x.length !== y.length) return false;
    for (let i = 0; i < x.length; i++) if (!jsonEqual(x[i], y[i])) return false;
    return true;
  }

  const x = a as Record<string, unknown>;
  const y = b as Record<string, unknown>;
  const xKeys = Object.keys(x).filter((k) => x[k] !== undefined);
  const yKeys = Object.keys(y).filter((k) => y[k] !== undefined);
  if (xKeys.length !== yKeys.length) return false;
  for (const k of xKeys) {
    if (!Object.prototype.hasOwnProperty.call(y, k) || !jsonEqual(x[k], y[k])) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// applyOps
// ---------------------------------------------------------------------------

export interface InvalidOpDetails {
  op_index?: number;
  op?: string;
  item_id?: string;
  [key: string]: unknown;
}

/** Prasyarat op tidak terpenuhi (padanan `InvalidOp` backend, kode `INVALID_OP`). */
export class InvalidOpError extends Error {
  readonly code = "INVALID_OP";
  readonly details: InvalidOpDetails;

  constructor(message: string, details: InvalidOpDetails = {}) {
    super(message);
    this.name = "InvalidOpError";
    this.details = details;
  }
}

function has(obj: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(obj, key);
}

function isValidRect(r: LayoutRect): boolean {
  return (
    Number.isInteger(r.x) &&
    Number.isInteger(r.y) &&
    Number.isInteger(r.w) &&
    Number.isInteger(r.h) &&
    r.x >= 0 &&
    r.y >= 0 &&
    r.w >= 1 &&
    r.w <= GRID_COLUMNS &&
    r.h >= 1 &&
    r.x + r.w <= GRID_COLUMNS
  );
}

/** Invariant `DashboardContent` (lihat `_check_invariants` di `core/models.py`). */
function checkContentInvariants(
  items: Record<string, DashboardItem>,
  layout: Record<string, LayoutRect>,
): void {
  const itemKeys = Object.keys(items);
  const layoutKeys = Object.keys(layout);
  const missingLayout = itemKeys.filter((k) => !has(layout, k)).sort();
  const orphanLayout = layoutKeys.filter((k) => !has(items, k)).sort();
  if (missingLayout.length > 0 || orphanLayout.length > 0) {
    throw new InvalidOpError("Hasil ops melanggar invariant Dashboard.", {
      missing_layout: missingLayout,
      orphan_layout: orphanLayout,
    });
  }
  for (const key of itemKeys) {
    if (items[key].id !== key) {
      throw new InvalidOpError("Hasil ops melanggar invariant Dashboard.", {
        item_id: key,
        reason: "key item tidak sama dengan item.id",
      });
    }
    if (!isValidRect(layout[key])) {
      throw new InvalidOpError("Hasil ops melanggar invariant Dashboard.", {
        item_id: key,
        reason: "layout tidak valid",
      });
    }
  }
}

/**
 * Terapkan `ops` berurutan secara murni; lempar `InvalidOpError` bila
 * prasyarat gagal. `content` tidak dimodifikasi dan tidak ada hasil parsial.
 */
export function applyOps(content: DashboardContent, ops: readonly Op[]): DashboardContent {
  let title = content.title;
  const items: Record<string, DashboardItem> = { ...content.items };
  const layout: Record<string, LayoutRect> = { ...content.layout };
  let filters = [...content.global_filters];
  let brief: DesignBrief | null | undefined = content.brief;

  ops.forEach((op, index) => {
    const where: InvalidOpDetails = { op_index: index, op: op.op };

    switch (op.op) {
      case "add_item": {
        const id = op.item.id;
        if (has(items, id) || has(layout, id)) {
          throw new InvalidOpError(`add_item: item '${id}' sudah ada.`, { ...where, item_id: id });
        }
        items[id] = op.item;
        layout[id] = op.layout;
        break;
      }

      case "remove_item": {
        const id = op.item.id;
        if (!has(items, id)) {
          throw new InvalidOpError(`remove_item: item '${id}' tidak ada.`, { ...where, item_id: id });
        }
        if (!jsonEqual(items[id], op.item) || !jsonEqual(layout[id], op.layout)) {
          throw new InvalidOpError(
            `remove_item: snapshot item '${id}' tidak sama dengan state saat ini.`,
            { ...where, item_id: id },
          );
        }
        delete items[id];
        delete layout[id];
        break;
      }

      case "set_item": {
        if (op.before.id !== op.id || op.after.id !== op.id) {
          throw new InvalidOpError(`set_item: id before/after harus '${op.id}'.`, {
            ...where,
            item_id: op.id,
          });
        }
        if (!has(items, op.id)) {
          throw new InvalidOpError(`set_item: item '${op.id}' tidak ada.`, {
            ...where,
            item_id: op.id,
          });
        }
        if (!jsonEqual(items[op.id], op.before)) {
          throw new InvalidOpError(
            `set_item: before item '${op.id}' tidak sama dengan state saat ini.`,
            { ...where, item_id: op.id },
          );
        }
        items[op.id] = op.after;
        break;
      }

      case "set_layout": {
        const seen = new Set<string>();
        for (const change of op.changes) {
          if (seen.has(change.id)) {
            throw new InvalidOpError(`set_layout: id '${change.id}' duplikat.`, {
              ...where,
              item_id: change.id,
            });
          }
          seen.add(change.id);
          if (!has(layout, change.id)) {
            throw new InvalidOpError(`set_layout: item '${change.id}' tidak ada.`, {
              ...where,
              item_id: change.id,
            });
          }
          if (!jsonEqual(layout[change.id], change.before)) {
            throw new InvalidOpError(
              `set_layout: before layout '${change.id}' tidak sama dengan state saat ini.`,
              { ...where, item_id: change.id },
            );
          }
        }
        for (const change of op.changes) layout[change.id] = change.after;
        break;
      }

      case "set_filters": {
        if (!jsonEqual(filters, op.before)) {
          throw new InvalidOpError(
            "set_filters: before tidak sama dengan global_filters saat ini.",
            where,
          );
        }
        filters = [...op.after];
        break;
      }

      case "set_title": {
        if (title !== op.before) {
          throw new InvalidOpError("set_title: before tidak sama dengan judul saat ini.", where);
        }
        title = op.after;
        break;
      }

      case "set_brief": {
        if (!jsonEqual(brief ?? null, op.before ?? null)) {
          throw new InvalidOpError("set_brief: before tidak sama dengan brief saat ini.", where);
        }
        brief = op.after ?? null;
        break;
      }

      default: {
        const unknownOp: { op?: unknown } = op;
        throw new InvalidOpError(`Op tidak dikenal: ${String(unknownOp.op)}.`, {
          op_index: index,
        });
      }
    }
  });

  checkContentInvariants(items, layout);
  const next: DashboardContent = { title, items, layout, global_filters: filters };
  // `brief` hanya disertakan bila sudah dikenal (konten lama tanpa field ini).
  if (brief !== undefined) next.brief = brief;
  return next;
}

/** Terapkan `ops` setiap Patch_Event berurutan (padanan `replay` backend). */
export function replayPatches(
  initial: DashboardContent,
  patches: readonly PatchEvent[],
): DashboardContent {
  let content = initial;
  let previous: PatchEvent | null = null;
  for (const patch of patches) {
    if (previous !== null && patch.base_version !== previous.version) {
      throw new InvalidOpError("replay: versi Patch_Event tidak berurutan.", {
        patch_id: patch.id,
        expected_base_version: previous.version,
        base_version: patch.base_version,
      });
    }
    content = applyOps(content, patch.ops);
    previous = patch;
  }
  return content;
}

// ---------------------------------------------------------------------------
// State & reducer
// ---------------------------------------------------------------------------

export type ResyncReason =
  /** Patch dengan celah versi diterima (mis. event SSE terlewat). */
  | "version_gap"
  /** Ops patch gagal diterapkan ke state lokal (state lokal menyimpang). */
  | "invalid_op"
  /** Command ditolak 409 `VERSION_CONFLICT`. */
  | "conflict"
  /** Diminta eksplisit (event SSE `resync`, reconnect, dsb.). */
  | "requested";

export interface DashboardNotice {
  kind: "conflict";
  message: string;
}

export interface DashboardState {
  /** `null` sampai snapshot pertama dimuat. */
  snapshot: DashboardSnapshot | null;
  /** `true` bila state lokal mungkin tertinggal/menyimpang; UI harus resync. */
  needsResync: boolean;
  resyncReason: ResyncReason | null;
  /** Versi server terbaru yang diketahui (dari patch bercelah / 409), bila ada. */
  knownServerVersion: number | null;
  /** `true` bila `can_undo`/`can_redo` hasil perkiraan setelah patch undo/redo. */
  historyApproximate: boolean;
  /** Id Patch_Event terakhir yang diterapkan. */
  lastPatchId: string | null;
  notice: DashboardNotice | null;
}

export const initialDashboardState: DashboardState = {
  snapshot: null,
  needsResync: false,
  resyncReason: null,
  knownServerVersion: null,
  historyApproximate: false,
  lastPatchId: null,
  notice: null,
};

export type DashboardAction =
  /** Snapshot otoritatif dari `GET /dashboards/{id}` (atau `patches_since.snapshot`). */
  | { type: "snapshotLoaded"; snapshot: DashboardSnapshot }
  /** Satu Patch_Event (SSE `patch.applied`, respons POST patch/undo/redo). */
  | { type: "patchReceived"; patch: PatchEvent }
  /** Respons `GET /dashboards/{id}/patches?since_version=`. */
  | { type: "patchesReceived"; response: PatchesSinceResponse }
  /** Command ditolak 409 `VERSION_CONFLICT`. */
  | { type: "conflict"; currentVersion?: number | null }
  /** Minta resync (event SSE `resync`, reconnect, dsb.). */
  | { type: "resyncRequested"; reason?: ResyncReason }
  | { type: "noticeDismissed" };

type PatchOutcome = "applied" | "duplicate" | "ignored" | "gap" | "invalid";

function itemIdsTouched(ops: readonly Op[]): string[] {
  const ids: string[] = [];
  for (const op of ops) {
    if (op.op === "add_item" || op.op === "remove_item") ids.push(op.item.id);
    else if (op.op === "set_item") ids.push(op.id);
  }
  return ids;
}

function nextItemStatus(
  current: Record<string, ItemStatus>,
  ops: readonly Op[],
  content: DashboardContent,
): Record<string, ItemStatus> {
  const touched = itemIdsTouched(ops);
  if (touched.length === 0) return current;
  const next: Record<string, ItemStatus> = {};
  const drop = new Set(touched);
  for (const [id, status] of Object.entries(current)) {
    if (!drop.has(id) && has(content.items, id)) next[id] = status;
  }
  return next;
}

function markResync(
  state: DashboardState,
  reason: ResyncReason,
  knownServerVersion: number | null = state.knownServerVersion,
): DashboardState {
  return {
    ...state,
    needsResync: true,
    // Pertahankan alasan pertama (mis. "conflict") sampai resync selesai.
    resyncReason: state.resyncReason ?? reason,
    knownServerVersion:
      knownServerVersion === null
        ? state.knownServerVersion
        : Math.max(knownServerVersion, state.knownServerVersion ?? knownServerVersion),
  };
}

function applyPatchWithOutcome(
  state: DashboardState,
  patch: PatchEvent,
): { state: DashboardState; outcome: PatchOutcome } {
  const snap = state.snapshot;
  if (snap === null || patch.dashboard_id !== snap.id) return { state, outcome: "ignored" };
  if (patch.version <= snap.version) return { state, outcome: "duplicate" };
  if (patch.base_version !== snap.version || patch.version !== snap.version + 1) {
    return { state: markResync(state, "version_gap", patch.version), outcome: "gap" };
  }

  let content: DashboardContent;
  try {
    content = applyOps(snap.content, patch.ops);
  } catch (err) {
    if (err instanceof InvalidOpError) {
      return { state: markResync(state, "invalid_op", patch.version), outcome: "invalid" };
    }
    throw err;
  }

  let { can_undo, can_redo } = snap;
  let historyApproximate = state.historyApproximate;
  if (patch.kind === "normal") {
    can_undo = true;
    can_redo = false;
  } else if (patch.kind === "undo") {
    can_redo = true;
    historyApproximate = true;
  } else {
    can_undo = true;
    historyApproximate = true;
  }

  const snapshot: DashboardSnapshot = {
    ...snap,
    title: content.title,
    version: patch.version,
    content,
    can_undo,
    can_redo,
    item_status: nextItemStatus(snap.item_status, patch.ops, content),
  };
  return {
    state: { ...state, snapshot, historyApproximate, lastPatchId: patch.id },
    outcome: "applied",
  };
}

/**
 * Terapkan satu Patch_Event ke state. Duplikat diabaikan; celah versi atau
 * prasyarat ops yang gagal tidak mengubah content dan menandai `needsResync`.
 * Mengembalikan objek `state` yang sama bila tidak ada perubahan.
 */
export function applyPatch(state: DashboardState, patch: PatchEvent): DashboardState {
  return applyPatchWithOutcome(state, patch).state;
}

function loadSnapshot(state: DashboardState, snapshot: DashboardSnapshot): DashboardState {
  const current = state.snapshot;
  // Snapshot lebih lama dari state lokal Dashboard yang sama (respons terlambat) diabaikan.
  if (current !== null && current.id === snapshot.id && snapshot.version < current.version) {
    return state;
  }
  const stillBehind =
    current !== null &&
    current.id === snapshot.id &&
    state.knownServerVersion !== null &&
    snapshot.version < state.knownServerVersion;
  return {
    ...state,
    snapshot,
    needsResync: stillBehind,
    resyncReason: stillBehind ? state.resyncReason : null,
    knownServerVersion: stillBehind ? state.knownServerVersion : null,
    historyApproximate: false,
    lastPatchId: current !== null && current.id === snapshot.id ? state.lastPatchId : null,
    notice: current !== null && current.id === snapshot.id ? state.notice : null,
  };
}

function receivePatches(state: DashboardState, response: PatchesSinceResponse): DashboardState {
  if (response.snapshot) return loadSnapshot(state, response.snapshot);
  if (state.snapshot === null) return state;

  const patches = [...(response.patches ?? [])].sort((a, b) => a.version - b.version);
  let next = state;
  let failed = false;
  for (const patch of patches) {
    const result = applyPatchWithOutcome(next, patch);
    next = result.state;
    if (result.outcome === "gap" || result.outcome === "invalid") {
      failed = true;
      break;
    }
  }
  if (failed || next.snapshot === null) return next;

  const target = Math.max(response.version ?? 0, next.knownServerVersion ?? 0);
  if (next.snapshot.version < target) {
    return markResync(next, "version_gap", target);
  }
  // Riwayat tersambung sampai versi server terbaru: resync selesai.
  if (!next.needsResync && next.knownServerVersion === null) return next;
  return { ...next, needsResync: false, resyncReason: null, knownServerVersion: null };
}

export function dashboardReducer(state: DashboardState, action: DashboardAction): DashboardState {
  switch (action.type) {
    case "snapshotLoaded":
      return loadSnapshot(state, action.snapshot);
    case "patchReceived":
      return applyPatch(state, action.patch);
    case "patchesReceived":
      return receivePatches(state, action.response);
    case "conflict": {
      const marked = markResync(state, "conflict", action.currentVersion ?? null);
      return {
        ...marked,
        resyncReason: "conflict",
        notice: { kind: "conflict", message: CONFLICT_MESSAGE },
      };
    }
    case "resyncRequested":
      return markResync(state, action.reason ?? "requested");
    case "noticeDismissed":
      return state.notice === null ? state : { ...state, notice: null };
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// Resync (I/O diinjeksikan)
// ---------------------------------------------------------------------------

export interface ResyncFetchers {
  /** Mis. `getDashboard` dari `api.ts`. */
  getDashboard: (dashboardId: string) => Promise<DashboardSnapshot>;
  /** Mis. `getPatchesSince` dari `api.ts`. */
  getPatchesSince: (dashboardId: string, sinceVersion: number) => Promise<PatchesSinceResponse>;
}

/**
 * Ambil data untuk menyelaraskan state dan kembalikan action yang harus
 * di-dispatch. Konflik 409 dan ops tidak valid selalu me-refetch snapshot
 * (Req 18.6); celah versi mencoba `patches_since` lebih dulu (Req 16.5).
 * Mengembalikan `null` bila snapshot belum pernah dimuat.
 */
export async function resyncDashboard(
  state: DashboardState,
  fetchers: ResyncFetchers,
): Promise<DashboardAction | null> {
  const snap = state.snapshot;
  if (snap === null) return null;
  if (state.resyncReason === "conflict" || state.resyncReason === "invalid_op") {
    return { type: "snapshotLoaded", snapshot: await fetchers.getDashboard(snap.id) };
  }
  const response = await fetchers.getPatchesSince(snap.id, snap.version);
  if (response.snapshot) return { type: "snapshotLoaded", snapshot: response.snapshot };
  return { type: "patchesReceived", response };
}

// ---------------------------------------------------------------------------
// Selector
// ---------------------------------------------------------------------------

export function dashboardVersion(state: DashboardState): number | null {
  return state.snapshot?.version ?? null;
}
