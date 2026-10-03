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
import { PageNav } from "./PageNav";
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
import { Alert, AlertAction, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

// Alert tidak punya varian warn; token semantik warn dipakai lewat className.
const WARN_ALERT = "mb-3 border-transparent bg-warn-soft text-warn *:data-[slot=alert-description]:text-warn";

export interface WorkspaceStudioProps {
  workspaceId: string;
  deps?: StudioDeps;
}

const PAGE_PARAM = "page";

function pageFromUrl(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get(PAGE_PARAM);
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
  const [semanticPending, setSemanticPending] = useState(0);
  const [creating, setCreating] = useState(false);
  /** Halaman aktif (dari `?page=`); bila tak dikenal dipakai halaman tertua. */
  const [activeId, setActiveId] = useState<string | null>(pageFromUrl);
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
    (id: string | null) => {
      if (id === null || loadedDashboardIdRef.current === id) return;
      loadedDashboardIdRef.current = id;
      void loadSnapshot(id);
    },
    [loadSnapshot],
  );

  /** Id halaman yang dikenal dari detail Workspace terakhir. */
  const knownPagesRef = useRef<Set<string>>(new Set());

  const selectPage = useCallback(
    (id: string) => {
      setActiveId(id);
      setCrossFilters([]);
      const url = new URL(window.location.href);
      url.searchParams.set(PAGE_PARAM, id);
      window.history.replaceState(null, "", url);
      loadDashboardIfNeeded(id);
    },
    [loadDashboardIfNeeded],
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
            const patch = event.data as PatchEvent;
            // Halaman baru (mis. dibuat agent): muat ulang daftar halaman.
            if (!knownPagesRef.current.has(patch.dashboard_id)) reloadDetailRef.current();
            // Reducer mengabaikan patch milik Dashboard lain.
            dispatch({ type: "patchReceived", patch });
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
      knownPagesRef.current = new Set(detail.dashboards.map((d) => d.id));
      const pageId = detail.dashboards.some((d) => d.id === activeId)
        ? activeId
        : (detail.dashboards[0]?.id ?? null);
      loadDashboardIfNeeded(pageId);

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
            <Alert className={WARN_ALERT}>
              <AlertDescription>{dashState.notice.message}</AlertDescription>
              <AlertAction>
                <Button variant="link" size="xs" onClick={() => dispatch({ type: "noticeDismissed" })}>
                  Tutup
                </Button>
              </AlertAction>
            </Alert>
          )}
          <div ref={canvasRef}>
            <Canvas
              snapshot={snapshot}
              crossFilters={crossFilters}
              datasetVersions={datasetVersions}
              datasets={detail.datasets}
              onCrossFiltersChange={setCrossFilters}
              renderKey={renderKey}
              onAskAgent={(message) => void chatRef.current?.send(message)}
              client={deps.canvas}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-input px-4 py-10 text-center">
          <p className="text-sm text-muted-foreground">
            {detail.dashboards.length === 0
              ? "Belum ada Dashboard di Workspace ini."
              : "Memuat Dashboard"}
          </p>
          {detail.dashboards.length === 0 && (
            <Button
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
              {creating ? "Membuat" : "Buat Dashboard"}
            </Button>
          )}
        </div>
      );

      const pagesSlot = detail.dashboards.length ? (
        <PageNav
          pages={detail.dashboards}
          activeId={pageId}
          busy={creating}
          onSelect={selectPage}
          onCreate={() => {
            setCreating(true);
            setStudioError(null);
            deps
              .createDashboard(workspaceId, `Halaman ${detail.dashboards.length + 1}`)
              .then((snap) => {
                dispatch({ type: "snapshotLoaded", snapshot: snap });
                loadedDashboardIdRef.current = snap.id;
                selectPage(snap.id);
                reload();
              })
              .catch((err: unknown) =>
                setStudioError(err instanceof Error ? err.message : "Gagal membuat halaman."),
              )
              .finally(() => setCreating(false));
          }}
          onRename={(id, title) => {
            if (!snapshot || snapshot.id !== id) return;
            deps.canvas
              .command(id, snapshot.version, { type: "set_title", title })
              .then((patch) => {
                dispatch({ type: "patchReceived", patch });
                reload();
              })
              .catch((err: unknown) =>
                setStudioError(err instanceof Error ? err.message : "Gagal mengganti nama."),
              );
          }}
          onDelete={(id) => {
            deps
              .deleteDashboard(id)
              .then(() => {
                const next = detail.dashboards.find((d) => d.id !== id);
                if (next) selectPage(next.id);
                reload();
              })
              .catch((err: unknown) =>
                setStudioError(err instanceof Error ? err.message : "Gagal menghapus halaman."),
              );
          }}
        />
      ) : undefined;

      return {
        pages: pagesSlot,
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
          <SemanticPanel
            workspaceId={workspaceId}
            refreshKey={semanticKey}
            client={deps.semantic}
            onCandidatesChange={setSemanticPending}
          />
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
          <p className="text-sm text-muted-foreground">Buat Dashboard untuk mulai memfilter.</p>
        ),
        canvas: canvasSlot,
        chat: (
          <ChatPanel
            ref={chatRef}
            workspaceId={workspaceId}
            sessionId={chatSessionId}
            client={deps.chat}
            datasets={detail.datasets}
            onPatch={(patch) => dispatch({ type: "patchReceived", patch })}
            onRelationsChanged={() => void reloadRelations()}
            onSessionChange={setChatSessionId}
            dashboardId={snapshot?.id ?? null}
          />
        ),
      };
    },
    [
      activeId,
      selectPage,
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
        pending={{
          relations: relations.filter((r) => r.status === "candidate").length,
          semantic: semanticPending,
        }}
      />
      {studioError && (
        <Alert className={WARN_ALERT}>
          <AlertDescription>{studioError}</AlertDescription>
        </Alert>
      )}
    </>
  );
}
