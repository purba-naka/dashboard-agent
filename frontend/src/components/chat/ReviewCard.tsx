"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ReviewFinding } from "@/lib/types";
import { ChatCard } from "./ChatCard";

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
    <ChatCard label="Review desain" title="Review desain BI">
      {findings.length === 0 ? (
        <p className="text-muted-foreground">Tidak ada temuan. Dashboard sudah mengikuti prinsip desain dasar.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {findings.map((f) => (
            <li key={`${f.code}:${f.item_ids.join(",")}`} className="flex items-start justify-between gap-2">
              <div className="flex flex-col gap-1">
                <Badge variant={f.severity === "warning" ? "destructive" : "secondary"}>
                  {f.severity === "warning" ? "Perlu diperbaiki" : "Saran"}
                </Badge>
                <p>{f.message}</p>
                <p className="text-muted-foreground">{f.suggestion}</p>
              </div>
              <Button
                variant="outline"
                size="sm"
                disabled={disabled}
                aria-label={`Terapkan saran ${f.code}`}
                onClick={() => onApply(applyMessage(f))}
              >
                Terapkan saran
              </Button>
            </li>
          ))}
        </ul>
      )}
    </ChatCard>
  );
}
