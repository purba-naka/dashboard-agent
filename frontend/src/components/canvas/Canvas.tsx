"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useContainerWidth, GridLayout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { InsightCard } from "@/components/insights";
import { KpiTile } from "./KpiTile";
import { toggleCrossFilter } from "@/lib/filters";
import type {
  DashboardSnapshot,
  Dataset,
  FilterSet,
  LayoutChange,
  LayoutRect,
  Predicate,
  RenderedItem,
} from "@/lib/types";
import { GRID_COLUMNS } from "@/lib/dashboard-state";
import {
  chartElementValue,
  defaultCanvasClient,
  errorMessage,
  resolveCrossFilterTable,
  sortedItemIds,
  type CanvasClient,
} from "./client";
import styles from "./canvas.module.css";

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
}

const ROW_HEIGHT = 48;
// Prop ECharts konstan: objek baru tiap render memicu resize/rebind di echarts-for-react.
const ECHARTS_OPTS = { renderer: "canvas" } as const;
const ECHARTS_STYLE = { height: "100%", minHeight: 0 } as const;

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
}: CanvasProps) {
  const { content, version } = snapshot;
  const { containerRef, width } = useContainerWidth();
  // Hasil /render disimpan sebagai state agar re-render saat selesai.
  const [rendered, setRendered] = useState<Record<string, RenderedItem>>({});
  const [renderError, setRenderError] = useState<string | null>(null);

  const ids = useMemo(() => sortedItemIds(content.layout), [content.layout]);

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
  }, [client, snapshot.id, snapshot.version, ids, crossFilters, renderKey]);

  function onLayoutChange(next: readonly GridEntry[]) {
    const changes: LayoutChange[] = [];
    for (const entry of next) {
      const before = content.layout[entry.i];
      if (!before) continue;
      const after: LayoutRect = { x: entry.x, y: entry.y, w: entry.w, h: entry.h };
      if (
        before.x !== after.x ||
        before.y !== after.y ||
        before.w !== after.w ||
        before.h !== after.h
      ) {
        changes.push({ id: entry.i, before, after });
      }
    }
    if (changes.length === 0) return;
    const after: Record<string, LayoutRect> = {};
    for (const change of changes) after[change.id] = change.after;
    client
      .command(snapshot.id, version, { type: "set_layout", changes: after })
      .catch(() => {
        // Gagal simpan tata letak → SSE `resync` akan memperbaiki state lokal.
      });
  }

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
  const handlersRef = useRef({ onRemove, onChartElementClick });
  useEffect(() => {
    handlersRef.current = { onRemove, onChartElementClick };
  });
  const stableRemove = useCallback((id: string) => handlersRef.current.onRemove(id), []);
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
      ids.map((id) => {
        const rect = content.layout[id];
        return { i: id, x: rect.x, y: rect.y, w: rect.w, h: rect.h };
      }),
    [ids, content.layout],
  );

  const renderItems = rendered;

  return (
    <div className={styles.canvas} data-canvas-version={version}>
      <div className={styles.toolbar}>
        <h2 className={styles.title}>{content.title}</h2>
        <span className={styles.muted}>v{version}</span>
        {hasFilters && (
          <button type="button" className={styles.itemAction} onClick={resetFilters}>
            Reset semua filter
          </button>
        )}
        {renderError && (
          <span role="alert" className={styles.error}>
            {renderError}
          </span>
        )}
      </div>

      <div ref={containerRef} className={styles.grid}>
        {width > 0 && (
          <GridLayout
            width={width}
            gridConfig={{ cols: GRID_COLUMNS, rowHeight: ROW_HEIGHT }}
            dragConfig={{ enabled: true, handle: `.${styles.dragHandle}` }}
            layout={layout}
            onLayoutChange={onLayoutChange}
          >
            {ids.map((id) => (
              <div key={id} className={styles.cell}>
                <Cell
                  id={id}
                  snapshot={snapshot}
                  rendered={renderItems[id]}
                  crossFilters={crossFilters}
                  datasetVersions={datasetVersions}
                  renderInsight={renderInsight}
                  onRemove={stableRemove}
                  onChartElementClick={stableChartClick}
                  onVerify={client.verify ? stableVerify : undefined}
                  verified={verifiedIds.has(id)}
                />
              </div>
            ))}
          </GridLayout>
        )}
      </div>

      {ids.length === 0 && (
        <p className={styles.empty}>
          Dashboard masih kosong. Minta agent di panel chat untuk membuat chart.
        </p>
      )}
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
  onRemove,
  onChartElementClick,
  onVerify,
  verified = false,
}: {
  id: string;
  snapshot: DashboardSnapshot;
  rendered: RenderedItem | undefined;
  crossFilters: Predicate[];
  datasetVersions: Readonly<Record<string, number>>;
  renderInsight: boolean;
  onRemove: (itemId: string) => void;
  onChartElementClick: (
    itemId: string,
    params: { name?: unknown; value?: unknown },
  ) => void;
  onVerify?: (itemId: string) => void;
  verified?: boolean;
}) {
  const item = snapshot.content.items[id];
  const status = snapshot.item_status[id];
  const badges: string[] = [];
  if (status?.invalid) badges.push("invalid");
  if (status?.stale) badges.push("stale");

  const title = item?.title ?? id;
  const isChartItem = item?.kind === "chart";
  const crossFilterActive =
    isChartItem && item.spec.cross_filter_column !== null && crossFilters.length > 0;

  return (
    <>
      <div className={styles.cellHeader}>
        <button
          type="button"
          className={styles.dragHandle}
          aria-label={`Pindah item ${title}`}
        >
          {title}
        </button>
        <span className={styles.badges}>
          {crossFilterActive && (
            <span className={`${styles.badge} ${styles.crossFilter}`}>
              Cross_Filter
            </span>
          )}
          {badges.map((b) => (
            <span
              key={b}
              className={`${styles.badge} ${
                b === "invalid" ? styles.badgeInvalid : styles.badgeStale
              }`}
            >
              {b === "invalid" ? "Tidak valid" : "Data berubah"}
            </span>
          ))}
          {onVerify && item && item.kind !== "insight" && (
            <button
              type="button"
              className={styles.itemAction}
              aria-label={`Tandai terverifikasi ${title}`}
              title="Jadikan contoh query terverifikasi untuk agent"
              disabled={verified}
              onClick={() => onVerify(id)}
            >
              {verified ? "Terverifikasi" : "Verifikasi"}
            </button>
          )}
          <button
            type="button"
            className={styles.remove}
            aria-label={`Hapus item ${title}`}
            onClick={() => onRemove(id)}
          >
            ✕
          </button>
        </span>
      </div>

      {isChartItem ? (
        rendered?.option ? (
          <Chart id={id} option={rendered.option} onClick={onChartElementClick} />
        ) : (
          <div className={styles.placeholder}>
            {rendered?.status && rendered.status !== "ok"
              ? `Chart tidak dapat dirender (${rendered.status}).`
              : "Menyiapkan chart…"}
          </div>
        )
      ) : item?.kind === "kpi" ? (
        <KpiTile item={item} rendered={rendered} />
      ) : item?.kind === "insight" ? (
        renderInsight ? (
          <div className={styles.insightSlot}>
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
        <div className={styles.placeholder}>Item tidak dikenal.</div>
      )}
    </>
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
  return (
    <ReactECharts
      opts={ECHARTS_OPTS}
      style={ECHARTS_STYLE}
      option={option ?? {}}
      notMerge
      lazyUpdate
      onEvents={onEvents}
    />
  );
});

