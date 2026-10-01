"use client";

import type { ReviewFinding } from "@/lib/types";
import styles from "./chat.module.css";

export interface ReviewCardProps {
  findings: readonly ReviewFinding[];
  disabled: boolean;
  /** "Terapkan saran" → kirim saran sebagai pesan chat pengguna (Req 39.5). */
  onApply: (message: string) => void;
}

export function applyMessage(finding: ReviewFinding): string {
  const items = finding.item_ids.length ? ` (item: ${finding.item_ids.join(", ")})` : "";
  return `Terapkan saran review ${finding.code}${items}: ${finding.suggestion}`;
}

/** Temuan review desain BI; tidak ada yang diterapkan otomatis (Req 39.6). */
export function ReviewCard({ findings, disabled, onApply }: ReviewCardProps) {
  return (
    <section className={styles.card} aria-label="Review desain">
      <h3 className={styles.cardTitle}>Review desain BI</h3>
      {findings.length === 0 ? (
        <p className={styles.muted}>Tidak ada temuan. Dashboard sudah mengikuti prinsip desain dasar.</p>
      ) : (
        <ul className={styles.plain}>
          {findings.map((f) => (
            <li key={`${f.code}:${f.item_ids.join(",")}`} className={styles.finding}>
              <span>
                <span className={`${styles.badge} ${f.severity === "warning" ? styles.badgeWarn : ""}`}>
                  {f.severity === "warning" ? "Perlu diperbaiki" : "Saran"}
                </span>{" "}
                {f.message}
                <br />
                <span className={styles.muted}>{f.suggestion}</span>
              </span>
              <button
                type="button"
                disabled={disabled}
                aria-label={`Terapkan saran ${f.code}`}
                onClick={() => onApply(applyMessage(f))}
              >
                Terapkan saran
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
