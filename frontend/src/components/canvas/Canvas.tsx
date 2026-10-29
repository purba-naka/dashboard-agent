"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useContainerWidth, GridLayout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  DotsSixVerticalIcon,
  DotsThreeIcon,
  SealCheckIcon,
  SquaresFourIcon,
  TrashIcon,
} from "@phosphor-icons/react/ssr";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { InsightCard } from "@/components/insights";
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
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { KpiTile } from "./KpiTile";
import { toggleCrossFilter } from "@/lib/filters";
import { normalizeChart } from "@/lib/chart-normalize";
import { useChartTheme } from "@/lib/echarts-theme";
import type {
  DashboardSnapshot,
  Dataset,
  FilterSet,
  LayoutRect,
  Predicate,
  RenderedItem,
} from "@/lib/types";
import { GRID_COLUMNS } from "@/lib/dashboard-state";
import { isApiError } from "@/lib/api";
import {
  chartElementValue,
  defaultCanvasClient,
  errorMessage,
  resolveCrossFilterTable,
  sortedItemIds,
  type CanvasClient,
} from "./client";

export interface CanvasProps {
  snapshot: DashboardSnapshot;
  /** Global_Filter aktif (untuk insight) + Cross_Filter untuk render. */
  crossFilters: Predicate[];
  /** `dataset_id -> data_version` terbaru (badge stale insight). */
  datasetVersions?: Readonly<Record<string, number>>;
  client?: CanvasClient;
  /** Render insight di dalam canvas. */
  renderInsight?: boolean;
  /** Daftar Dataset Workspace (resolusi tabel Cross_Filter). */
  datasets?: readonly Dataset[];
  /** Toggle/hapus Cross_Filter dari klik elemen chart (Req 24.1). */
  onCrossFiltersChange?: (filters: FilterSet) => void;
  /** Berubah (mis. `data_version` Dataset naik) → render ulang chart (Req 26.2). */
  renderKey?: number | string;
  /** Kirim permintaan ke agent chat (mis. tombol "Buat KPI"). */
  onAskAgent?: (message: string) => void;
}

const ROW_HEIGHT = 48;
/** Kartu: elevasi lewat permukaan; border naik saat hover/fokus. Aksi muncul lewat `group/tile`. */
const TILE =
  "group/tile relative flex flex-col overflow-hidden rounded-xl border bg-card shadow-(--shadow-sm) transition-colors [contain:layout_paint] hover:border-border-strong focus-within:border-border-strong";
/** Aksi sekunder: tersembunyi di perangkat ber-mouse sampai hover/fokus; selalu tampil di layar sentuh. */
const HOVER_REVEAL =
  "transition-opacity pointer-fine:opacity-0 pointer-fine:group-hover/tile:opacity-100 group-focus-within/tile:opacity-100 data-[state=open]:opacity-100";
/** Chart + insight yang tampil sekaligus; KPI selalu tampil. Sisanya di-collapse. */
const MAX_VISIBLE_VIEWS = 8;
const KPI_REQUEST =
  "Tambahkan 2-4 KPI utama di baris atas dashboard dari metrik model semantik, dengan pembanding awal periode.";
// Prop ECharts konstan: objek baru tiap render memicu resize/rebind di echarts-for-react.
const ECHARTS_OPTS = { renderer: "canvas" } as const;
const ECHARTS_STYLE = { height: "100%", minHeight: 0 } as const;
const DRAG_HANDLE = "canvas-drag-handle";
const PLACEHOLDER = "flex flex-1 items-center justify-center p-4 text-center text-[0.8125rem] text-muted-foreground";
/** Jarak antar kartu = gutter halaman; tanpa padding luar agar tepi kartu sejajar filter. */
const GRID_MARGIN = [16, 16] as const;
const GRID_PADDING = [0, 0] as const;

interface GridEntry {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * Canvas_Editor: grid 12 kolom (drag pindah, resize ubah ukuran), render chart
 * ECharts via `/render` (tanpa data inline di LLM), Insight_Card inline, badge
 * status item (`invalid`/`stale`), dan penanda Cross_Filter aktif + tombol
 * hapus per item (Req 22.1–22.3, 23.3, 24.3, 26.2).
 */
export function Canvas({
  snapshot,
  crossFilters,
  datasetVersions = {},
  client = defaultCanvasClient,
  renderInsight = true,
  datasets = [],
  onCrossFiltersChange,
  renderKey = 0,
  onAskAgent,
}: CanvasProps) {
  const { content, version } = snapshot;
  const { containerRef, width } = useContainerWidth();
  // Hasil /render disimpan sebagai state agar re-render saat selesai.
  const [rendered, setRendered] = useState<Record<string, RenderedItem>>({});
  const [renderError, setRenderError] = useState<string | null>(null);
  /** Naik saat pengguna menekan "Coba lagi" di tile gagal: memicu /render ulang. */
  const [retryKey, setRetryKey] = useState(0);
  const stableRetry = useCallback(() => setRetryKey((k) => k + 1), []);
  /** `query_id` insight yang sedang di-hover: chart bersumber sama disorot. */
  const [hoverQuery, setHoverQuery] = useState<string | null>(null);

  const ids = useMemo(() => sortedItemIds(content.layout), [content.layout]);
  const [expanded, setExpanded] = useState(false);
  // Urutan baca = urutan layout (kiri atas dulu); KPI tidak dihitung ke batas.
  const { visibleIds, hiddenCount, hasKpi } = useMemo(() => {
    let views = 0;
    let kpi = false;
    const visible: string[] = [];
    for (const id of ids) {
      if (content.items[id]?.kind === "kpi") {
        kpi = true;
        visible.push(id);
      } else if (expanded || views < MAX_VISIBLE_VIEWS) {
        views++;
        visible.push(id);
      }
    }
    return { visibleIds: visible, hiddenCount: ids.length - visible.length, hasKpi: kpi };
  }, [ids, content.items, expanded]);

  // Render ulang saat patch (version), filter, Cross_Filter, atau data sumber
  // berubah (Req 18.3, 22.3, 26.2).
  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;
    client
      .render(snapshot.id, ids, crossFilters, { signal: ctrl.signal })
      .then((res) => {
        if (cancelled) return;
        setRendered(res.items);
        setRenderError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setRenderError(errorMessage(err));
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [client, snapshot.id, snapshot.version, ids, crossFilters, renderKey, retryKey]);

  // KPI, chart, insight dirender di section terpisah; urutan dalam section tetap urutan layout.
  // Pengelompokan hanya di layer render: layout tersimpan tidak dimigrasi.
  const groups = useMemo(() => {
    const kpi: string[] = [];
    const chart: string[] = [];
    const insight: string[] = [];
    for (const id of visibleIds) {
      const kind = content.items[id]?.kind;
      (kind === "kpi" ? kpi : kind === "insight" ? insight : chart).push(id);
    }
    return { kpi, chart, insight };
  }, [visibleIds, content.items]);

  /** Grid chart mulai di y=0; `chartBaseY` mengembalikan posisi ke koordinat tersimpan. */
  const chartBaseY = useMemo(
    () => (groups.chart.length ? Math.min(...groups.chart.map((id) => content.layout[id].y)) : 0),
    [groups.chart, content.layout],
  );

  function sendLayout(changes: Record<string, LayoutRect>) {
    if (Object.keys(changes).length === 0) return;
    client
      .command(snapshot.id, version, { type: "set_layout", changes })
      .catch(() => {
        // Gagal simpan tata letak → SSE `resync` akan memperbaiki state lokal.
      });
  }

  function tidyLayout() {
    client
      .command(snapshot.id, version, { type: "tidy_layout" })
      .then(() => toast.success("Tata letak dirapikan."))
      .catch((err: unknown) => {
        // NO_CHANGES = sudah rapi; selain itu SSE `resync` memperbaiki state lokal.
        if (!(isApiError(err) && err.code === "NO_CHANGES")) toast.error(errorMessage(err));
      });
  }

  // Simpan hanya setelah aksi pengguna (drag/resize). `onLayoutChange` juga terpanggil saat
  // grid memadatkan layout ketika mount; menyimpannya berarti menulis ulang data tanpa diminta.
  const beforeRef = useRef<readonly GridEntry[]>([]);
  function onInteractionStart(current: readonly GridEntry[]) {
    beforeRef.current = current;
  }
  function onInteractionStop(next: readonly GridEntry[]) {
    const before = new Map(beforeRef.current.map((e) => [e.i, e]));
    const changes: Record<string, LayoutRect> = {};
    for (const e of next) {
      const b = before.get(e.i);
      if (!b || (b.x === e.x && b.y === e.y && b.w === e.w && b.h === e.h)) continue;
      changes[e.i] = { x: e.x, y: e.y + chartBaseY, w: e.w, h: e.h };
    }
    sendLayout(changes);
  }

  /** Alternatif keyboard untuk drag: tukar posisi dengan tetangga di section yang sama. */
  // ponytail: tukar x/y, ukuran tetap. Backend (push_down_collisions) menggeser item yang bertabrakan,
  // jadi layout tersimpan tidak pernah tumpang tindih. Hasil akhir bisa meninggalkan celah vertikal.
  const moveRef = useRef<(id: string, dir: -1 | 1) => void>(() => {});
  useEffect(() => {
    moveRef.current = (id, dir) => {
      const kind = content.items[id]?.kind;
      const order = kind === "kpi" ? groups.kpi : kind === "insight" ? groups.insight : groups.chart;
      const other = order[order.indexOf(id) + dir];
      if (!other) return;
      const a = content.layout[id];
      const b = content.layout[other];
      const place = (r: LayoutRect, to: LayoutRect): LayoutRect => ({
        ...r,
        x: Math.min(to.x, GRID_COLUMNS - r.w),
        y: to.y,
      });
      sendLayout({ [id]: place(a, b), [other]: place(b, a) });
    };
  });
  const stableMove = useCallback((id: string, dir: -1 | 1) => moveRef.current(id, dir), []);

  /** Item yang menunggu konfirmasi hapus (AlertDialog). */
  const [removeId, setRemoveId] = useState<string | null>(null);

  function onRemove(itemId: string) {
    client
      .command(snapshot.id, version, { type: "remove_item", id: itemId })
      .catch(() => {
        /* sama seperti di atas */
      });
  }

  /** Klik elemen chart → toggle Cross_Filter pada kolom dimensi (Req 24.1). */
  function onChartElementClick(itemId: string, params: { name?: unknown; value?: unknown }) {
    if (!onCrossFiltersChange) return;
    const item = content.items[itemId];
    if (!item || item.kind !== "chart") return;
    const column = item.spec.cross_filter_column;
    if (!column) return;
    const value = chartElementValue(params);
    if (value === null) return;
    const table = resolveCrossFilterTable(datasets, column);
    if (!table) return;
    onCrossFiltersChange(toggleCrossFilter(crossFilters, { table, column, value }));
  }

  // Handler lewat ref agar identitasnya stabil untuk `Cell` (memo).
  const handlersRef = useRef({ onChartElementClick });
  useEffect(() => {
    handlersRef.current = { onChartElementClick };
  });
  const stableChartClick = useCallback(
    (id: string, params: { name?: unknown; value?: unknown }) =>
      handlersRef.current.onChartElementClick(id, params),
    [],
  );

  const [verifiedIds, setVerifiedIds] = useState<ReadonlySet<string>>(() => new Set());
  const verifyRef = useRef<(id: string) => void>(() => {});
  useEffect(() => {
    verifyRef.current = (itemId: string) => {
      client
        .verify?.(snapshot.id, itemId)
        .then(() => setVerifiedIds((prev) => new Set(prev).add(itemId)))
        .catch((err: unknown) => setRenderError(errorMessage(err)));
    };
  });
  const stableVerify = useCallback((id: string) => verifyRef.current(id), []);

  const hasFilters = content.global_filters.length > 0 || crossFilters.length > 0;
  /** Reset semua filter: Global_Filter via Dashboard_Store + kosongkan Cross_Filter (Req 22.8). */
  const resetFilters = () => {
    onCrossFiltersChange?.([]);
    if (content.global_filters.length === 0) return;
    client
      .command(snapshot.id, version, { type: "set_global_filters", filters: [] })
      .catch((err: unknown) => setRenderError(errorMessage(err)));
  };

  const layout: GridEntry[] = useMemo(
    () =>
      groups.chart.map((id) => {
        const rect = content.layout[id];
        return { i: id, x: rect.x, y: rect.y - chartBaseY, w: rect.w, h: rect.h };
      }),
    [groups.chart, content.layout, chartBaseY],
  );

  const renderCell = (id: string, order: readonly string[], draggable = false) => (
    <Cell
      id={id}
      snapshot={snapshot}
      rendered={rendered[id]}
      crossFilters={crossFilters}
      datasetVersions={datasetVersions}
      renderInsight={renderInsight}
      draggable={draggable}
      canMoveUp={order.indexOf(id) > 0}
      canMoveDown={order.indexOf(id) < order.length - 1}
      onMove={stableMove}
      onRetry={stableRetry}
      onRemove={setRemoveId}
      onChartElementClick={stableChartClick}
      onVerify={client.verify ? stableVerify : undefined}
      verified={verifiedIds.has(id)}
      highlighted={
        hoverQuery !== null &&
        content.items[id]?.kind === "chart" &&
        content.items[id].spec.query_id === hoverQuery
      }
      onHoverQuery={setHoverQuery}
    />
  );

  const removeTitle = removeId ? (content.items[removeId]?.title ?? removeId) : "";

  return (
    <div className="flex min-h-0 flex-col gap-4" data-canvas-version={version}>
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h2 className="m-0 truncate text-xl leading-tight font-semibold tracking-[-0.01em]" title={content.title}>
            {content.title}
          </h2>
          <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
            <span>v{version}</span>
            <span aria-hidden>·</span>
            <span>{ids.length} tile</span>
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
        <div className="flex flex-wrap items-center gap-1.5" data-export-hide>
          <Button
            variant="ghost"
            size="sm"
            disabled={ids.length === 0}
            aria-label="Rapikan tata letak"
            title="Rapikan tata letak"
            onClick={tidyLayout}
            data-slot="canvas-tidy"
          >
            <SquaresFourIcon aria-hidden />
            Rapikan
          </Button>
          {hasFilters && (
            <Button variant="ghost" size="sm" onClick={resetFilters}>
              Reset semua filter
            </Button>
          )}
          {onAskAgent && ids.length > 0 && !hasKpi && (
            <Button variant="outline" size="sm" onClick={() => onAskAgent(KPI_REQUEST)}>
              Buat KPI di atas dashboard
            </Button>
          )}
        </div>
      </div>
      {renderError && (
        <Alert variant="destructive">
          <AlertDescription>{renderError}</AlertDescription>
        </Alert>
      )}

      {groups.kpi.length > 0 && (
        <section aria-label="KPI" className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {groups.kpi.map((id) => (
            <div key={id} data-tile-id={id} className={cn(TILE, "min-h-28")}>
              {renderCell(id, groups.kpi)}
            </div>
          ))}
        </section>
      )}

      <div ref={containerRef} className="relative">
        {width > 0 && groups.chart.length > 0 && (
          <GridLayout
            width={width}
            gridConfig={{ cols: GRID_COLUMNS, rowHeight: ROW_HEIGHT, margin: GRID_MARGIN, containerPadding: GRID_PADDING }}
            dragConfig={{ enabled: true, handle: `.${DRAG_HANDLE}` }}
            layout={layout}
            onDragStart={onInteractionStart}
            onResizeStart={onInteractionStart}
            onDragStop={onInteractionStop}
            onResizeStop={onInteractionStop}
          >
            {groups.chart.map((id) => (
              <div key={id} data-tile-id={id} className={TILE}>
                {renderCell(id, groups.chart, true)}
              </div>
            ))}
          </GridLayout>
        )}
      </div>

      {groups.insight.length > 0 && renderInsight && (
        <section aria-labelledby="canvas-insight" className="flex flex-col gap-3">
          <h3 id="canvas-insight" className="section-label">Insight</h3>
          <div className="grid gap-4 md:grid-cols-2">
            {groups.insight.map((id) => (
              <div key={id} data-tile-id={id} className={TILE}>
                {renderCell(id, groups.insight)}
              </div>
            ))}
          </div>
        </section>
      )}

      {(hiddenCount > 0 || (expanded && ids.length > MAX_VISIBLE_VIEWS)) && (
        <Button variant="outline" size="sm" className="self-center" data-export-hide onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Ringkas dashboard" : `Tampilkan ${hiddenCount} item lainnya`}
        </Button>
      )}

      {ids.length === 0 && (
        <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-input px-4 py-14 text-center">
          <p className="font-medium">Dashboard masih kosong.</p>
          <p className="text-[0.8125rem] text-muted-foreground">
            Minta agent di panel chat untuk membuat chart, misalnya &ldquo;Buatkan dashboard ringkasan dari data ini.&rdquo;
          </p>
        </div>
      )}

      <AlertDialog open={removeId !== null} onOpenChange={(open) => !open && setRemoveId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hapus “{removeTitle}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Item hilang dari dashboard. Batalkan lewat Undo bila perlu.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Batal</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (removeId) onRemove(removeId);
                setRemoveId(null);
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

const Cell = memo(function Cell({
  id,
  snapshot,
  rendered,
  crossFilters,
  datasetVersions,
  renderInsight,
  draggable,
  canMoveUp,
  canMoveDown,
  onMove,
  onRetry,
  onRemove,
  onChartElementClick,
  onVerify,
  verified = false,
  highlighted = false,
  onHoverQuery,
}: {
  id: string;
  snapshot: DashboardSnapshot;
  rendered: RenderedItem | undefined;
  crossFilters: Predicate[];
  datasetVersions: Readonly<Record<string, number>>;
  renderInsight: boolean;
  /** Hanya tile di grid chart yang bisa diseret. */
  draggable: boolean;
  canMoveUp: boolean;
  canMoveDown: boolean;
  onMove: (itemId: string, dir: -1 | 1) => void;
  onRetry: () => void;
  onRemove: (itemId: string) => void;
  onChartElementClick: (
    itemId: string,
    params: { name?: unknown; value?: unknown },
  ) => void;
  onVerify?: (itemId: string) => void;
  verified?: boolean;
  highlighted?: boolean;
  onHoverQuery?: (queryId: string | null) => void;
}) {
  const item = snapshot.content.items[id];
  const status = snapshot.item_status[id];
  const badges: string[] = [];
  if (status?.invalid) badges.push("invalid");
  if (status?.stale) badges.push("stale");

  const title = item?.title ?? id;
  const isChartItem = item?.kind === "chart";
  const renderedOption = rendered?.option;
  const normalized = useMemo(
    () => (isChartItem && renderedOption ? normalizeChart(renderedOption) : null),
    [isChartItem, renderedOption],
  );
  const crossFilterActive =
    isChartItem && item.spec.cross_filter_column !== null && crossFilters.length > 0;

  const canVerify = onVerify && item && item.kind !== "insight";

  return (
    <div
      className={cn(
        "flex min-h-0 flex-1 flex-col transition-shadow",
        highlighted && "shadow-[inset_0_0_0_2px_var(--ring)]",
      )}
      onMouseEnter={item?.kind === "insight" ? () => onHoverQuery?.(item.query_id) : undefined}
      onMouseLeave={item?.kind === "insight" ? () => onHoverQuery?.(null) : undefined}
    >
      <div className="flex min-h-10 shrink-0 items-start justify-between gap-2 pt-2.5 pr-1.5 pb-1 pl-4">
        {(() => {
          const heading = (
            <>
              <span
                className={cn(
                  "line-clamp-2 leading-snug",
                  // KPI: judul sebagai label kecil di atas angka; chart: judul utama kartu.
                  item?.kind === "kpi" ? "text-xs font-medium text-muted-foreground" : "text-sm font-medium",
                )}
              >
                {title}
              </span>
              {normalized?.subtitle && (
                <span className="mt-0.5 block w-full truncate text-xs text-muted-foreground">{normalized.subtitle}</span>
              )}
            </>
          );
          const tooltip = normalized?.subtitle ? `${title}\n${normalized.subtitle}` : title;
          return draggable ? (
            <button
              type="button"
              data-slot="drag-handle"
              className={cn(
                DRAG_HANDLE,
                "relative flex min-w-0 flex-1 cursor-grab flex-col items-start justify-center text-left active:cursor-grabbing",
              )}
              aria-label={`Pindah item ${title}`}
              title={tooltip}
            >
              <DotsSixVerticalIcon
                aria-hidden
                weight="bold"
                className={cn("absolute top-0.5 -left-3.5 size-3.5 text-text-subtle", HOVER_REVEAL)}
                data-export-hide
              />
              {heading}
            </button>
          ) : (
            <div className="flex min-w-0 flex-1 flex-col items-start" title={tooltip}>
              {heading}
            </div>
          );
        })()}
        <span className="flex shrink-0 items-center gap-1">
          {crossFilterActive && <Badge variant="outline">Cross_Filter</Badge>}
          {badges.map((b) =>
            b === "invalid" ? (
              <Badge key={b} variant="destructive">Tidak valid</Badge>
            ) : (
              // ponytail: belum ada varian Badge "warn"; tambah varian bila dipakai di >1 tempat.
              <Badge key={b} variant="secondary" className="bg-warn-soft text-warn">Data berubah</Badge>
            ),
          )}
          {verified && (
            <Badge variant="secondary">
              <SealCheckIcon data-icon="inline-start" weight="fill" />
              Hasil benar
            </Badge>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                className={cn("-mt-1 text-muted-foreground", HOVER_REVEAL)}
                aria-label={`Aksi untuk ${title}`}
                data-export-hide
              >
                <DotsThreeIcon weight="bold" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              {canVerify && (
                <>
                  <DropdownMenuGroup>
                    <DropdownMenuItem disabled={verified} onSelect={() => onVerify(id)}>
                      <SealCheckIcon />
                      <span className="flex flex-col">
                        Tandai benar
                        <span className="text-xs text-muted-foreground">Jadi contoh untuk pertanyaan serupa</span>
                      </span>
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                </>
              )}
              {(canMoveUp || canMoveDown) && (
                <>
                  <DropdownMenuGroup>
                    <DropdownMenuItem disabled={!canMoveUp} onSelect={() => onMove(id, -1)}>
                      <ArrowUpIcon />
                      Pindahkan ke atas
                    </DropdownMenuItem>
                    <DropdownMenuItem disabled={!canMoveDown} onSelect={() => onMove(id, 1)}>
                      <ArrowDownIcon />
                      Pindahkan ke bawah
                    </DropdownMenuItem>
                  </DropdownMenuGroup>
                  <DropdownMenuSeparator />
                </>
              )}
              <DropdownMenuGroup>
                <DropdownMenuItem variant="destructive" onSelect={() => onRemove(id)}>
                  <TrashIcon />
                  Hapus
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </span>
      </div>

      {isChartItem ? (
        normalized?.error ? (
          <div className={PLACEHOLDER} role="alert">
            Chart tidak dapat ditampilkan: {normalized.error}
          </div>
        ) : normalized ? (
          <div className="min-h-0 flex-1 px-1.5 pb-1.5">
            <Chart id={id} option={normalized.option} onClick={onChartElementClick} />
          </div>
        ) : (
          rendered?.status && rendered.status !== "ok" ? (
            <div className={cn(PLACEHOLDER, "flex-col gap-2")} role="alert">
              Chart tidak dapat dirender ({rendered.status}).
              <Button variant="outline" size="sm" onClick={onRetry} data-export-hide>
                Coba lagi
              </Button>
            </div>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col gap-2 px-3.5 pb-3.5" aria-busy="true">
              <span className="sr-only">Menyiapkan chart</span>
              <Skeleton className="min-h-0 flex-1" />
            </div>
          )
        )
      ) : item?.kind === "kpi" ? (
        <KpiTile item={item} rendered={rendered} />
      ) : item?.kind === "insight" ? (
        renderInsight ? (
          <div className="min-h-0 flex-1 px-4 pb-4">
            <InsightCard
              insight={item}
              dashboardId={snapshot.id}
              baseVersion={snapshot.version}
              activeFilters={snapshot.content.global_filters}
              datasetVersions={datasetVersions}
            />
          </div>
        ) : null
      ) : (
        <div className={PLACEHOLDER}>Item tidak dikenal.</div>
      )}
    </div>
  );
});

/** Chart ECharts dengan `onEvents` stabil (tidak di-unbind/bind ulang tiap render). */
const Chart = memo(function Chart({
  id,
  option,
  onClick,
}: {
  id: string;
  option: RenderedItem["option"];
  onClick: (itemId: string, params: { name?: unknown; value?: unknown }) => void;
}) {
  const onEvents = useMemo(
    () => ({ click: (params: { name?: unknown; value?: unknown }) => onClick(id, params) }),
    [id, onClick],
  );
  const theme = useChartTheme();
  return (
    <ReactECharts
      theme={theme}
      opts={ECHARTS_OPTS}
      style={ECHARTS_STYLE}
      option={option ?? {}}
      notMerge
      lazyUpdate
      onEvents={onEvents}
    />
  );
});

