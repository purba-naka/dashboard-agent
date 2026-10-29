"use client";

import { formatNumber } from "@/lib/format-number";
import { useLayoutEffect, useRef, useState } from "react";
import { formatScalar } from "@/lib/filters";
import type { FilterSet, InsightItem, QueryDetail } from "@/lib/types";
import { ArrowClockwiseIcon } from "@phosphor-icons/react/ssr";
import { IconAction } from "@/components/IconAction";
import { cn } from "@/lib/utils";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  computedWithDifferentFilters,
  defaultInsightClient,
  errorMessage,
  insightTypeLabel,
  isStale,
  type InsightClient,
} from "./client";

export interface InsightCardProps {
  insight: InsightItem;
  dashboardId: string;
  baseVersion: number;
  /** Global_Filter aktif untuk badge "filter berbeda" (Req 15.3). */
  activeFilters: FilterSet;
  /** `dataset_id -> data_version` terbaru untuk badge stale (Req 26.2). */
  datasetVersions?: Readonly<Record<string, number>>;
  client?: InsightClient;
}

/**
 * Insight_Card: teks + tipe, badge "dihitung dengan filter berbeda" dan badge
 * stale, modal detail (SQL sumber + tabel bukti), tombol refresh (Req 14.7,
 * 15.1, 15.3, 26.2).
 */
export function InsightCard({
  insight,
  dashboardId,
  baseVersion,
  activeFilters,
  datasetVersions = {},
  client = defaultInsightClient,
}: InsightCardProps) {
  const [detail, setDetail] = useState<QueryDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [busy, setBusy] = useState<"detail" | "refresh" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const textRef = useRef<HTMLParagraphElement>(null);
  const [clamped, setClamped] = useState(false);
  useLayoutEffect(() => {
    const el = textRef.current;
    if (el) setClamped(el.scrollHeight > el.clientHeight + 1);
  }, [insight.text]);

  const differentFilters = computedWithDifferentFilters(insight, activeFilters);
  const stale = isStale(insight, datasetVersions);

  async function openDetail() {
    setError(null);
    setDetailOpen(true);
    if (detail) return;
    setBusy("detail");
    try {
      setDetail(await client.query(insight.query_id));
    } catch (err) {
      setError(errorMessage(err));
      setDetailOpen(false);
    } finally {
      setBusy(null);
    }
  }

  async function onRefresh() {
    setError(null);
    setBusy("refresh");
    try {
      await client.refresh(dashboardId, insight.id, baseVersion);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <article className="flex h-full min-h-0 flex-col gap-2 overflow-hidden" aria-label={`Insight: ${insight.title}`}>
      {/* Judul tampil di header sel Canvas; kartu hanya badge tipe. */}
      <Badge variant="secondary">{insightTypeLabel(insight.insight_type)}</Badge>

      {/* Section Insight: tinggi ikut isi, jadi 3 baris. */}
      <p ref={textRef} className="m-0 line-clamp-3 shrink-0 text-sm leading-5">
        {insight.text}
      </p>
      {clamped && (
        <Button variant="link" size="sm" className="h-auto self-start p-0" onClick={openDetail}>
          Lihat selengkapnya
        </Button>
      )}

      {(differentFilters || stale) && (
        <div className="flex flex-wrap gap-1.5">
          {differentFilters && (
            <Badge variant="secondary" className="bg-warn-soft text-warn">
              Dihitung dengan filter berbeda
            </Badge>
          )}
          {stale && <Badge variant="destructive">Data sumber sudah berubah</Badge>}
        </div>
      )}

      {/* Aksi muncul saat hover/fokus kartu (`group/tile` di Canvas); selalu tampil di layar sentuh. */}
      <div
        className="mt-auto flex items-center gap-1 transition-opacity pointer-fine:opacity-0 pointer-fine:group-hover/tile:opacity-100 group-focus-within/tile:opacity-100"
        data-export-hide
      >
        <Button variant="ghost" size="sm" onClick={openDetail} disabled={busy !== null}>
          Detail
        </Button>
        <IconAction label={busy === "refresh" ? "Menyegarkan" : "Segarkan"} onClick={onRefresh} disabled={busy !== null}>
          <ArrowClockwiseIcon className={cn(busy === "refresh" && "animate-spin")} />
        </IconAction>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Dialog Radix dirender via portal: lolos dari transform + overflow sel grid. */}
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="max-h-[85dvh] overflow-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{insight.title}</DialogTitle>
            <DialogDescription className="whitespace-pre-wrap text-foreground">{insight.text}</DialogDescription>
          </DialogHeader>

          <section className="flex flex-col gap-2">
            <h4 className="text-sm font-semibold">SQL sumber</h4>
            {busy === "detail" && !detail ? (
              <Skeleton className="h-20 w-full" aria-busy="true" aria-label="Memuat SQL" />
            ) : (
              <pre className="overflow-x-auto rounded-md bg-muted p-3 font-mono text-[0.8125rem] whitespace-pre-wrap">
                {detail?.sql ?? insight.sql}
              </pre>
            )}
          </section>

          <section className="flex flex-col gap-2">
            <h4 className="text-sm font-semibold">Tabel bukti</h4>
            <EvidenceTableView
              columns={(detail?.columns ?? insight.evidence.columns).map((c) => c.name)}
              rows={detail?.rows ?? insight.evidence.rows}
              rowCount={detail?.row_count ?? insight.evidence.row_count}
            />
          </section>
        </DialogContent>
      </Dialog>
    </article>
  );
}

function EvidenceTableView({
  columns,
  rows,
  rowCount,
}: {
  columns: string[];
  rows: readonly (readonly (string | number | boolean | null)[])[];
  rowCount: number;
}) {
  if (columns.length === 0) {
    return <p className="text-[0.8125rem] text-muted-foreground">Tidak ada kolom bukti.</p>;
  }
  return (
    <>
      <Table className="tabular-nums">
        <TableHeader>
          <TableRow>
            {columns.map((c) => (
              <TableHead key={c}>{c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, i) => (
            <TableRow key={i}>
              {row.map((cell, j) => (
                <TableCell key={j}>{formatScalar(cell)}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {rowCount > rows.length && (
        <p className="text-[0.8125rem] text-muted-foreground">
          Menampilkan {rows.length} dari {formatNumber(rowCount)} baris.
        </p>
      )}
    </>
  );
}
