"use client";

/**
 * Canvas_Editor berbasis OpenUI (docs/plan-openui-canvas.md D1–D8).
 *
 * Program OpenUI Lang tersimpan dirender `Renderer` dengan library custom
 * (`openui/library.tsx`). Data diambil runtime OpenUI lewat `toolProvider`
 * yang membatch semua `Query` ke `/canvas/render`; filter adalah state host.
 * Edit manual (hapus, pindah, lebar, tipe chart, slicer) dikirim sebagai
 * patch `apply_canvas_patch` / `set_global_filters` dengan `source = user`.
 */
import { useCallback, useMemo, useState } from "react";
import { Renderer, type OpenUIError } from "@openuidev/react-lang";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { setColumnFilter, toggleCrossFilter } from "@/lib/filters";
import type { DashboardSnapshot, Dataset, FilterSet, Predicate, Scalar } from "@/lib/types";
import { canvasLibrary } from "@/openui/library";
import { CanvasHostProvider, type CanvasHost } from "@/openui/host";
import { programText, removePatch, statementTitle } from "@/openui/program";
import { createCanvasToolProvider } from "@/openui/tool-provider";
import { defaultCanvasClient, errorMessage, resolveCrossFilterTable, type CanvasClient } from "./client";

export interface OpenUICanvasProps {
  snapshot: DashboardSnapshot;
  crossFilters: Predicate[];
  datasetVersions?: Readonly<Record<string, number>>;
  datasets?: readonly Dataset[];
  onCrossFiltersChange?: (filters: FilterSet) => void;
  /** Berubah (mis. `data_version` naik) → semua Query di-fetch ulang. */
  renderKey?: number | string;
  client?: CanvasClient;
  /** `false` = read-only (tanpa menu/drag/resize). */
  editable?: boolean;
}

export function OpenUICanvas({
  snapshot,
  crossFilters,
  datasetVersions = {},
  datasets = [],
  onCrossFiltersChange,
  renderKey = 0,
  client = defaultCanvasClient,
  editable = true,
}: OpenUICanvasProps) {
  const { content, version, id } = snapshot;
  const statements = useMemo(() => content.statements ?? {}, [content.statements]);
  const program = useMemo(() => programText(statements), [statements]);
  const [error, setError] = useState<string | null>(null);
  const [programErrors, setProgramErrors] = useState<OpenUIError[]>([]);
  const [removeName, setRemoveName] = useState<string | null>(null);
  const [verified, setVerified] = useState<ReadonlySet<string>>(() => new Set());
  const [dragging, setDragging] = useState<string | null>(null);

  // Identitas baru → QueryManager baru → semua Query di-fetch ulang (filter/versi/data).
  const toolProvider = useMemo(() => {
    const render = client.renderCanvas;
    if (!render) return null;
    return createCanvasToolProvider({ dashboardId: id, crossFilters, render: (d, b) => render(d, b) });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- version & renderKey memicu refetch
  }, [client, id, crossFilters, version, renderKey]);

  const send = useCallback(
    (command: Parameters<CanvasClient["command"]>[2]) =>
      client.command(id, version, command).then(
        () => setError(null),
        (err: unknown) => setError(errorMessage(err)),
      ),
    [client, id, version],
  );

  const host: CanvasHost = useMemo(
    () => ({
      dashboardId: id,
      version,
      statements,
      itemStatus: snapshot.item_status,
      globalFilters: content.global_filters,
      crossFilters,
      datasets,
      datasetVersions,
      toggleCrossFilter(column: string, value: Scalar) {
        const table = resolveCrossFilterTable(datasets, column);
        if (!table || !onCrossFiltersChange) return;
        onCrossFiltersChange(toggleCrossFilter(crossFilters, { table, column, value }));
      },
      setColumnFilter(column: string, next: Predicate | null) {
        const table = next?.table ?? resolveCrossFilterTable(datasets, column);
        if (!table) return;
        void send({ type: "set_global_filters", filters: setColumnFilter(content.global_filters, table, column, next) });
      },
      applyPatch(patch: string) {
        if (patch.trim()) void send({ type: "apply_canvas_patch", patch });
      },
      requestRemove: setRemoveName,
      verify: client.verify
        ? (name: string) =>
            client.verify!(id, name).then(
              () => setVerified((prev) => new Set(prev).add(name)),
              (err: unknown) => setError(errorMessage(err)),
            )
        : undefined,
      verified,
      dragging,
      setDragging,
      editable,
    }),
    [id, version, statements, snapshot.item_status, content.global_filters, crossFilters, datasets,
      datasetVersions, onCrossFiltersChange, send, client, verified, dragging, editable],
  );

  const tileCount = Object.values(statements).filter(
    (s) => s.kind === "component" && s.component && !["VContainer", "HContainer", "Section", "Tabs"].includes(s.component),
  ).length;
  const hasFilters = content.global_filters.length > 0 || crossFilters.length > 0;
  const blocking = programErrors.filter((e) => e.source === "parser" && e.severity !== "warning");

  return (
    <div className="flex min-h-0 flex-col gap-4" data-canvas-version={version} data-canvas-engine="openui">
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h2 className="m-0 truncate text-xl leading-tight font-semibold tracking-[-0.01em]" title={content.title}>
            {content.title}
          </h2>
          <p className="flex flex-wrap items-center gap-x-2 text-[0.8125rem] text-muted-foreground tabular-nums">
            <span>v{version}</span>
            <span aria-hidden>·</span>
            <span>{tileCount} tile</span>
            {hasFilters && (
              <>
                <span aria-hidden>·</span>
                <span className="font-medium text-primary">
                  {content.global_filters.length + crossFilters.length} filter aktif
                </span>
              </>
            )}
          </p>
        </div>
        {hasFilters && (
          <div className="flex items-center gap-1.5" data-export-hide>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                onCrossFiltersChange?.([]);
                if (content.global_filters.length) void send({ type: "set_global_filters", filters: [] });
              }}
            >
              Reset semua filter
            </Button>
          </div>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {blocking.length > 0 && (
        <Alert variant="destructive" data-export-hide>
          <AlertDescription>
            Sebagian canvas tidak dapat ditampilkan: {blocking[0].message}
          </AlertDescription>
        </Alert>
      )}

      {program ? (
        <CanvasHostProvider value={host}>
          <Renderer
            library={canvasLibrary}
            response={program}
            toolProvider={toolProvider}
            queryLoader={null}
            onError={setProgramErrors}
          />
        </CanvasHostProvider>
      ) : (
        <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-input px-4 py-14 text-center">
          <p className="font-medium">Dashboard masih kosong.</p>
          <p className="text-[0.8125rem] text-muted-foreground">
            Minta agent di panel chat untuk menyusun dashboard dari data ini.
          </p>
        </div>
      )}

      <AlertDialog open={removeName !== null} onOpenChange={(open) => !open && setRemoveName(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus &ldquo;{removeName ? statementTitle(statements, removeName) : ""}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>Tile hilang dari dashboard. Batalkan lewat Undo bila perlu.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                const patch = removeName ? removePatch(statements, removeName) : null;
                if (patch) void send({ type: "apply_canvas_patch", patch });
                setRemoveName(null);
              }}
            >
              Hapus
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
