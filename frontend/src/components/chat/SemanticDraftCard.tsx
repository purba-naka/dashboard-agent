"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { SemanticDraftCardData } from "@/lib/types";
import { CANDIDATE_ROW, ChatCard, ChatCardSection, DecisionActions } from "./ChatCard";

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

  const entryActions = (id: string, status: string, label: string) => (
    <DecisionActions
      status={statusOf(id, status)}
      label={label}
      disabled={disabled || busy !== null}
      onDecide={(confirm) => run(id, () => onDecide(id, confirm))}
    />
  );

  const pending =
    data.metrics.some((m) => statusOf(m.id, m.status) === "candidate") ||
    data.columns_highlight.some((c) => statusOf(c.id, c.status) === "candidate");

  return (
    <ChatCard
      label="Pemahaman data"
      title="Pemahaman data"
      footer={
        <>
          <Button size="sm" disabled={disabled || busy !== null || !pending} onClick={() => run("all", onConfirmAll)}>
            Konfirmasi semua
          </Button>
          <form
            className="flex min-w-0 flex-1 gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (!correction.trim()) return;
              onCorrect(`Koreksi pemahaman data: ${correction.trim()}`);
              setCorrection("");
            }}
          >
            <Input
              className="h-7 min-w-0 flex-1"
              aria-label="Koreksi pemahaman data"
              placeholder="Koreksi, mis. 'amount dalam USD'"
              value={correction}
              onChange={(e) => setCorrection(e.target.value)}
              disabled={disabled}
            />
            <Button type="submit" variant="outline" size="sm" disabled={disabled || !correction.trim()}>
              Kirim koreksi
            </Button>
          </form>
        </>
      }
    >
      {data.domain && (
        <p>
          Domain dugaan: <strong>{data.domain}</strong>
        </p>
      )}
      {data.summary && <p>{data.summary}</p>}

      {data.metrics.length > 0 && (
        <ChatCardSection title="Metrik usulan">
          <ul className="flex flex-col gap-2">
            {data.metrics.map((m) => (
              <li key={m.id} className={CANDIDATE_ROW}>
                <span className="min-w-0">
                  <strong>{m.label || m.name}</strong>{" "}
                  <code className="rounded bg-muted px-1 text-[0.85em]">{m.expr}</code>
                </span>
                {entryActions(m.id, m.status, `metrik ${m.label || m.name}`)}
              </li>
            ))}
          </ul>
        </ChatCardSection>
      )}

      {data.columns_highlight.length > 0 && (
        <ChatCardSection title="Kolom penting">
          <ul className="flex flex-col gap-2">
            {data.columns_highlight.map((c) => (
              <li key={c.id} className={CANDIDATE_ROW}>
                <span className="min-w-0">
                  <strong>{c.label || c.column}</strong>{" "}
                  <span className="text-muted-foreground">
                    ({c.table}.{c.column}) · {c.description}
                  </span>
                </span>
                {entryActions(c.id, c.status, `kolom ${c.label || c.column}`)}
              </li>
            ))}
          </ul>
        </ChatCardSection>
      )}

      {data.assumptions.length > 0 && (
        <ChatCardSection title="Asumsi">
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {data.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </ChatCardSection>
      )}
    </ChatCard>
  );
}
