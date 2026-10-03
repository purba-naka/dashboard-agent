"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type {
  BlueprintSlot,
  BlueprintSlotStatus,
  DashboardBlueprint,
  VisualType,
} from "@/lib/types";
import { ChatCard } from "./ChatCard";
import { Markdown } from "./Markdown";

const VISUALS: VisualType[] = ["kpi", "line", "bar", "pie", "scatter", "heatmap", "waterfall", "insight"];

const VISUAL_LABELS: Record<VisualType, string> = {
  kpi: "KPI",
  line: "Garis",
  bar: "Batang",
  pie: "Pie",
  scatter: "Scatter",
  heatmap: "Heatmap",
  waterfall: "Waterfall",
  insight: "Insight",
};

const STATUS_LABELS: Record<BlueprintSlotStatus, string> = {
  pending: "Menunggu",
  building: "Dibangun",
  done: "Selesai",
  failed: "Gagal",
  skipped: "Dilewati",
};

const STATUS_VARIANT: Record<BlueprintSlotStatus, "secondary" | "outline" | "destructive"> = {
  pending: "outline",
  building: "outline",
  done: "secondary",
  failed: "destructive",
  skipped: "outline",
};

// ponytail: native <select>, bukan Radix Select, agar tetap sempit di baris slot dan
// teruji via selectOptions. data-slot melepas gaya kontrol legacy.
const NATIVE_SELECT =
  "h-7 rounded-md border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50";

export interface BlueprintCardProps {
  summary: string;
  blueprint: DashboardBlueprint;
  approved: boolean;
  selectedSlotIds?: string[];
  progress: Record<string, BlueprintSlotStatus>;
  disabled: boolean;
  /** Setujui slot terpilih (semua bila `selected` sama dengan seluruh slot). */
  onApprove: (selected: string[]) => void;
  /** Kirim permintaan revisi rancangan sebagai pesan chat. */
  onRevise: (message: string) => void;
}

interface Edit {
  purpose: string;
  visual: VisualType;
}

/** Ringkasan perubahan slot untuk pesan revisi. */
export function revisionMessage(
  slots: readonly BlueprintSlot[],
  edits: Readonly<Record<string, Edit>>,
  selected: ReadonlySet<string>,
): string {
  const lines: string[] = [];
  for (const slot of slots) {
    const edit = edits[slot.slot_id];
    if (!selected.has(slot.slot_id)) lines.push(`- Hapus slot ${slot.slot_id} (${slot.purpose}).`);
    else if (edit && (edit.purpose !== slot.purpose || edit.visual !== slot.visual)) {
      const parts: string[] = [];
      if (edit.purpose !== slot.purpose) parts.push(`tujuan menjadi "${edit.purpose}"`);
      if (edit.visual !== slot.visual) parts.push(`visual menjadi ${edit.visual}`);
      lines.push(`- Slot ${slot.slot_id}: ${parts.join(", ")}.`);
    }
  }
  return lines.length ? `Revisi rancangan dashboard:\n${lines.join("\n")}` : "Revisi rancangan dashboard.";
}

/** Kartu Dashboard_Blueprint: wireframe grid 12 kolom + persetujuan per slot (Req 37.5). */
export function BlueprintCard({
  summary,
  blueprint,
  approved,
  selectedSlotIds,
  progress,
  disabled,
  onApprove,
  onRevise,
}: BlueprintCardProps) {
  const slots = blueprint.slots;
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(selectedSlotIds ?? slots.map((s) => s.slot_id)),
  );
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const rows = useMemo(
    () => Math.max(1, ...slots.map((s) => (s.layout ? s.layout.y + s.layout.h : 0))),
    [slots],
  );
  const edited = slots.some((s) => {
    const e = edits[s.slot_id];
    return e && (e.purpose !== s.purpose || e.visual !== s.visual);
  });
  const shown = approved ? new Set(selectedSlotIds ?? slots.map((s) => s.slot_id)) : selected;

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const edit = (slot: BlueprintSlot, patch: Partial<Edit>) =>
    setEdits((prev) => {
      const base: Edit = prev[slot.slot_id] ?? { purpose: slot.purpose, visual: slot.visual };
      return { ...prev, [slot.slot_id]: { ...base, ...patch } };
    });

  return (
    <ChatCard
      label="Rancangan dashboard"
      title="Rancangan dashboard"
      footer={
        approved ? (
          <span className="text-muted-foreground">Disetujui ({shown.size} slot)</span>
        ) : (
          <>
            <Button
              size="sm"
              disabled={disabled || selected.size === 0 || edited}
              onClick={() => onApprove(slots.filter((s) => selected.has(s.slot_id)).map((s) => s.slot_id))}
            >
              Setujui terpilih ({selected.size})
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={disabled || edited}
              onClick={() => onApprove(slots.map((s) => s.slot_id))}
            >
              Setujui semua
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => onRevise(revisionMessage(slots, edits, selected))}
            >
              Revisi
            </Button>
          </>
        )
      }
    >
      <Markdown text={summary} />
      {blueprint.brief.purpose && (
        <p className="text-muted-foreground">
          Tujuan: {blueprint.brief.purpose}
          {blueprint.brief.audience ? ` · Audiens: ${blueprint.brief.audience}` : ""}
        </p>
      )}
      {blueprint.brief.assumptions.length > 0 && (
        <p className="text-muted-foreground">Asumsi: {blueprint.brief.assumptions.join("; ")}</p>
      )}

      <div
        className="grid grid-cols-12 gap-[3px] rounded-md border border-dashed border-input bg-background p-1.5"
        role="img"
        aria-label={`Wireframe ${slots.length} slot`}
        style={{ gridTemplateRows: `repeat(${rows}, 10px)` }}
      >
        {slots.map((s) =>
          s.layout ? (
            <div
              key={s.slot_id}
              className={cn(
                "flex items-center justify-center overflow-hidden rounded-[3px] bg-secondary text-[0.6875rem] font-semibold whitespace-nowrap text-secondary-foreground ring-1 ring-foreground/10 transition-opacity motion-reduce:transition-none",
                s.section === "kpi_row" && "bg-foreground text-background",
                !shown.has(s.slot_id) && "opacity-25",
              )}
              data-section={s.section}
              style={{
                gridColumn: `${s.layout.x + 1} / span ${s.layout.w}`,
                gridRow: `${s.layout.y + 1} / span ${s.layout.h}`,
              }}
              title={s.purpose}
            >
              {VISUAL_LABELS[(edits[s.slot_id]?.visual ?? s.visual) as VisualType]}
            </div>
          ) : null,
        )}
      </div>

      <ul className="flex flex-col gap-2">
        {slots.map((s) => {
          const status = progress[s.slot_id];
          const e = edits[s.slot_id];
          return (
            <li key={s.slot_id} className="flex flex-wrap items-center gap-1.5">
              {!approved && (
                <input
                  type="checkbox"
                  data-slot="checkbox"
                  className="size-4 accent-foreground"
                  checked={selected.has(s.slot_id)}
                  onChange={() => toggle(s.slot_id)}
                  aria-label={`Pilih slot ${s.slot_id}`}
                  disabled={disabled}
                />
              )}
              {approved ? (
                <span className="min-w-0 flex-1">
                  <strong>{VISUAL_LABELS[s.visual]}</strong> · {s.purpose}
                </span>
              ) : (
                <>
                  <select
                    data-slot="native-select"
                    className={NATIVE_SELECT}
                    aria-label={`Visual slot ${s.slot_id}`}
                    value={e?.visual ?? s.visual}
                    onChange={(ev) => edit(s, { visual: ev.target.value as VisualType })}
                    disabled={disabled}
                  >
                    {VISUALS.map((v) => (
                      <option key={v} value={v}>
                        {VISUAL_LABELS[v]}
                      </option>
                    ))}
                  </select>
                  <Input
                    className="h-7 min-w-40 flex-1"
                    aria-label={`Tujuan slot ${s.slot_id}`}
                    value={e?.purpose ?? s.purpose}
                    onChange={(ev) => edit(s, { purpose: ev.target.value })}
                    disabled={disabled}
                  />
                </>
              )}
              {status && (
                <Badge variant={STATUS_VARIANT[status]} className={cn(status === "building" && "border-dashed")}>
                  {STATUS_LABELS[status]}
                </Badge>
              )}
            </li>
          );
        })}
      </ul>
    </ChatCard>
  );
}
