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
import styles from "./relations.module.css";

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
      <section aria-label={`Relasi ${title}`} className={styles.group}>
        <h3 className={styles.groupTitle}>{title}</h3>
        <ul className={styles.list}>
          {items.map((relation) => (
            <li key={relation.id} className={styles.row}>
              <div className={styles.rowMain}>
                <span className={styles.rowTitle}>{relationTitle(relation, tableNames)}</span>
                <span className={styles.rowMeta}>
                  {formatCardinality(relation.cardinality)} · overlap {formatOverlap(relation.overlap_pct)}
                </span>
              </div>
              <div className={styles.rowActions}>
                {relation.status === "candidate" && (
                  <>
                    <button
                      type="button"
                      className={styles.buttonPrimary}
                      disabled={busyId === relation.id}
                      onClick={() => void run(relation, "confirm")}
                    >
                      Konfirmasi
                    </button>
                    <button
                      type="button"
                      className={styles.button}
                      disabled={busyId === relation.id}
                      onClick={() => void run(relation, "reject")}
                    >
                      Tolak
                    </button>
                  </>
                )}
                {relation.status !== "candidate" && (
                  <button
                    type="button"
                    className={styles.buttonDanger}
                    disabled={busyId === relation.id}
                    onClick={() => void run(relation, "remove")}
                    aria-label={`Hapus relasi ${relationTitle(relation, tableNames)}`}
                  >
                    Hapus
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <h2 className={styles.title}>Relasi</h2>
        <button
          type="button"
          className={styles.button}
          disabled={detecting}
          onClick={() => void detect()}
        >
          {detecting ? "Mendeteksi…" : "Deteksi ulang"}
        </button>
      </div>

      {list.length === 0 && (
        <p className={styles.muted}>Belum ada relasi — coba deteksi ulang setelah mengunggah beberapa dataset.</p>
      )}

      {renderGroup("Kandidat", groups.candidate)}
      {renderGroup("Terkonfirmasi", groups.confirmed)}
      {renderGroup("Ditolak", groups.rejected)}

      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}
    </div>
  );
}
