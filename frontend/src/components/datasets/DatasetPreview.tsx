"use client";

import { useEffect, useState } from "react";
import { isApiError } from "@/lib/api";
import { formatNumber } from "@/lib/format-number";
import type { Dataset, DatasetRowsPage } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { defaultDatasetClient, errorMessage, typeLabel, type DatasetClient } from "./client";

export const PREVIEW_PAGE_SIZE = 100;

export interface DatasetPreviewProps {
  workspaceId: string;
  dataset: Dataset;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client?: Partial<Pick<DatasetClient, "rows">>;
}

type Result =
  | { status: "ready"; page: DatasetRowsPage }
  | { status: "privacy" }
  | { status: "error"; message: string };

type State = { status: "loading" } | Result;

/** Pratinjau baris dataset: paginasi server, 100 baris per halaman. */
export function DatasetPreview({
  workspaceId,
  dataset,
  open,
  onOpenChange,
  client = defaultDatasetClient,
}: DatasetPreviewProps) {
  const [offset, setOffset] = useState(0);
  const [retry, setRetry] = useState(0);
  const key = `${dataset.id}:${offset}:${retry}`;
  const [done, setDone] = useState<{ key: string; result: Result } | null>(null);
  // Hasil milik permintaan lain (halaman/dataset berganti) dianggap masih memuat.
  const state: State = done && done.key === key ? done.result : { status: "loading" };

  useEffect(() => {
    if (!open) return;
    const ctrl = new AbortController();
    const rows = client.rows ?? defaultDatasetClient.rows;
    rows(workspaceId, dataset.id, { offset, limit: PREVIEW_PAGE_SIZE }, { signal: ctrl.signal })
      .then((page) => setDone({ key, result: { status: "ready", page } }))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (isApiError(err) && err.code === "PRIVACY_NO_SAMPLES") {
          setDone({ key, result: { status: "privacy" } });
        } else {
          setDone({ key, result: { status: "error", message: errorMessage(err) } });
        }
      });
    return () => ctrl.abort();
  }, [open, client, workspaceId, dataset.id, offset, key]);

  const page = state.status === "ready" ? state.page : null;
  const total = page?.total ?? dataset.row_count;
  const from = page && page.rows.length > 0 ? page.offset + 1 : 0;
  const to = page ? page.offset + page.rows.length : 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setOffset(0);
        onOpenChange(next);
      }}
    >
      <DialogContent className="flex max-h-[85vh] flex-col sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Pratinjau {dataset.source_name}</DialogTitle>
          <DialogDescription>
            {formatNumber(total)} baris · {dataset.schema.length} kolom
          </DialogDescription>
        </DialogHeader>

        {state.status === "loading" && (
          <div role="status" aria-label="Memuat baris" className="flex flex-col gap-2">
            {Array.from({ length: 6 }, (_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        )}

        {state.status === "privacy" && (
          <p role="status" className="text-sm text-muted-foreground">
            Pratinjau dinonaktifkan karena mode privasi.
          </p>
        )}

        {state.status === "error" && (
          <div role="alert" className="flex flex-col items-start gap-2 text-sm text-destructive">
            <p>{state.message}</p>
            <Button variant="outline" size="sm" onClick={() => setRetry((n) => n + 1)}>
              Coba lagi
            </Button>
          </div>
        )}

        {page && (
          <>
            <div className="min-h-0 flex-1 overflow-auto rounded-md border [&_[data-slot=table-container]]:overflow-visible">
              <Table>
                <TableHeader>
                  <TableRow>
                    {page.columns.map((c) => (
                      <TableHead key={c.name} className="sticky top-0 z-10 bg-muted">
                        <span className="block">{c.name}</span>
                        <span className="block text-xs font-normal text-muted-foreground">
                          {typeLabel(c.type)}
                        </span>
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {page.rows.map((row, i) => (
                    <TableRow key={page.offset + i}>
                      {row.map((cell, j) => (
                        <TableCell
                          key={j}
                          className={typeof cell === "number" ? "text-right tabular-nums" : undefined}
                        >
                          {cell === null ? (
                            <span className="text-muted-foreground">—</span>
                          ) : typeof cell === "number" ? (
                            formatNumber(cell)
                          ) : (
                            String(cell)
                          )}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground tabular-nums">
                {formatNumber(from)}–{formatNumber(to)} dari {formatNumber(page.total)}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PREVIEW_PAGE_SIZE))}
                >
                  Sebelumnya
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset + PREVIEW_PAGE_SIZE >= page.total}
                  onClick={() => setOffset(offset + PREVIEW_PAGE_SIZE)}
                >
                  Berikutnya
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
