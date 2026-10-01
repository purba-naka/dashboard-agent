"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Canvas } from "@/components/canvas";
import { ChatPanel, type ChatPanelHandle } from "@/components/chat";
import { DatasetsPanel } from "@/components/datasets";
import { ExportBar } from "@/components/export";
import { GlobalFilterPanel } from "@/components/filters";
import { RelationsPanel } from "@/components/relations";
import { SemanticPanel } from "@/components/semantic";
import { BriefPanel } from "@/components/brief";
import { WorkspaceView } from "@/components/workspaces";
import { connectWorkspaceEvents, type WorkspaceEventsSubscription } from "@/lib/sse";
import {
  dashboardReducer,
  initialDashboardState,
  resyncDashboard,
} from "@/lib/dashboard-state";
import type {
  FilterSet,
  PatchEvent,
  Relation,
  WorkspaceDetail,
} from "@/lib/types";
import { defaultStudioDeps, type StudioDeps } from "./deps";
import styles from "./studio.module.css";

export interface WorkspaceStudioProps {
  workspaceId: string;
  deps?: StudioDeps;
}

/** Giliran chat pemicu setelah upload selesai (Req 21.1). */
function uploadTriggerMessage(datasetName: string): string {
  return `Dataset "${datasetName}" baru saja selesai diunggah. Tampilkan ringkasan profilnya, lalu tawarkan kandidat relasi dengan dataset lain bila ada.`;
}

/**
 * Perangkai halaman Workspace (task 23.1): satu state store Dashboard
 * (`dashboardReducer`) yang diisi dari SSE workspace dan chat, panel
 * dataset/relasi/filter, Canvas_Editor, Export_Service, dan Chat_Panel.
 * Render ulang chart dipicu oleh patch (versi), filter, Cross_Filter, atau
 * `data_version` Dataset yang berubah (Req 1.2, 6.4, 16.5, 18.3, 21.1, 22.3,
 * 24.4, 26.2).
 */
export function WorkspaceStudio({ workspaceId, deps = defaultStudioDeps }: WorkspaceStudioProps) {
  const [dashState, dispatch] = useReducer(dashboardReducer, initialDashboardState);
  const [relations, setRelations] = useState<Relation[]>([]);
  const [crossFilters, setCrossFilters] = useState<FilterSet>([]);
  const [chatSessionId, setChatSessionId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [studioError, setStudioError] = useState<string | null>(null);
  /** Naik saat `semantic.updated` → SemanticPanel memuat ulang. */
  const [semanticKey, setSemanticKey] = useState(0);

  const chatRef = useRef<ChatPanelHandle | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);

  const depsRef = useRef(deps);

  useEffect(() => {
    depsRef.current = deps;
  });

  /** Reload detail Workspace milik WorkspaceView (dipasang saat render slot). */
  const reloadDetailRef = useRef<() => void>(() => {});

  // -----------------------------------------------------------------------
  // Data: relasi & snapshot Dashboard aktif
  // -----------------------------------------------------------------------

  const reloadRelations = useCallback(async () => {
    try {
      setRelations(await deps.relations.list(workspaceId));
    } catch {
      /* daftar relasi dipertahankan; SSE/aksi pengguna akan menyegarkan */
    }
  }, [deps.relations, workspaceId]);

  const reloadRelationsRef = useRef(reloadRelations);
  useEffect(() => {
    reloadRelationsRef.current = reloadRelations;
  });

  useEffect(() => {
    let cancelled = false;
    deps.relations
      .list(workspaceId)
      .then((next) => {
        if (!cancelled) setRelations(next);
      })
      .catch(() => {
        /* daftar relasi dipertahankan; SSE/aksi pengguna akan menyegarkan */
      });
    return () => {
      cancelled = true;
    };
  }, [deps.relations, workspaceId]);

  const loadSnapshot = useCallback(
    async (dashboardId: string) => {
      try {
        const snapshot = await deps.getDashboard(dashboardId);
        dispatch({ type: "snapshotLoaded", snapshot });
      } catch (err) {
        setStudioError(err instanceof Error ? err.message : "Gagal memuat Dashboard.");
      }
    },
    [deps],
  );

  /** Id Dashboard aktif yang sudah/sedang dimuat snapshot-nya. */
  const loadedDashboardIdRef = useRef<string | null>(null);
  const loadDashboardIfNeeded = useCallback(
    (dashboards: ReadonlyArray<{ id: string }>) => {
      const id = dashboards[0]?.id ?? null;
      if (id === null || loadedDashboardIdRef.current === id) return;
      loadedDashboardIdRef.current = id;
      void loadSnapshot(id);
    },
    [loadSnapshot],
  );

  // -----------------------------------------------------------------------
  // Langganan SSE workspace (Req 16.5, 21.1, 26.2)
  // -----------------------------------------------------------------------

  useEffect(() => {
    const subscription: WorkspaceEventsSubscription = connectWorkspaceEvents(
      workspaceId,
      {
        onEvent: (event) => {
          if (event.event === "patch.applied") {
            // Reducer mengabaikan patch milik Dashboard lain.
            dispatch({ type: "patchReceived", patch: event.data as PatchEvent });
            return;
          }
          if (event.event === "job.done") {
            // Muat ulang detail (data_version naik → render ulang chart,
            // Req 26.2), lalu kirim giliran chat pemicu (Req 21.1).
            const { dataset_id } = event.data as { job_id: string; dataset_id: string };
            void (async () => {
              reloadDetailRef.current();
              try {
                const detail = await depsRef.current.workspace.get(workspaceId);
                reloadDetailRef.current();
                const name =
                  detail.datasets.find((d) => d.id === dataset_id)?.source_name ?? "dataset";
                await chatRef.current?.send(uploadTriggerMessage(name));
              } catch {
                /* trigger chat opsional; jangan ganggu UI */
              }
            })();
            return;
          }
          if (event.event === "relation.updated") {
            void reloadRelationsRef.current();
            return;
          }
          if (event.event === "semantic.updated" || event.event === "semantic.warning") {
            setSemanticKey((k) => k + 1);
            return;
          }
          if (event.event === "resync") {
            dispatch({ type: "resyncRequested", reason: "requested" });
          }
        },
        onReconnect: () => {
          dispatch({ type: "resyncRequested", reason: "requested" });
        },
      },
      { eventSourceFactory: deps.eventSourceFactory },
    );
    return () => subscription.close();
    // `deps.eventSourceFactory` identitas stabil; handler memakai ref.
  }, [workspaceId, deps.eventSourceFactory]);

  // -----------------------------------------------------------------------
  // Resync otomatis saat state lokal tertinggal (Req 16.5, 18.6)
  // -----------------------------------------------------------------------

  useEffect(() => {
    if (!dashState.needsResync || dashState.snapshot === null) return;
    let cancelled = false;
    resyncDashboard(dashState, {
      getDashboard: deps.getDashboard,
      getPatchesSince: deps.getPatchesSince,
    })
      .then((action) => {
        if (!cancelled && action) dispatch(action);
      })
      .catch(() => {
        /* `needsResync` tetap menyala; dicoba lagi pada event berikutnya */
      });
    return () => {
      cancelled = true;
    };
  }, [dashState, deps]);

  // -----------------------------------------------------------------------
  // Slot WorkspaceView (detail + reload)
  // -----------------------------------------------------------------------

  const renderSlots = useCallback(
    ({ detail, reload }: { detail: WorkspaceDetail; reload: () => void }) => {
      reloadDetailRef.current = reload;
      loadDashboardIfNeeded(detail.dashboards);

      const snapshot = dashState.snapshot;
      const datasetVersions: Record<string, number> = {};
      for (const d of detail.datasets) datasetVersions[d.id] = d.data_version;
      const renderKey = detail.datasets.map((d) => `${d.id}:${d.data_version}`).join("|");

      const exportSlot = snapshot ? (
        <ExportBar
          canvasRef={canvasRef}
          title={snapshot.content.title}
          filters={snapshot.content.global_filters}
          client={deps.export}
        />
      ) : null;

      const canvasSlot = snapshot ? (
        <>
          {dashState.notice && (
            <p role="alert" className={styles.notice}>
              {dashState.notice.message}{" "}
              <button
                type="button"
                className={styles.linkButton}
                onClick={() => dispatch({ type: "noticeDismissed" })}
              >
                Tutup
              </button>
            </p>
          )}
          <div ref={canvasRef}>
            <Canvas
              snapshot={snapshot}
              crossFilters={crossFilters}
              datasetVersions={datasetVersions}
              datasets={detail.datasets}
              onCrossFiltersChange={setCrossFilters}
              renderKey={renderKey}
              client={deps.canvas}
            />
          </div>
        </>
      ) : (
        <div className={styles.create}>
          <p className={styles.muted}>
            {detail.dashboards.length === 0
              ? "Belum ada Dashboard di Workspace ini."
              : "Memuat Dashboard…"}
          </p>
          {detail.dashboards.length === 0 && (
            <button
              type="button"
              disabled={creating}
              onClick={() => {
                setCreating(true);
                setStudioError(null);
                deps
                  .createDashboard(workspaceId, "Dashboard")
                  .then((snap) => {
                    dispatch({ type: "snapshotLoaded", snapshot: snap });
                    loadedDashboardIdRef.current = snap.id;
                    reload();
                  })
                  .catch((err: unknown) => {
                    setStudioError(
                      err instanceof Error ? err.message : "Gagal membuat Dashboard.",
                    );
                  })
                  .finally(() => setCreating(false));
              }}
            >
              {creating ? "Membuat…" : "Buat Dashboard"}
            </button>
          )}
        </div>
      );

      return {
        export: exportSlot,
        datasets: (
          <DatasetsPanel
            workspaceId={workspaceId}
            datasets={detail.datasets}
            onDatasetsChanged={reload}
            client={deps.datasets}
          />
        ),
        relations: (
          <RelationsPanel
            workspaceId={workspaceId}
            relations={relations}
            datasets={detail.datasets}
            onRelationsChange={setRelations}
            client={deps.relations}
          />
        ),
        semantic: (
          <SemanticPanel workspaceId={workspaceId} refreshKey={semanticKey} client={deps.semantic} />
        ),
        brief: snapshot ? <BriefPanel snapshot={snapshot} client={deps.brief} /> : undefined,
        filters: snapshot ? (
          <GlobalFilterPanel
            workspaceId={workspaceId}
            snapshot={snapshot}
            datasets={detail.datasets}
            crossFilters={crossFilters}
            onCrossFiltersChange={setCrossFilters}
            client={deps.filters}
          />
        ) : (
          <p className={styles.muted}>Buat Dashboard untuk mulai memfilter.</p>
        ),
        canvas: canvasSlot,
        chat: (
          <ChatPanel
            ref={chatRef}
            workspaceId={workspaceId}
            sessionId={chatSessionId}
            client={deps.chat}
            onPatch={(patch) => dispatch({ type: "patchReceived", patch })}
            onRelationsChanged={() => void reloadRelations()}
            onSessionChange={setChatSessionId}
          />
        ),
      };
    },
    [
      chatSessionId,
      creating,
      crossFilters,
      deps,
      dashState,
      loadDashboardIfNeeded,
      relations,
      reloadRelations,
      semanticKey,
      workspaceId,
    ],
  );

  return (
    <>
      <WorkspaceView
        workspaceId={workspaceId}
        client={deps.workspace}
        renderSlots={renderSlots}
        activeSessionId={chatSessionId}
        onSelectSession={setChatSessionId}
      />
      {studioError && (
        <p role="alert" className={styles.notice}>
          {studioError}
        </p>
      )}
    </>
  );
}
