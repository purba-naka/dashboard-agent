"use client";

import { useState } from "react";
import { formatScalar } from "@/lib/filters";
import type { FilterSet, InsightItem, QueryDetail } from "@/lib/types";
import {
  computedWithDifferentFilters,
  defaultInsightClient,
  errorMessage,
  insightTypeLabel,
  isStale,
  type InsightClient,
} from "./client";
import styles from "./insights.module.css";

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
    <article className={styles.card} aria-label={`Insight: ${insight.title}`}>
      <div className={styles.header}>
        <h3 className={styles.title}>{insight.title}</h3>
        <span className={styles.type}>{insightTypeLabel(insight.insight_type)}</span>
      </div>

      <p className={styles.text}>{insight.text}</p>

      {(differentFilters || stale) && (
        <div className={styles.badges}>
          {differentFilters && (
            <span className={`${styles.badge} ${styles.badgeWarn}`}>
              Dihitung dengan filter berbeda
            </span>
          )}
          {stale && (
            <span className={`${styles.badge} ${styles.badgeStale}`}>
              Data sumber sudah berubah
            </span>
          )}
        </div>
      )}

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.button}
          onClick={openDetail}
          disabled={busy !== null}
        >
          Detail
        </button>
        <button
          type="button"
          className={styles.button}
          onClick={onRefresh}
          disabled={busy !== null}
        >
          {busy === "refresh" ? "Menyegarkan…" : "Segarkan"}
        </button>
      </div>

      {error && (
        <p role="alert" className={styles.error}>
          {error}
        </p>
      )}

      {detailOpen && (
        <InsightDetail
          insight={insight}
          detail={detail}
          loading={busy === "detail"}
          onClose={() => setDetailOpen(false)}
        />
      )}
    </article>
  );
}

function InsightDetail({
  insight,
  detail,
  loading,
  onClose,
}: {
  insight: InsightItem;
  detail: QueryDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  return (
    <div
      className={styles.backdrop}
      role="dialog"
      aria-modal="true"
      aria-label={`Detail insight: ${insight.title}`}
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h3 className={styles.title}>{insight.title}</h3>
          <button type="button" className={styles.button} onClick={onClose}>
            Tutup
          </button>
        </div>

        <div>
          <h4>SQL sumber</h4>
          {loading && !detail ? (
            <p aria-busy="true" className={styles.muted}>
              Memuat…
            </p>
          ) : (
            <pre className={styles.sql}>{detail?.sql ?? insight.sql}</pre>
          )}
        </div>

        <div>
          <h4>Tabel bukti</h4>
          <EvidenceTableView
            columns={(detail?.columns ?? insight.evidence.columns).map((c) => c.name)}
            rows={detail?.rows ?? insight.evidence.rows}
            rowCount={detail?.row_count ?? insight.evidence.row_count}
          />
        </div>
      </div>
    </div>
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
    return <p className={styles.muted}>Tidak ada kolom bukti.</p>;
  }
  return (
    <>
      <table className={styles.table}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{formatScalar(cell)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rowCount > rows.length && (
        <p className={styles.muted}>
          Menampilkan {rows.length} dari {rowCount.toLocaleString("id-ID")} baris.
        </p>
      )}
    </>
  );
}
