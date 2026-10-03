import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";

/** Kartu hasil agent di log chat: region berlabel, judul, isi, aksi opsional. */
export function ChatCard({
  label,
  title,
  footer,
  children,
}: {
  label: string;
  title: string;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card size="sm" role="region" aria-label={label}>
      <CardHeader>
        <CardTitle role="heading" aria-level={3}>
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">{children}</CardContent>
      {footer && <CardFooter className="flex-wrap gap-2">{footer}</CardFooter>}
    </Card>
  );
}

/** Konfirmasi/Tolak untuk kandidat, atau status bila sudah diputuskan. */
export function DecisionActions({
  status,
  label,
  disabled,
  onDecide,
}: {
  status: string;
  label: string;
  disabled: boolean;
  onDecide: (confirm: boolean) => void;
}) {
  if (status !== "candidate")
    return <span className="text-muted-foreground">{status === "confirmed" ? "Dikonfirmasi" : "Ditolak"}</span>;
  return (
    <span className="flex gap-1.5">
      <Button variant="outline" size="sm" disabled={disabled} aria-label={`Konfirmasi ${label}`} onClick={() => onDecide(true)}>
        Konfirmasi
      </Button>
      <Button variant="ghost" size="sm" disabled={disabled} aria-label={`Tolak ${label}`} onClick={() => onDecide(false)}>
        Tolak
      </Button>
    </span>
  );
}

/** Baris kandidat: teks kiri, aksi kanan, membungkus di layar sempit. */
export const CANDIDATE_ROW = "flex flex-wrap items-center justify-between gap-2";

/** Subjudul bagian di dalam kartu. */
export function ChatCardSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
      {children}
    </div>
  );
}
