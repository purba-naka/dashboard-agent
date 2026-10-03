import { ApiError } from "@/lib/api";
import type { Workspace, WorkspaceDetail, WorkspaceSummary } from "@/lib/types";
import type { WorkspaceClient } from "./client";

/**
 * WorkspaceClient in-memory untuk test komponen; meniru aturan backend
 * (nama di-trim & tidak kosong, 404 untuk id tak dikenal, `confirm_name`
 * harus sama persis → 422 `VALIDATION_ERROR`).
 */
export function createMemoryClient(
  initial: WorkspaceSummary[] = [],
  details: Record<string, Partial<Omit<WorkspaceDetail, "workspace">>> = {},
) {
  const store = new Map(initial.map((w) => [w.id, { ...w }]));
  let seq = 0;
  const calls: { remove: Array<[string, string]> } = { remove: [] };

  const find = (id: string) => {
    const ws = store.get(id);
    if (!ws) throw new ApiError(404, "NOT_FOUND", "Workspace tidak ditemukan.");
    return ws;
  };
  const checkName = (raw: string) => {
    const name = raw.trim();
    if (!name) throw new ApiError(422, "VALIDATION_ERROR", "Nama tidak boleh kosong.");
    return name;
  };

  const client: WorkspaceClient = {
    async list() {
      return [...store.values()].map((w) => ({ ...w }));
    },
    async create(raw) {
      const now = new Date().toISOString();
      const ws: Workspace = {
        id: `ws_${++seq}`,
        owner_id: "local",
        name: checkName(raw),
        created_at: now,
        updated_at: now,
      };
      store.set(ws.id, { ...ws, dataset_count: 0, dashboard_count: 0, last_activity_at: now });
      return ws;
    },
    async get(id) {
      const { id: wsId, owner_id, name, created_at, updated_at } = find(id);
      const workspace = { id: wsId, owner_id, name, created_at, updated_at };
      return { workspace, datasets: [], dashboards: [], chat_sessions: [], ...details[id] };
    },
    async rename(id, raw) {
      const ws = find(id);
      ws.name = checkName(raw);
      ws.updated_at = ws.last_activity_at = new Date().toISOString();
      const { id: wsId, owner_id, name, created_at, updated_at } = ws;
      return { id: wsId, owner_id, name, created_at, updated_at };
    },
    async remove(id, confirmName) {
      calls.remove.push([id, confirmName]);
      const ws = find(id);
      if (confirmName !== ws.name) {
        throw new ApiError(422, "VALIDATION_ERROR", "Nama konfirmasi tidak cocok.");
      }
      store.delete(id);
    },
  };

  return { client, store, calls };
}

export function makeWorkspace(
  id: string,
  name: string,
  extra: Partial<WorkspaceSummary> = {},
): WorkspaceSummary {
  return {
    id,
    owner_id: "local",
    name,
    created_at: "2024-01-31T10:00:00Z",
    updated_at: "2024-01-31T10:00:00Z",
    dataset_count: 0,
    dashboard_count: 0,
    last_activity_at: "2024-01-31T10:00:00Z",
    ...extra,
  };
}
