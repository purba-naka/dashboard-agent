"use client";

/**
 * Library React OpenUI untuk canvas: renderer di atas skema `schema.ts` (D4).
 *
 * Skema (zod) dipakai ulang apa adanya sehingga parser browser identik dengan
 * sidecar backend; file ini hanya memasang komponen React.
 */
import { memo, useMemo, useRef, useState, type ReactNode } from "react";
import ReactECharts from "echarts-for-react";
import {
  ArrowDownIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  ArrowsHorizontalIcon,
  ChartBarIcon,
  DotsSixVerticalIcon,
  DotsThreeIcon,
  SealCheckIcon,
  TrashIcon,
} from "@phosphor-icons/react/ssr";
import { createLibrary, defineComponent, type ComponentRenderProps } from "@openuidev/react-lang";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { InsightCard } from "@/components/insights";
import { KpiTile } from "@/components/canvas/KpiTile";
import { chartElementValue, resolveCrossFilterTable } from "@/components/canvas/client";
import { normalizeChart } from "@/lib/chart-normalize";
import { useChartTheme } from "@/lib/echarts-theme";
import { dateRange, formatScalar, inValues } from "@/lib/filters";
import type { CanvasQueryResult, KpiItem, RenderedItem, Scalar } from "@/lib/types";
import * as S from "./schema";
import { buildChartOption, type Row } from "./chart-options";
import { useCanvasHost } from "./host";
import {
  CHART_SWAPS,
  changeChartTypePatch,
  childNames,
  movePatch,
  parentOf,
  queryArgs,
  relocatePatch,
  sourceQuery,
  weightsPatch,
  widthPatch,
} from "./program";

/* eslint-disable @typescript-eslint/no-explicit-any -- props hasil parser bersifat dinamis */
type P = ComponentRenderProps<any>;

const CHART_HEIGHT = 320;
const ECHARTS_OPTS = { renderer: "canvas" } as const;
const PLACEHOLDER = "flex flex-1 items-center justify-center p-4 text-center text-[0.8125rem] text-muted-foreground";

/** Hasil Query sudah tiba (bukan default `{rows: []}` saat loading). */
function loaded(data: unknown): data is CanvasQueryResult {
  return typeof data === "object" && data !== null && typeof (data as CanvasQueryResult).status === "string";
}

function dataError(data: unknown): string | null {
  if (!loaded(data)) return null;
  if (data.status === "invalid") return "Tile tidak valid: relasi yang dipakai query tidak lagi dikonfirmasi.";
  if (data.status === "error") return `Data tidak dapat dimuat${data.error ? `: ${data.error.message}` : "."}`;
  return null;
}

// ---------------------------------------------------------------------------
// Tile: kartu + header + menu aksi
// ---------------------------------------------------------------------------

function TileMenu({ name, component }: { name: string; component: string }) {
  const host = useCanvasHost();
  const parent = parentOf(host.statements, name);
  const siblings = parent ? childNames(parent) : [];
  const idx = siblings.indexOf(name);
  const horizontal = parent?.component === "HContainer";
  const stmt = host.statements[name];
  const send = (patch: string | null) => patch && host.applyPatch(patch);
  const swaps = CHART_SWAPS[component] ?? [];
  const canVerify = host.verify && component !== "Insight" && component !== "Slicer";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" className="-mt-1 text-muted-foreground" aria-label="Aksi tile" data-export-hide>
          <DotsThreeIcon weight="bold" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {parent && siblings.length > 1 && (
          <DropdownMenuGroup>
            <DropdownMenuItem disabled={idx <= 0} onSelect={() => send(movePatch(host.statements, name, -1))}>
              {horizontal ? <ArrowLeftIcon /> : <ArrowUpIcon />}
              {horizontal ? "Pindah ke kiri" : "Pindah ke atas"}
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={idx >= siblings.length - 1}
              onSelect={() => send(movePatch(host.statements, name, 1))}
            >
              {horizontal ? <ArrowRightIcon /> : <ArrowDownIcon />}
              {horizontal ? "Pindah ke kanan" : "Pindah ke bawah"}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        )}
        {horizontal && (
          <DropdownMenuGroup>
            {(
              [
                [4, "Lebar ⅓"],
                [6, "Lebar ½"],
                [8, "Lebar ⅔"],
              ] as const
            ).map(([span, label]) => (
              <DropdownMenuItem key={span} onSelect={() => send(widthPatch(host.statements, name, span))}>
                <ArrowsHorizontalIcon />
                {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        )}
        {stmt && swaps.length > 0 && (
          <DropdownMenuGroup>
            {swaps.map((to) => (
              <DropdownMenuItem key={to} onSelect={() => send(changeChartTypePatch(stmt, to))}>
                <ChartBarIcon />
                Ubah ke {to.replace("Chart", "")}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        )}
        {canVerify && (
          <DropdownMenuItem disabled={host.verified.has(name)} onSelect={() => host.verify?.(name)}>
            <SealCheckIcon />
            Tandai benar
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" disabled={!parent} onSelect={() => host.requestRemove(name)}>
          <TrashIcon />
          Hapus
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Tile({
  name,
  component,
  title,
  subtitle,
  children,
  compact = false,
}: {
  name: string | undefined;
  component: string;
  title: string;
  subtitle?: string;
  children: ReactNode;
  compact?: boolean;
}) {
  const host = useCanvasHost();
  const status = name ? host.itemStatus[name] : undefined;
  const draggable = Boolean(name && host.editable && parentOf(host.statements, name));
  const [over, setOver] = useState(false);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const moving = host.dragging;
    host.setDragging(null);
    if (!moving || !name || moving === name) return;
    const parent = parentOf(host.statements, name);
    if (!parent) return;
    host.applyPatch(relocatePatch(host.statements, moving, parent.name, childNames(parent).indexOf(name)) ?? "");
  };

  return (
    <div
      className={cn(
        "relative flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card shadow-(--shadow-sm) transition-colors hover:border-border-strong",
        over && "ring-2 ring-ring",
      )}
      data-tile={name}
      data-component={component}
      onDragOver={(e) => {
        if (host.dragging && host.dragging !== name) {
          e.preventDefault();
          setOver(true);
        }
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <div className="flex min-h-11 shrink-0 items-start justify-between gap-2 pt-2.5 pr-1.5 pb-1 pl-2">
        <div className="flex min-w-0 flex-1 items-start gap-1">
          {draggable && (
            <span
              draggable
              onDragStart={(e) => {
                e.dataTransfer.effectAllowed = "move";
                host.setDragging(name ?? null);
              }}
              onDragEnd={() => host.setDragging(null)}
              className="mt-0.5 cursor-grab text-muted-foreground active:cursor-grabbing"
              aria-label={`Seret ${title}`}
              role="button"
              tabIndex={-1}
              data-export-hide
            >
              <DotsSixVerticalIcon />
            </span>
          )}
          <div className="flex min-w-0 flex-col">
            <span
              className={cn(
                "line-clamp-2 leading-snug",
                compact ? "text-[0.8125rem] font-medium text-muted-foreground" : "text-sm font-semibold",
              )}
              title={title}
            >
              {title}
            </span>
            {subtitle && <span className="truncate text-xs text-muted-foreground">{subtitle}</span>}
          </div>
        </div>
        <span className="flex shrink-0 items-center gap-1">
          {status?.invalid && <Badge variant="destructive">Tidak valid</Badge>}
          {status?.stale && (
            <Badge variant="secondary" className="bg-warn-soft text-warn">
              Data berubah
            </Badge>
          )}
          {name && host.verified.has(name) && (
            <Badge variant="secondary">
              <SealCheckIcon data-icon="inline-start" weight="fill" />
              Hasil benar
            </Badge>
          )}
          {name && host.editable && <TileMenu name={name} component={component} />}
        </span>
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chart
// ---------------------------------------------------------------------------

const EChart = memo(function EChart({
  option,
  onClick,
}: {
  option: Record<string, unknown>;
  onClick?: (params: { name?: unknown; value?: unknown }) => void;
}) {
  const theme = useChartTheme();
  const onEvents = useMemo(() => (onClick ? { click: onClick } : undefined), [onClick]);
  return (
    <ReactECharts
      theme={theme}
      opts={ECHARTS_OPTS}
      style={{ height: "100%", minHeight: 0 }}
      option={option}
      notMerge
      lazyUpdate
      onEvents={onEvents}
    />
  );
});

function makeChart(component: string) {
  return function ChartTile({ props, statementId }: P) {
    const host = useCanvasHost();
    const data = props.data;
    const error = dataError(data);
    const ready = loaded(data) && !error;
    const crossFilter: string | undefined = props.options?.crossFilter;
    const normalized = useMemo(() => {
      if (!ready) return null;
      const option = buildChartOption({
        component,
        props,
        columns: data.columns ?? [],
        rows: (data.rows ?? []) as Row[],
      });
      return normalizeChart(option);
    }, [ready, data, props]);
    const onClick = useMemo(
      () =>
        crossFilter
          ? (params: { name?: unknown; value?: unknown }) => {
              const value = chartElementValue(params);
              if (value !== null) host.toggleCrossFilter(crossFilter, value);
            }
          : undefined,
      [crossFilter, host],
    );
    const active = crossFilter && host.crossFilters.some((p) => p.column === crossFilter);
    return (
      <Tile name={statementId} component={component} title={props.title} subtitle={normalized?.subtitle}>
        {active && (
          <Badge variant="outline" className="absolute top-2.5 right-10">
            Cross_Filter
          </Badge>
        )}
        <div className="min-h-0 px-1.5 pb-1.5" style={{ height: CHART_HEIGHT }}>
          {error ? (
            <div className={PLACEHOLDER} role="alert">
              {error}
            </div>
          ) : normalized?.error ? (
            <div className={PLACEHOLDER} role="alert">
              Chart tidak dapat ditampilkan: {normalized.error}
            </div>
          ) : normalized ? (
            <EChart option={normalized.option} onClick={onClick} />
          ) : (
            <div className="flex h-full flex-col p-2" aria-busy="true">
              <span className="sr-only">Menyiapkan chart</span>
              <Skeleton className="min-h-0 flex-1" />
            </div>
          )}
        </div>
        {loaded(data) && data.filter_unaffected && (
          <p className="px-3.5 pb-2 text-xs text-muted-foreground">Filter aktif tidak berlaku untuk tile ini.</p>
        )}
      </Tile>
    );
  };
}

// ---------------------------------------------------------------------------
// KPI, tabel, matrix, slicer, insight
// ---------------------------------------------------------------------------

function KpiView({ props, statementId }: P) {
  const host = useCanvasHost();
  const stmt = statementId ? host.statements[statementId] : undefined;
  const args = queryArgs(sourceQuery(host.statements, stmt));
  const data = props.data;
  const item: KpiItem = {
    id: statementId ?? "kpi",
    kind: "kpi",
    title: props.title,
    spec: {
      spec_version: 1,
      query_id: String(args.query ?? ""),
      value_column: String(args.value ?? ""),
      comparison_column: (args.compare as string) ?? null,
      comparison_label: (args.compare_label as string) ?? null,
      format: { style: "number", currency: null, decimals: 0, compact: true },
      good_direction: (args.good as KpiItem["spec"]["good_direction"]) ?? "up",
      metric_name: null,
    },
  };
  const rendered: RenderedItem | undefined = loaded(data)
    ? { kpi: data.kpi, status: data.status, filter_unaffected: data.filter_unaffected, error: data.error }
    : undefined;
  return (
    <Tile name={statementId} component="Kpi" title={props.title} compact>
      <div className="flex min-h-[4.5rem] flex-col">
        <KpiTile item={item} rendered={rendered} />
      </div>
    </Tile>
  );
}

function useSorted(rows: Row[]) {
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    return [...rows].sort((a, b) => {
      const x = a[sort.col];
      const y = b[sort.col];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x < y ? -1 : 1) * sort.dir;
    });
  }, [rows, sort]);
  const toggle = (col: string) => setSort((s) => (s?.col === col ? { col, dir: s.dir === 1 ? -1 : 1 } : { col, dir: 1 }));
  return { sorted, sort, toggle };
}

const MAX_TABLE_ROWS = 200;

function TableView({ props, statementId }: P) {
  const data = props.data;
  const error = dataError(data);
  const rows: Row[] = loaded(data) ? ((data.rows ?? []) as Row[]) : [];
  const columns: string[] = Array.isArray(props.columns) && props.columns.length ? props.columns : loaded(data) ? (data.columns ?? []) : [];
  const { sorted, sort, toggle } = useSorted(rows);
  return (
    <Tile name={statementId} component="Table" title={props.title}>
      <div className="max-h-[360px] min-h-24 overflow-auto px-2 pb-2">
        {error ? (
          <div className={PLACEHOLDER} role="alert">{error}</div>
        ) : !loaded(data) ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <table className="w-full text-[0.8125rem] tabular-nums">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b">
                {columns.map((c) => (
                  <th key={c} scope="col" className="px-2 py-1.5 text-left font-medium text-muted-foreground"
                    aria-sort={sort?.col === c ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
                    <button type="button" className="hover:text-foreground" onClick={() => toggle(c)}>
                      {c}
                      {sort?.col === c ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, MAX_TABLE_ROWS).map((r, i) => (
                <tr key={i} className="border-b last:border-0">
                  {columns.map((c) => (
                    <td key={c} className={cn("px-2 py-1", typeof r[c] === "number" && "text-right")}>
                      {r[c] === undefined ? "" : formatScalar(r[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows.length > MAX_TABLE_ROWS && (
          <p className="pt-1 text-xs text-muted-foreground">
            Menampilkan {MAX_TABLE_ROWS} dari {rows.length} baris.
          </p>
        )}
      </div>
    </Tile>
  );
}

function MatrixView({ props, statementId }: P) {
  const data = props.data;
  const error = dataError(data);
  const rows: Row[] = useMemo(() => (loaded(data) ? ((data.rows ?? []) as Row[]) : []), [data]);
  const { rows: rowKey, columns: colKey, value } = props as { rows: string; columns: string; value: string };
  const pivot = useMemo(() => {
    const rk = [...new Set(rows.map((r) => r[rowKey]))];
    const ck = [...new Set(rows.map((r) => r[colKey]))];
    const cells = new Map<string, Scalar>();
    for (const r of rows) cells.set(JSON.stringify([r[rowKey], r[colKey]]), r[value]);
    return { rk, ck, cells };
  }, [rows, rowKey, colKey, value]);
  return (
    <Tile name={statementId} component="Matrix" title={props.title}>
      <div className="max-h-[360px] min-h-24 overflow-auto px-2 pb-2">
        {error ? (
          <div className={PLACEHOLDER} role="alert">{error}</div>
        ) : !loaded(data) ? (
          <Skeleton className="h-24 w-full" />
        ) : (
          <table className="w-full text-[0.8125rem] tabular-nums">
            <thead className="sticky top-0 bg-card">
              <tr className="border-b">
                <th scope="col" className="px-2 py-1.5 text-left font-medium text-muted-foreground">{rowKey}</th>
                {pivot.ck.map((c) => (
                  <th key={String(c)} scope="col" className="px-2 py-1.5 text-right font-medium text-muted-foreground">
                    {formatScalar(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pivot.rk.map((r) => (
                <tr key={String(r)} className="border-b last:border-0">
                  <th scope="row" className="px-2 py-1 text-left font-medium">{formatScalar(r)}</th>
                  {pivot.ck.map((c) => {
                    const v = pivot.cells.get(JSON.stringify([r, c]));
                    return (
                      <td key={String(c)} className="px-2 py-1 text-right">
                        {v === undefined ? "" : formatScalar(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Tile>
  );
}

function SlicerView({ props, statementId }: P) {
  const host = useCanvasHost();
  const column: string = props.column;
  const table = resolveCrossFilterTable(host.datasets, column);
  const current = host.globalFilters.find((p) => p.column === column && (!table || p.table === table));
  const data = props.data;
  const values = useMemo(() => {
    if (!loaded(data)) return [] as Scalar[];
    return [...new Set(((data.rows ?? []) as Row[]).map((r) => r[column]))].filter((v) => v !== undefined) as Scalar[];
  }, [data, column]);
  const selected = new Set(current?.kind === "in" ? current.values.map(String) : []);

  const setValues = (next: Scalar[]) =>
    table && host.setColumnFilter(column, next.length ? inValues(table, column, next) : null);

  return (
    <Tile name={statementId} component="Slicer" title={props.title} compact>
      <div className="flex flex-col gap-2 px-3 pb-3 text-[0.8125rem]">
        {!table ? (
          <p className="text-muted-foreground">Kolom &ldquo;{column}&rdquo; tidak ditemukan di dataset.</p>
        ) : props.mode === "date_range" ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="date"
              aria-label={`${props.title} mulai`}
              className="rounded-md border bg-background px-2 py-1"
              value={current?.kind === "date_range" ? (current.start ?? "") : ""}
              onChange={(e) =>
                host.setColumnFilter(
                  column,
                  dateRange(table, column, e.target.value || null, current?.kind === "date_range" ? current.end : null),
                )
              }
            />
            <span aria-hidden>–</span>
            <input
              type="date"
              aria-label={`${props.title} selesai`}
              className="rounded-md border bg-background px-2 py-1"
              value={current?.kind === "date_range" ? (current.end ?? "") : ""}
              onChange={(e) =>
                host.setColumnFilter(
                  column,
                  dateRange(table, column, current?.kind === "date_range" ? current.start : null, e.target.value || null),
                )
              }
            />
          </div>
        ) : !loaded(data) ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <div className="flex max-h-40 flex-col gap-1 overflow-auto" role="group" aria-label={props.title}>
            {values.map((v) => (
              <label key={String(v)} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={selected.has(String(v))}
                  onChange={(e) =>
                    setValues(
                      e.target.checked
                        ? [...values.filter((x) => selected.has(String(x))), v]
                        : values.filter((x) => selected.has(String(x)) && String(x) !== String(v)),
                    )
                  }
                />
                {formatScalar(v)}
              </label>
            ))}
          </div>
        )}
        {current && (
          <Button variant="ghost" size="sm" className="self-start" onClick={() => host.setColumnFilter(column, null)} data-export-hide>
            Hapus filter
          </Button>
        )}
      </div>
    </Tile>
  );
}

function InsightView({ props, statementId }: P) {
  const host = useCanvasHost();
  const insight = statementId ? host.statements[statementId]?.insight : null;
  return (
    <Tile name={statementId} component="Insight" title={props.title}>
      <div className="min-h-0 overflow-hidden px-2.5 py-2">
        {insight ? (
          <InsightCard
            insight={insight}
            dashboardId={host.dashboardId}
            baseVersion={host.version}
            activeFilters={host.globalFilters}
            datasetVersions={host.datasetVersions}
          />
        ) : (
          <p className="text-[0.8125rem]">{props.text}</p>
        )}
      </div>
    </Tile>
  );
}

function UnsupportedView({ props, statementId }: P) {
  return (
    <Tile name={statementId} component="Unsupported" title={props.title}>
      <div className={PLACEHOLDER}>{props.reason}</div>
    </Tile>
  );
}

// ---------------------------------------------------------------------------
// Layout (D8)
// ---------------------------------------------------------------------------

function Children({ items, renderNode }: { items: unknown[]; renderNode: (v: unknown) => ReactNode }) {
  return <>{items.map((c, i) => <div key={i} className="min-w-0">{renderNode(c)}</div>)}</>;
}

function VContainerView({ props, renderNode }: P) {
  return (
    <div className="flex min-w-0 flex-col gap-4" data-container="v">
      <Children items={props.children ?? []} renderNode={renderNode} />
    </div>
  );
}

function HContainerView({ props, renderNode, statementId }: P) {
  const host = useCanvasHost();
  const children: unknown[] = props.children ?? [];
  const given: number[] | undefined = props.weights;
  const weights =
    given && given.length === children.length && given.every((w) => w > 0) ? given : children.map(() => 1);
  const [preview, setPreview] = useState<number[] | null>(null);
  const shown = preview ?? weights;
  const ref = useRef<HTMLDivElement>(null);
  const stmt = statementId ? host.statements[statementId] : undefined;
  const resizable = Boolean(stmt && host.editable && children.length > 1);

  const startResize = (i: number) => (e: React.PointerEvent) => {
    const el = ref.current;
    if (!el || !stmt) return;
    e.preventDefault();
    const total = weights.reduce((a, b) => a + b, 0);
    const width = el.getBoundingClientRect().width;
    const startX = e.clientX;
    let next = weights;
    const move = (ev: PointerEvent) => {
      const delta = ((ev.clientX - startX) / width) * total;
      const pair = weights[i] + weights[i + 1];
      const left = Math.min(pair - total * 0.1, Math.max(total * 0.1, weights[i] + delta));
      next = weights.map((w, j) => (j === i ? left : j === i + 1 ? pair - left : w));
      setPreview(next);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setPreview(null);
      // Normalisasi ke skala 12 agar angka di program tetap ringkas.
      const sum = next.reduce((a, b) => a + b, 0);
      const patch = weightsPatch(stmt, next.map((w) => (w / sum) * 12));
      if (patch && next !== weights) host.applyPatch(patch);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div
      ref={ref}
      className="grid min-w-0 gap-4 max-lg:!grid-cols-1"
      style={{ gridTemplateColumns: shown.map((w) => `minmax(0, ${w}fr)`).join(" ") }}
      data-container="h"
    >
      {children.map((c, i) => (
        <div key={i} className="relative min-w-0">
          {renderNode(c)}
          {resizable && i < children.length - 1 && (
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Ubah lebar tile"
              tabIndex={0}
              onPointerDown={startResize(i)}
              onKeyDown={(e) => {
                if (!stmt || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
                const step = e.key === "ArrowLeft" ? -1 : 1;
                const total = weights.reduce((a, b) => a + b, 0);
                const unit = total / 12;
                const left = weights[i] + step * unit;
                const right = weights[i + 1] - step * unit;
                if (left <= 0 || right <= 0) return;
                const next = weights.map((w, j) => (j === i ? left : j === i + 1 ? right : w));
                host.applyPatch(weightsPatch(stmt, next.map((w) => (w / total) * 12)) ?? "");
              }}
              className="absolute top-0 -right-3 z-10 h-full w-2 cursor-col-resize rounded hover:bg-ring/40 focus-visible:bg-ring/60 max-lg:hidden"
              data-export-hide
            />
          )}
        </div>
      ))}
    </div>
  );
}

function SectionView({ props, renderNode }: P) {
  return (
    <section className="flex min-w-0 flex-col gap-3" aria-label={props.title}>
      <header className="flex flex-col gap-0.5 border-b pb-1.5">
        <h3 className="m-0 text-base font-semibold">{props.title}</h3>
        {props.subtitle && <p className="m-0 text-[0.8125rem] text-muted-foreground">{props.subtitle}</p>}
      </header>
      <div className="flex min-w-0 flex-col gap-4">
        <Children items={props.children ?? []} renderNode={renderNode} />
      </div>
    </section>
  );
}

function TabsView({ props, renderNode }: P) {
  const labels: string[] = props.labels ?? [];
  const children: unknown[] = props.children ?? [];
  const [active, setActive] = useState(0);
  const idx = Math.min(active, Math.max(0, children.length - 1));
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div role="tablist" className="flex flex-wrap gap-1 border-b" data-export-hide={false}>
        {children.map((_, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === idx}
            className={cn(
              "-mb-px border-b-2 px-3 py-1.5 text-[0.8125rem] font-medium",
              i === idx ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActive(i)}
          >
            {labels[i] ?? `Tab ${i + 1}`}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="min-w-0">
        {children[idx] !== undefined && renderNode(children[idx])}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Library
// ---------------------------------------------------------------------------

const RENDERERS: Record<string, (p: P) => ReactNode> = {
  VContainer: VContainerView,
  HContainer: HContainerView,
  Section: SectionView,
  Tabs: TabsView,
  LineChart: makeChart("LineChart"),
  BarChart: makeChart("BarChart"),
  PieChart: makeChart("PieChart"),
  ScatterChart: makeChart("ScatterChart"),
  Heatmap: makeChart("Heatmap"),
  Waterfall: makeChart("Waterfall"),
  Kpi: KpiView,
  Table: TableView,
  Matrix: MatrixView,
  Slicer: SlicerView,
  Insight: InsightView,
  Unsupported: UnsupportedView,
};

export const canvasLibrary = createLibrary({
  components: S.COMPONENTS.map((c) =>
    defineComponent({
      name: c.name,
      description: c.description,
      props: c.props,
      component: RENDERERS[c.name] as any,
    }),
  ),
  componentGroups: S.COMPONENT_GROUPS,
  root: S.ROOT_COMPONENT,
});
/* eslint-enable @typescript-eslint/no-explicit-any */
