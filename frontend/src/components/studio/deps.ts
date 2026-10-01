/**
 * Port data untuk perangkai halaman Workspace (task 23.1).
 *
 * Semua dependensi eksternal (klien REST + SSE) dikumpulkan di satu objek
 * `StudioDeps` agar mudah diganti implementasi in-memory di test; default-nya
 * memakai klien REST `lib/api` dan `lib/sse`.
 */

import {
  createDashboard,
  getDashboard,
  getPatchesSince,
} from "@/lib/api";
import type { EventSourceLike } from "@/lib/sse";
import type { CanvasClient } from "@/components/canvas";
import type { ChatClient } from "@/components/chat";
import type { DatasetClient } from "@/components/datasets";
import type { ExportClient } from "@/components/export";
import type { FilterClient } from "@/components/filters";
import type { RelationClient } from "@/components/relations";
import type { WorkspaceClient } from "@/components/workspaces";
import { defaultCanvasClient } from "@/components/canvas";
import { defaultChatClient } from "@/components/chat";
import { defaultDatasetClient } from "@/components/datasets";
import { defaultExportClient } from "@/components/export";
import { defaultFilterClient } from "@/components/filters";
import { defaultRelationClient } from "@/components/relations";
import { defaultWorkspaceClient } from "@/components/workspaces";
import { defaultSemanticClient, type SemanticClient } from "@/components/semantic";
import { defaultBriefClient, type BriefClient } from "@/components/brief";

export interface StudioDeps {
  workspace: WorkspaceClient;
  canvas: CanvasClient;
  chat: ChatClient;
  datasets: DatasetClient;
  export: ExportClient;
  filters: FilterClient;
  relations: RelationClient;
  semantic: SemanticClient;
  brief: BriefClient;
  /** Snapshot & patch Dashboard (resync). */
  getDashboard: (dashboardId: string) => Promise<import("@/lib/types").DashboardSnapshot>;
  getPatchesSince: (
    dashboardId: string,
    sinceVersion: number,
  ) => Promise<import("@/lib/types").PatchesSinceResponse>;
  createDashboard: (ws: string, title: string) => Promise<import("@/lib/types").DashboardSnapshot>;
  /** Pabrik EventSource workspace (injeksi untuk test). */
  eventSourceFactory?: (url: string) => EventSourceLike;
}

export const defaultStudioDeps: StudioDeps = {
  workspace: defaultWorkspaceClient,
  canvas: defaultCanvasClient,
  chat: defaultChatClient,
  datasets: defaultDatasetClient,
  export: defaultExportClient,
  filters: defaultFilterClient,
  relations: defaultRelationClient,
  semantic: defaultSemanticClient,
  brief: defaultBriefClient,
  getDashboard,
  getPatchesSince,
  createDashboard: (ws, title) => createDashboard(ws, title),
};
