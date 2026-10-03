"use client";

import { useState } from "react";
import type { Dataset, Relation } from "@/lib/types";
import {
  defaultRelationClient,
  errorMessage,
  formatCardinality,
  formatOverlap,
  groupByStatus,
  mergeDetected,
  relationTitle,
  upsertRelation,
  type RelationClient,
} from "./client";
import { ArrowsClockwiseIcon, TrashIcon, XIcon } from "@phosphor-icons/react/ssr";
import { IconAction } from "@/components/IconAction";
import { Button } from "@/components/ui/button";

export interface RelationsPanelProps {
  workspaceId: string;
  /** Relasi terbaru (dari state Workspace/SSE); sinkron ke tampilan lokal. */
  relations: readonly Relation[];
  /** Daftar Dataset untuk nama tabel tampilan. */
  datasets?: readonly Dataset[];
  /** Daftar relasi berubah (aksi konfirmasi/tolak/hapus/deteksi ulang). */
  onRelationsChange?: (relations: Relation[]) => void;
  client?: RelationClient;
}

/**
 * Panel relasi antar-dataset: daftar kandidat/terkonfirmasi/ditolak dengan
 * kardinalitas dan overlap, aksi Konfirmasi/Tolak/Hapus, serta deteksi ulang
 * (Req 7.2–7.5, 7.8).
 */
export function RelationsPanel({
  workspaceId,
  relations,
  datasets = [],
  onRelationsChange,
  client = defaultRelationClient,
}: RelationsPanelProps) {
  const [list, setList] = useState<Relation[]>(() => [...relations]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sinkronkan bila daftar dari parent (mis. event SSE) berubah.
  const [syncedRelations, setSyncedRelations] = useState(relations);
  if (relations !== syncedRelations) {
    setSyncedRelations(relations);
    setList([...relations]);
  }

  const tableNames = Object.fromEntries(datasets.map((d) => [d.id, d.table_name]));
  const groups = groupByStatus(list);

  function apply(next: Relation[]) {
    setList(next);
    onRelationsChange?.(next);
  }

  async function run(relation: Relation, action: "confirm" | "reject" | "remove") {
    setBusyId(relation.id);
    setError(null);
    try {
      if (action === "confirm") {
        apply(upsertRelation(list, await client.confirm(workspaceId, relation.id)));
      } else if (action === "reject") {
        apply(upsertRelation(list, await client.reject(workspaceId, relation.id)));
      } else {
        await client.remove(workspaceId, relation.id);
        apply(upsertRelation(list, { ...relation, status: "deleted" }));
      }
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function detect() {
    setDetecting(true);
    setError(null);
    try {
      const candidates = await client.detect(workspaceId);
      apply(mergeDetected(list, candidates));
    } catch (err: unknown) {
      setError(errorMessage(err));
    } finally {
      setDetecting(false);
    }
  }

  function renderGroup(title: string, items: readonly Relation[]) {
    if (items.length === 0) return null;
    return (
      <section aria-label={`Relasi ${title}`} className="flex flex-col gap-1.5">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        <ul className="flex flex-col gap-1.5">
          {items.map((relation) => {
            const name = relationTitle(relation, tableNames);
            const busy = busyId === relation.id;
            return (
              <li key={relation.id} className="flex items-start gap-2 rounded-md border px-2.5 py-2">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="text-sm font-medium break-words">{name}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {formatCardinality(relation.cardinality)} · overlap {formatOverlap(relation.overlap_pct)}
                  </span>
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  {relation.status === "candidate" ? (
                    <>
                      <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void run(relation, "confirm")}>
                        Konfirmasi
                      </Button>
                      <IconAction label="Tolak" disabled={busy} onClick={() => void run(relation, "reject")}>
                        <XIcon />
                      </IconAction>
                    </>
                  ) : (
                    <IconAction
                      label={`Hapus relasi ${name}`}
                      tip="Hapus"
                      disabled={busy}
                      onClick={() => void run(relation, "remove")}
                    >
                      <TrashIcon />
                    </IconAction>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Relasi</h2>
        <Button type="button" variant="outline" size="sm" disabled={detecting} onClick={() => void detect()}>
          <ArrowsClockwiseIcon data-icon="inline-start" />
          {detecting ? "Mendeteksi" : "Deteksi ulang"}
        </Button>
      </div>

      {list.length === 0 && (
        <p className="text-sm text-muted-foreground">
          Belum ada relasi. Coba deteksi ulang setelah mengunggah beberapa dataset.
        </p>
      )}

      {renderGroup("Kandidat", groups.candidate)}
      {renderGroup("Terkonfirmasi", groups.confirmed)}
      {renderGroup("Ditolak", groups.rejected)}

      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
