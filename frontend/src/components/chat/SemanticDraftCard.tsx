"use client";

import { useState } from "react";
import type { SemanticDraftCardData } from "@/lib/types";
import styles from "./chat.module.css";

export interface SemanticDraftCardProps {
  data: SemanticDraftCardData;
  decided: Readonly<Record<string, "confirmed" | "rejected">>;
  onDecide: (entryId: string, confirm: boolean) => Promise<void>;
  onConfirmAll: () => Promise<void>;
  /** Kirim koreksi bebas sebagai pesan chat. */
  onCorrect: (message: string) => void;
  disabled: boolean;
}

/** Kartu "Pemahaman data": domain, metrik usulan, kolom penting, asumsi (Req 32.9, 32.10). */
export function SemanticDraftCard({
  data,
  decided,
  onDecide,
  onConfirmAll,
  onCorrect,
  disabled,
}: SemanticDraftCardProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [correction, setCorrection] = useState("");

  const statusOf = (id: string, status: string): string =>
    (decided[id] as string | undefined) ?? status;
  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  };

  const entryActions = (id: string, status: string, label: string) =>
    statusOf(id, status) === "candidate" ? (
      <span className={styles.actions}>
        <button
          type="button"
          disabled={disabled || busy !== null}
          aria-label={`Konfirmasi ${label}`}
          onClick={() => run(id, () => onDecide(id, true))}
        >
          Konfirmasi
        </button>
        <button
          type="button"
          disabled={disabled || busy !== null}
          aria-label={`Tolak ${label}`}
          onClick={() => run(id, () => onDecide(id, false))}
        >
          Tolak
        </button>
      </span>
    ) : (
      <span className={styles.muted}>{statusOf(id, status) === "confirmed" ? "Dikonfirmasi" : "Ditolak"}</span>
    );

  const pending =
    data.metrics.some((m) => statusOf(m.id, m.status) === "candidate") ||
    data.columns_highlight.some((c) => statusOf(c.id, c.status) === "candidate");

  return (
    <section className={styles.card} aria-label="Pemahaman data">
      <h3 className={styles.cardTitle}>Pemahaman data</h3>
      {data.domain && <p>Domain dugaan: <strong>{data.domain}</strong></p>}
      {data.summary && <p>{data.summary}</p>}

      {data.metrics.length > 0 && (
        <>
          <h4 className={styles.cardSubtitle}>Metrik usulan</h4>
          <ul className={styles.plain}>
            {data.metrics.map((m) => (
              <li key={m.id} className={styles.relation}>
                <span>
                  <strong>{m.label || m.name}</strong> <code>{m.expr}</code>
                </span>
                {entryActions(m.id, m.status, `metrik ${m.label || m.name}`)}
              </li>
            ))}
          </ul>
        </>
      )}

      {data.columns_highlight.length > 0 && (
        <>
          <h4 className={styles.cardSubtitle}>Kolom penting</h4>
          <ul className={styles.plain}>
            {data.columns_highlight.map((c) => (
              <li key={c.id} className={styles.relation}>
                <span>
                  <strong>{c.label || c.column}</strong> ({c.table}.{c.column}) · {c.description}
                </span>
                {entryActions(c.id, c.status, `kolom ${c.label || c.column}`)}
              </li>
            ))}
          </ul>
        </>
      )}

      {data.assumptions.length > 0 && (
        <>
          <h4 className={styles.cardSubtitle}>Asumsi</h4>
          <ul>
            {data.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </>
      )}

      <span className={styles.actions}>
        <button
          type="button"
          disabled={disabled || busy !== null || !pending}
          onClick={() => run("all", onConfirmAll)}
        >
          Konfirmasi semua
        </button>
      </span>
      <form
        className={styles.inlineForm}
        onSubmit={(e) => {
          e.preventDefault();
          if (!correction.trim()) return;
          onCorrect(`Koreksi pemahaman data: ${correction.trim()}`);
          setCorrection("");
        }}
      >
        <input
          aria-label="Koreksi pemahaman data"
          placeholder="Koreksi, mis. 'amount dalam USD'"
          value={correction}
          onChange={(e) => setCorrection(e.target.value)}
          disabled={disabled}
        />
        <button type="submit" disabled={disabled || !correction.trim()}>
          Kirim koreksi
        </button>
      </form>
    </section>
  );
}
